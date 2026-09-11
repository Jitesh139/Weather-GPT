"""FastAPI app + route definitions (spec Section 8, 9)."""
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
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
from services import bhashini_client, fast_path, google_voice_client, weather
from services.bhashini_client import BhashiniConfigError, BhashiniError, TransientBhashiniError
from services.google_voice_client import GoogleVoiceConfigError, GoogleVoiceError
from services.generator import run_generator
from services.llm_client import (
    LLMConfigError,
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

# /frontend is where docker-compose volume-mounts the frontend/ folder
# inside the backend container. FRONTEND_DIR lets a local (non-Docker)
# run point this at the real frontend/ folder instead.
_FRONTEND_DIR = Path(os.environ.get("FRONTEND_DIR", "/frontend"))


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
        if settings.voice_provider == "google":
            audio_base64 = google_voice_client.text_to_speech(request.text)
        else:
            audio_base64 = bhashini_client.text_to_speech(
                request.text,
                request.language or settings.bhashini_language,
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
_LLM_ERRORS = (LLMConfigError, LLMToolLoopExceeded, LLMUngroundedClaimError)


def _run_fast_path(text_query: str, db: Session) -> tuple[FinalAnswer, int | None]:
    """Returns (FinalAnswer, fetch_latency_ms)."""
    fetch_start = time.monotonic()
    location, parameter = fast_path.extract_location_and_param(text_query)

    if not location:
        return (
            FinalAnswer(
                answer=None,
                path="fast",
                verified=False,
                error="I couldn't identify a location in your question - please specify a city.",
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
                    error=f"I couldn't get reliable data right now ({validation.reason}).",
                    latency_ms=0,
                ),
                fetch_latency_ms,
            )

        answer_text = fast_path.build_answer(forecast, parameter)
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
                error=f"I couldn't get reliable weather data right now: {exc}",
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
                error=f"I couldn't get reliable weather data right now: {exc}",
                latency_ms=0,
            ),
            fetch_latency_ms,
        )


@app.post("/query", response_model=FinalAnswer)
def query(request: QueryRequest, db: Session = Depends(get_db)) -> FinalAnswer:
    total_start = time.monotonic()
    path, reason = classify_with_reason(request.text)
    logger.info("Routed query %r -> %s (%s)", request.text, path, reason)

    if path == "fast":
        result, fetch_ms = _run_fast_path(request.text, db)
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
        draft = run_generator(request.text, generator_client, db)
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
                request.text, generator_client, db, mismatch_hint="; ".join(verification.mismatches)
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
        fallback, fetch_ms = _run_fast_path(request.text, db)
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
            error=f"I couldn't get a reliable, verified answer right now: {exc}",
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
            error=f"I couldn't get reliable weather data right now: {exc}",
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
            error=f"I couldn't get a reliable, verified answer right now: {exc}",
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


# Mounted last, deliberately: a StaticFiles mount at "/" matches every
# path by prefix, and Starlette resolves routes in registration order -
# mounting this before the API routes above would shadow /health,
# /query, and /voice/* entirely whenever the frontend directory exists
# (i.e. inside the actual Docker deployment, where this bug would have
# gone unnoticed since local sandbox testing has no /frontend directory).
if _FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")
