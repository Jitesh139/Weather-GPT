"""Environment/configuration loading. All API keys and tunables are read
from the environment (via .env in local/dev, or real env vars in prod) -
nothing is ever hardcoded here.
"""
import logging
from pathlib import Path
from typing import Literal, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# The .env lives at the project root, next to docker-compose.yml - one
# level above this backend/ package. Resolving it absolutely means the
# app picks up the same config whether it is started from the repo root,
# from backend/, or from inside the container. A plain ".env" is still
# honoured as a fallback for a CWD-local override.
_ROOT_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(_ROOT_ENV_FILE, ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    anthropic_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None

    # Google Weather API (weather.googleapis.com) - a second, independent
    # weather data source combined with Open-Meteo (services/weather.py's
    # fetch_combined_forecast) to cross-check and blend a "best estimate".
    # Purely additive: if unset, every path silently falls back to
    # Open-Meteo alone, which remains a fully grounded, valid answer on
    # its own - this is never a hard dependency.
    google_weather_api_key: Optional[str] = None

    # Model used for voice ASR when VOICE_PROVIDER=google - one-shot audio
    # transcription. Measured ~1.7s for a short clip, against ~20s for the
    # Live translate model this replaced (which streams at real-time pace).
    google_asr_model: str = "gemini-3.5-flash-lite"
    # Model used for voice TTS when the TTS provider is google.
    google_tts_model: str = "gemini-3.1-flash-tts-preview"

    database_url: str = "postgresql://weathergpt:weathergpt@localhost:5432/weathergpt"

    cache_ttl_seconds: int = 900
    log_level: str = "info"

    # Provider/model routing for the two-stage slow path. Spec Section 14
    # explicitly leaves this unfinalized - these are swappable defaults,
    # not a locked-in architectural decision. Change via env vars only;
    # pipeline code never branches on provider name directly.
    generator_provider: Literal["claude", "gemini"] = "claude"
    verifier_provider: Literal["claude", "gemini"] = "gemini"
    generator_model: str = "claude-opus-5"
    verifier_model: str = "gemini-3.6-flash"

    # Voice layer (spec Section 7). Default "web_speech" needs zero backend
    # config - STT/TTS happen entirely in the browser. "bhashini" routes
    # voice through Bhashini's ASR/TTS pipeline instead, which requires
    # BHASHINI_USER_ID/BHASHINI_API_KEY (server-side only - the browser
    # must never hold these, so Bhashini calls happen via backend
    # endpoints, unlike Web Speech which is fully client-side).
    voice_provider: Literal["web_speech", "bhashini", "google"] = "web_speech"
    # Lets speech output use a different server-side provider from speech
    # input (e.g. Gemini ASR + Bhashini TTS). Unset = same as VOICE_PROVIDER.
    voice_tts_provider: Optional[Literal["bhashini", "google"]] = None
    bhashini_user_id: Optional[str] = None
    bhashini_api_key: Optional[str] = None
    # Default pipeline ID for the standard ASR+TTS pipeline, as commonly
    # referenced in community Bhashini integrations - NOT independently
    # confirmed against your account's available pipelines. Verify via
    # the Bhashini portal / Pipeline Search API and override if needed.
    bhashini_pipeline_id: str = "64392f96daac500b55c543cd"
    # ISO-639 language code (e.g. "hi", "ta", "en") - Bhashini's ASR/TTS
    # model catalog is Indian-language-focused; English coverage is not
    # guaranteed. Verify supported languages via the pipeline config
    # call's response before relying on this in production.
    bhashini_language: str = "hi"
    bhashini_tts_gender: Literal["male", "female"] = "female"


settings = Settings()


def warn_if_llm_keys_missing() -> None:
    """Called at app startup. The app must still boot and serve the FAST
    path with no LLM keys configured - this only logs a loud warning so
    the operator knows the SLOW path will fail (honestly, not silently)
    until real keys are added to .env.
    """
    missing = []
    if not settings.anthropic_api_key:
        missing.append("ANTHROPIC_API_KEY")
    if not settings.gemini_api_key:
        missing.append("GEMINI_API_KEY")
    if missing:
        logger.warning(
            "Missing LLM API key(s): %s. The FAST path will still work, but "
            "any SLOW-path (reasoning) query will fail with an honest error "
            "response until these are set in .env.",
            ", ".join(missing),
        )
    if settings.voice_provider == "bhashini" and not (settings.bhashini_user_id and settings.bhashini_api_key):
        logger.warning(
            "VOICE_PROVIDER=bhashini but BHASHINI_USER_ID/BHASHINI_API_KEY are not set. "
            "Voice requests will fail with an honest error response until these are set in .env."
        )
    if settings.voice_provider == "google" and not settings.gemini_api_key:
        logger.warning(
            "VOICE_PROVIDER=google but GEMINI_API_KEY is not set. Voice requests will fail "
            "with an honest error response until it is set in .env."
        )
    if not settings.google_weather_api_key:
        logger.warning(
            "GOOGLE_WEATHER_API_KEY is not set. Weather answers will use Open-Meteo only - "
            "set it to also cross-check/blend with the Google Weather API."
        )
