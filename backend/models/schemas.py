"""Pydantic models shared across the API boundary and internal pipeline."""
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    text: str
    input_mode: Literal["voice", "text"]


class GeocodeResult(BaseModel):
    location_name: str
    latitude: float
    longitude: float


class WeatherData(BaseModel):
    location: str
    latitude: float
    longitude: float
    current: dict[str, Any]
    hourly: Optional[dict[str, Any]] = None
    daily: Optional[dict[str, Any]] = None
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # Google Weather API cross-check, set only when GOOGLE_WEATHER_API_KEY
    # is configured and the call succeeds - Open-Meteo (`current` above)
    # remains authoritative and is always present on its own.
    google: Optional[dict[str, Any]] = None
    best_estimate: Optional[dict[str, Any]] = None


class ValidationResult(BaseModel):
    ok: bool
    reason: Optional[str] = None


class DraftAnswer(BaseModel):
    text: str
    raw_tool_data: list[dict[str, Any]]
    tool_call_count: int


class VerificationResult(BaseModel):
    passed: bool
    mismatches: list[str] = Field(default_factory=list)
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class VoiceConfigResponse(BaseModel):
    """Tells the frontend which voice provider is active server-side, so
    it can pick the right client-side flow. Never carries credentials -
    those stay backend-only.
    """

    provider: Literal["web_speech", "bhashini", "google"]
    language: str


class ASRRequest(BaseModel):
    audio_base64: str
    language: Optional[str] = None
    audio_format: str = "wav"
    sampling_rate: int = 16000


class ASRResponse(BaseModel):
    transcript: Optional[str] = None
    error: Optional[str] = None


class TTSRequest(BaseModel):
    text: str
    language: Optional[str] = None
    gender: Optional[Literal["male", "female"]] = None


class TTSResponse(BaseModel):
    audio_base64: Optional[str] = None
    audio_format: str = "wav"
    error: Optional[str] = None


class FinalAnswer(BaseModel):
    """Response body for POST /query - matches spec Section 9 exactly.

    Success: answer is set, error is None.
    Failure: error is set, answer is None. Never both.
    """

    answer: Optional[str] = None
    path: Literal["fast", "slow"]
    verified: bool
    source_data: Optional[dict[str, Any]] = None
    latency_ms: int
    error: Optional[str] = None
