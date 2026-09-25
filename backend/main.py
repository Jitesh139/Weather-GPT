"""FastAPI app + route definitions (spec Section 8, 9)."""
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from config import settings, warn_if_llm_keys_missing
from db.session import get_db
from logging_config import (
    configure_logging,
    create_pending_query_log,
    log_query,
    log_verification,
    update_query_log,
)
from models.schemas import ASRRequest, ASRResponse, FinalAnswer, QueryRequest, TTSRequest, TTSResponse, VoiceConfigResponse
from router.query_classifier import classify_with_reason
from services import (
    bhashini_client,
    fast_path,
    google_voice_client,
    language as lang,
    research,
    weather,
)
from services.bhashini_client import BhashiniConfigError, BhashiniError, TransientBhashiniError
from services.google_voice_client import GoogleVoiceConfigError, GoogleVoiceError
from services.generator import run_generator
from services.regional_narrative import build_regional_narrative
from services.llm_client import (
    LLMConfigError,
    LLMResponseTruncated,
    LLMToolLoopExceeded,
    LLMUngroundedClaimError,
    get_generator_client,
    get_verifier_client,
)
from services.verifier import run_verifier
from services.weather import TransientWeatherError, WeatherServiceError

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    warn_if_llm_keys_missing()
    yield


app = FastAPI(title="WeatherGPT", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# The frontend is a Vite/React app (../frontend) whose production build
# lands in ../frontend/dist. /frontend is where docker-compose
# volume-mounts that dist folder inside the backend container; the local
# (non-Docker) fallback points straight at it on disk, so `uvicorn main:app`
# serves the built UI on the same origin as the API with no extra config.
# FRONTEND_DIR overrides both.
_BACKEND_DIR = Path(__file__).resolve().parent
_FRONTEND_CANDIDATES = [Path("/frontend"), _BACKEND_DIR.parent / "frontend" / "dist"]


def _resolve_frontend_dir() -> Path:
    override = os.environ.get("FRONTEND_DIR")
    if override:
        return Path(override)
    for candidate in _FRONTEND_CANDIDATES:
        if candidate.is_dir():
            return candidate
    return _FRONTEND_CANDIDATES[0]


_FRONTEND_DIR = _resolve_frontend_dir()


@app.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/voice/config", response_model=VoiceConfigResponse)
def voice_config() -> VoiceConfigResponse:
    """Tells the frontend which voice provider is active, so it can pick
    the right client-side flow. No credentials are ever exposed here.
    """
    return VoiceConfigResponse(provider=settings.voice_provider, language=settings.bhashini_language)


_BHASHINI_ERRORS = (BhashiniConfigError, BhashiniError, TransientBhashiniError)
_GOOGLE_VOICE_ERRORS = (GoogleVoiceConfigError, GoogleVoiceError)


@app.post("/voice/asr", response_model=ASRResponse)
def voice_asr(request: ASRRequest) -> ASRResponse:
    """Speech-to-text via Bhashini or Google (Gemini's live translate
    model), chosen by VOICE_PROVIDER. Web Speech mode does STT entirely in
    the browser and never calls this endpoint.
    """
    try:
        if settings.voice_provider == "google":
            transcript = google_voice_client.speech_to_text(request.audio_base64, request.sampling_rate)
        else:
            transcript = bhashini_client.speech_to_text(
                request.audio_base64,
                request.language or settings.bhashini_language,
                request.audio_format,
                request.sampling_rate,
            )
        return ASRResponse(transcript=transcript)
    except _BHASHINI_ERRORS as exc:
        logger.warning("Bhashini ASR failed: %s", exc)
        return ASRResponse(error=f"Speech recognition failed: {exc}")
    except _GOOGLE_VOICE_ERRORS as exc:
        logger.warning("Google ASR failed: %s", exc)
        return ASRResponse(error=f"Speech recognition failed: {exc}")
    except Exception as exc:  # noqa: BLE001 - never crash on a voice request
        logger.exception("Unexpected ASR failure")
        return ASRResponse(error=f"Speech recognition failed: {exc}")


@app.post("/voice/tts", response_model=TTSResponse)
def voice_tts(request: TTSRequest) -> TTSResponse:
    """Text-to-speech via Bhashini or Google (Gemini's native TTS model),
    chosen by VOICE_PROVIDER. Web Speech mode does TTS entirely in the
    browser and never calls this endpoint.
    """
    try:
        if (settings.voice_tts_provider or settings.voice_provider) == "google":
            audio_base64 = google_voice_client.text_to_speech(request.text)
        else:
            # Voice the answer in the language it was written in, which
            # follows the question's language, not the configured default.
            audio_base64 = bhashini_client.text_to_speech(
                request.text,
                lang.detect_language(request.text),
                request.gender or settings.bhashini_tts_gender,
            )
        return TTSResponse(audio_base64=audio_base64)
    except _BHASHINI_ERRORS as exc:
        logger.warning("Bhashini TTS failed: %s", exc)
        return TTSResponse(error=f"Speech synthesis failed: {exc}")
    except _GOOGLE_VOICE_ERRORS as exc:
        logger.warning("Google TTS failed: %s", exc)
        return TTSResponse(error=f"Speech synthesis failed: {exc}")
    except Exception as exc:  # noqa: BLE001 - never crash on a voice request
        logger.exception("Unexpected TTS failure")
        return TTSResponse(error=f"Speech synthesis failed: {exc}")


_WEATHER_FETCH_ERRORS = (WeatherServiceError, TransientWeatherError)
_LLM_ERRORS = (LLMConfigError, LLMResponseTruncated, LLMToolLoopExceeded, LLMUngroundedClaimError)


def _run_fast_path(text_query: str, db: Session, language: str = lang.DEFAULT_LANGUAGE) -> tuple[FinalAnswer, int | None]:
    """Returns (FinalAnswer, fetch_latency_ms)."""
    fetch_start = time.monotonic()
    location, parameter = fast_path.extract_location_and_param(text_query)

    if not location:
        return (
            FinalAnswer(
                answer=None,
                path="fast",
                verified=False,
                error=lang.message("no_location", language),
                latency_ms=0,
            ),
            None,
        )

    try:
        geo = weather.geocode(db, location)
        forecast = weather.fetch_combined_forecast(db, geo)
        validation = weather.validate_forecast(forecast, parameter)
        fetch_latency_ms = int((time.monotonic() - fetch_start) * 1000)

        if not validation.ok:
            return (
                FinalAnswer(
                    answer=None,
                    path="fast",
                    verified=False,
                    error=lang.message("unreliable_data", language, reason=validation.reason),
                    latency_ms=0,
                ),
                fetch_latency_ms,
            )

        answer_text = fast_path.build_answer(forecast, parameter, language)
        return (
            FinalAnswer(
                answer=answer_text,
                path="fast",
                verified=True,
                source_data=forecast.model_dump(mode="json"),
                latency_ms=0,
            ),
            fetch_latency_ms,
        )
    except _WEATHER_FETCH_ERRORS as exc:
        fetch_latency_ms = int((time.monotonic() - fetch_start) * 1000)
        logger.warning("Fast path weather fetch failed: %s", exc)
        return (
            FinalAnswer(
                answer=None,
                path="fast",
                verified=False,
                error=lang.message("fetch_failed", language, error=exc),
                latency_ms=0,
            ),
            fetch_latency_ms,
        )
    except Exception as exc:  # noqa: BLE001 - never surface a raw 500;
        # always return the spec's honest {error, answer: null} shape.
        fetch_latency_ms = int((time.monotonic() - fetch_start) * 1000)
        logger.exception("Fast path failed with an unexpected error")
        return (
            FinalAnswer(
                answer=None,
                path="fast",
                verified=False,
                error=lang.message("fetch_failed", language, error=exc),
                latency_ms=0,
            ),
            fetch_latency_ms,
        )


@app.post("/query", response_model=FinalAnswer)
def query(request: QueryRequest, db: Session = Depends(get_db)) -> FinalAnswer:
    total_start = time.monotonic()
    path, reason = classify_with_reason(request.text)
    # Answer in whatever language the question came in. Non-English
    # queries match none of the router's English FAST patterns, so they
    # land on the SLOW path by default and are handled by the generator
    # prompt rather than by the template table.
    query_language = lang.detect_language(request.text)
    logger.info("Routed query %r -> %s (%s), language=%s", request.text, path, reason, query_language)

    if path == "fast":
        result, fetch_ms = _run_fast_path(request.text, db, query_language)
        total_ms = int((time.monotonic() - total_start) * 1000)
        result.latency_ms = total_ms
        log_query(
            db,
            query_text=request.text,
            path="fast",
            latency_total_ms=total_ms,
            latency_fetch_ms=fetch_ms,
            final_answer=result.answer,
            verified=result.verified,
            error=result.error,
        )
        return result

    # SLOW path
    gen_ms: int | None = None
    verify_ms: int | None = None
    generator_client = get_generator_client()
    verifier_client = get_verifier_client()
    query_log_row = create_pending_query_log(db, query_text=request.text, path="slow")

    try:
        gen_start = time.monotonic()
        draft = run_generator(request.text, generator_client, db, language=query_language)
        gen_ms = int((time.monotonic() - gen_start) * 1000)

        verify_start = time.monotonic()
        verification = run_verifier(draft, verifier_client)
        verify_ms = int((time.monotonic() - verify_start) * 1000)
        log_verification(
            db,
            query_log_id=query_log_row.id,
            draft_answer=draft.text,
            source_data_snapshot={"tool_results": draft.raw_tool_data},
            passed=verification.passed,
            mismatch_detail="; ".join(verification.mismatches) or None,
            attempt_number=1,
        )

        if not verification.passed:
            logger.info("Verification failed (attempt 1): %s - retrying generator once", verification.mismatches)
            gen_start = time.monotonic()
            draft = run_generator(
                request.text,
                generator_client,
                db,
                mismatch_hint="; ".join(verification.mismatches),
                language=query_language,
            )
            gen_ms += int((time.monotonic() - gen_start) * 1000)

            verify_start = time.monotonic()
            verification = run_verifier(draft, verifier_client)
            verify_ms += int((time.monotonic() - verify_start) * 1000)
            log_verification(
                db,
                query_log_id=query_log_row.id,
                draft_answer=draft.text,
                source_data_snapshot={"tool_results": draft.raw_tool_data},
                passed=verification.passed,
                mismatch_detail="; ".join(verification.mismatches) or None,
                attempt_number=2,
            )

        if verification.passed:
            total_ms = int((time.monotonic() - total_start) * 1000)
            result = FinalAnswer(
                answer=draft.text,
                path="slow",
                verified=True,
                source_data={"tool_results": draft.raw_tool_data},
                latency_ms=total_ms,
            )
            update_query_log(
                db,
                query_log_row,
                latency_total_ms=total_ms,
                latency_generation_ms=gen_ms,
                latency_verification_ms=verify_ms,
                final_answer=result.answer,
                verified=True,
            )
            return result

        # Verification failed twice - fall back to the fast-path template
        # (spec 5.2: "never surface an unverified answer") using the same
        # query's location extraction, rather than the unverified draft.
        logger.warning("Verification failed twice - falling back to fast-path template")
        fallback, fetch_ms = _run_fast_path(request.text, db, query_language)
        total_ms = int((time.monotonic() - total_start) * 1000)
        fallback.path = "slow"
        fallback.latency_ms = total_ms
        update_query_log(
            db,
            query_log_row,
            latency_total_ms=total_ms,
            latency_generation_ms=gen_ms,
            latency_verification_ms=verify_ms,
            latency_fetch_ms=fetch_ms,
            final_answer=fallback.answer,
            verified=fallback.verified,
            error=fallback.error,
        )
        return fallback

    except _LLM_ERRORS as exc:
        total_ms = int((time.monotonic() - total_start) * 1000)
        logger.warning("Slow path LLM failure: %s", exc)
        result = FinalAnswer(
            answer=None,
            path="slow",
            verified=False,
            error=lang.message("verify_failed", query_language, error=exc),
            latency_ms=total_ms,
        )
        update_query_log(
            db,
            query_log_row,
            latency_total_ms=total_ms,
            latency_generation_ms=gen_ms,
            latency_verification_ms=verify_ms,
            error=result.error,
        )
        return result
    except _WEATHER_FETCH_ERRORS as exc:
        total_ms = int((time.monotonic() - total_start) * 1000)
        logger.warning("Slow path weather fetch failure: %s", exc)
        result = FinalAnswer(
            answer=None,
            path="slow",
            verified=False,
            error=lang.message("fetch_failed", query_language, error=exc),
            latency_ms=total_ms,
        )
        update_query_log(
            db,
            query_log_row,
            latency_total_ms=total_ms,
            latency_generation_ms=gen_ms,
            latency_verification_ms=verify_ms,
            error=result.error,
        )
        return result
    except Exception as exc:  # noqa: BLE001 - last resort: never let an
        # unexpected SDK/provider error (rate limits, invalid model IDs,
        # provider-side validation errors, network blips not already
        # wrapped as WeatherServiceError/LLMConfigError, etc.) surface as
        # a raw 500 with no body. Spec principle: fail loud with an
        # honest {error, answer: null} response, never crash silently.
        total_ms = int((time.monotonic() - total_start) * 1000)
        logger.exception("Slow path failed with an unexpected error")
        result = FinalAnswer(
            answer=None,
            path="slow",
            verified=False,
            error=lang.message("verify_failed", query_language, error=exc),
            latency_ms=total_ms,
        )
        update_query_log(
            db,
            query_log_row,
            latency_total_ms=total_ms,
            latency_generation_ms=gen_ms,
            latency_verification_ms=verify_ms,
            error=result.error,
        )
        return result


# ---------------------------------------------------------------------
# Researcher dashboard (spec: Researcher persona only)
#
# Features 1 and 2 are direct data endpoints with no LLM involvement -
# the statistics are arithmetic, computed in services/research.py, and
# routing them through a language model would make them slower, costlier
# and less trustworthy for no gain. Feature 3's optional narrative is the
# single place this dashboard touches the LLM, and it reuses the core
# generate-then-verify pipeline rather than a separate path.
# ---------------------------------------------------------------------


@app.get("/research/models")
def research_models() -> dict:
    """Catalog for the dashboard's model picker. Every id here was probed
    against the live API - see services/research.py."""
    return {
        "models": research.FORECAST_MODELS,
        "default": research.DEFAULT_MODELS,
        "parameters": research.COMPARE_PARAMETERS,
        "historical_parameters": [
            {"id": key, "label": spec["label"], "unit": spec["unit"]}
            for key, spec in research.HISTORICAL_PARAMETERS.items()
        ],
    }


@app.get("/research/compare-models")
def research_compare_models(
    location: str = Query(..., min_length=1),
    models: Optional[str] = Query(None, description="Comma-separated model ids"),
    forecast_days: int = Query(3, ge=1, le=14),
    db: Session = Depends(get_db),
) -> dict:
    model_list = [m.strip() for m in models.split(",") if m.strip()] if models else None
    try:
        return research.compare_models(db, location, model_list, forecast_days)
    except research.ResearchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except _WEATHER_FETCH_ERRORS as exc:
        raise HTTPException(status_code=502, detail=f"Weather provider error: {exc}") from exc


@app.get("/research/historical-trend")
def research_historical_trend(
    location: str = Query(..., min_length=1),
    parameter: str = Query("temperature"),
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    aggregation: str = Query("monthly", pattern="^(daily|monthly|yearly)$"),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return research.historical_trend(db, location, parameter, start_date, end_date, aggregation)
    except research.ResearchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except _WEATHER_FETCH_ERRORS as exc:
        raise HTTPException(status_code=502, detail=f"Weather provider error: {exc}") from exc


@app.get("/research/regional-context")
def research_regional_context(db: Session = Depends(get_db)) -> dict:
    """Conditions across the Arabian Sea / Bay of Bengal sampling points
    next to the Indian coastal cities. Data only - it asserts no link
    between the two, by design."""
    try:
        return research.regional_context(db)
    except _WEATHER_FETCH_ERRORS as exc:
        raise HTTPException(status_code=502, detail=f"Weather provider error: {exc}") from exc


@app.get("/research/map-cities")
def research_map_cities(db: Session = Depends(get_db)) -> dict:
    """Pinned cities and their current conditions for the Live Cloud View
    map. Same Open-Meteo integration as every other number in the app."""
    try:
        return research.map_cities(db)
    except _WEATHER_FETCH_ERRORS as exc:
        raise HTTPException(status_code=502, detail=f"Weather provider error: {exc}") from exc


@app.get("/research/regional-context/narrative")
def research_regional_narrative(db: Session = Depends(get_db)) -> dict:
    """Optional grounded description of the regional panel.

    Separate from /research/regional-context on purpose: the data must
    render immediately, while this costs two LLM round trips. Same
    generate-then-verify flow as /query - an unverified draft is never
    surfaced, exactly as on the conversational path.
    """
    total_start = time.monotonic()
    try:
        context = research.regional_context(db)
    except _WEATHER_FETCH_ERRORS as exc:
        raise HTTPException(status_code=502, detail=f"Weather provider error: {exc}") from exc

    try:
        draft, verification = build_regional_narrative(
            context, get_generator_client(), get_verifier_client()
        )
    except _LLM_ERRORS as exc:
        logger.warning("Regional narrative failed: %s", exc)
        return {
            "narrative": None,
            "verified": False,
            "error": f"I couldn't produce a verified description right now: {exc}",
            "latency_ms": int((time.monotonic() - total_start) * 1000),
        }
    except Exception as exc:  # noqa: BLE001 - never a raw 500 on this path either
        logger.exception("Regional narrative failed with an unexpected error")
        return {
            "narrative": None,
            "verified": False,
            "error": f"I couldn't produce a verified description right now: {exc}",
            "latency_ms": int((time.monotonic() - total_start) * 1000),
        }

    total_ms = int((time.monotonic() - total_start) * 1000)
    if not verification.passed:
        # Same rule as the conversational path: never surface an
        # unverified answer. Here there is no template to fall back to, so
        # the panel simply shows its data without a narrative.
        return {
            "narrative": None,
            "verified": False,
            "error": "The generated description did not pass verification, so it is not shown.",
            "mismatches": verification.mismatches,
            "latency_ms": total_ms,
        }

    return {
        "narrative": draft.text,
        "verified": True,
        "disclaimer": (
            "Descriptive context grounded in the fetched data for both regions. "
            "Not a cyclone forecast or a causal prediction."
        ),
        "latency_ms": total_ms,
    }


# The dashboard is a client-side route, so there is no dashboard/ file on
# disk for StaticFiles to serve. Without this, opening
# /dashboard/researcher directly - or just refreshing the page while on
# it - would 404. Registered ahead of the mount, and scoped to the one
# prefix the SPA owns rather than being a catch-all, so it can't shadow a
# real asset.
@app.get("/dashboard/{spa_path:path}", include_in_schema=False)
def dashboard_spa(spa_path: str):  # noqa: ARG001 - path is handled client-side
    index = _FRONTEND_DIR / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=404, detail="Frontend build not found - run `npm run build`.")
    return FileResponse(index)


# Mounted last, deliberately: a StaticFiles mount at "/" matches every
# path by prefix, and Starlette resolves routes in registration order -
# mounting this before the API routes above would shadow /health,
# /query, and /voice/* entirely whenever the frontend directory exists
# (i.e. inside the actual Docker deployment, where this bug would have
# gone unnoticed since local sandbox testing has no /frontend directory).
if _FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")
