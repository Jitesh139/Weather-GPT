"""Voice layer via Google (Gemini), used when VOICE_PROVIDER=google - the
Bhashini alternative requested once Bhashini credentials turned out not to
be available. Both directions authenticate with the same GEMINI_API_KEY
already used by the LLM pipeline - no separate signup needed.

ASR is a single generate_content call with the audio inline
(GOOGLE_ASR_MODEL). It returns what the user said in their own language, so
the answer can come back in it. It replaced the Live translate model, which
had to be fed audio at real-time pace and took ~20s for a 3s clip.

TTS uses Gemini's text-to-speech model and speaks whatever language the
answer text is written in.
"""
import audioop
import base64
import io
import logging
import wave
from typing import Optional

from config import settings
from services.llm_client import genai_sdk_client

logger = logging.getLogger(__name__)

TARGET_SAMPLE_RATE = 16000
_TTS_VOICE = "Kore"
_TTS_OUTPUT_SAMPLE_RATE = 24000


class GoogleVoiceConfigError(Exception):
    """Raised when GEMINI_API_KEY is not set."""


class GoogleVoiceError(Exception):
    """Non-retryable failure talking to Gemini's voice models, or a
    malformed/empty result."""


def _require_client():
    if not settings.gemini_api_key:
        raise GoogleVoiceConfigError("GEMINI_API_KEY is not set")
    return genai_sdk_client(settings.gemini_api_key)


def _wav_to_pcm16_mono(audio_bytes: bytes) -> tuple[bytes, int]:
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            frame_rate = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
    except wave.Error as exc:
        raise GoogleVoiceError(f"Could not decode audio as WAV: {exc}") from exc

    if sample_width != 2:
        frames = audioop.lin2lin(frames, sample_width, 2)
    if channels > 1:
        frames = audioop.tomono(frames, 2, 0.5, 0.5)
    return frames, frame_rate


def _resample_to_target(pcm: bytes, frame_rate: int) -> bytes:
    if frame_rate == TARGET_SAMPLE_RATE:
        return pcm
    converted, _state = audioop.ratecv(pcm, 2, 1, frame_rate, TARGET_SAMPLE_RATE, None)
    return converted


_ASR_PROMPT = (
    "Transcribe this audio verbatim, in the language and script it was spoken in "
    "(Hindi in Devanagari, Hinglish in Latin script, English as English). "
    "Output only the transcript, nothing else."
)


def _pcm_to_wav(pcm16_mono: bytes, frame_rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(frame_rate)
        wf.writeframes(pcm16_mono)
    return buffer.getvalue()


def speech_to_text(audio_base64: str, sampling_rate: int = 16000) -> str:
    """Returns the transcript of the given base64-encoded WAV audio, in the
    language it was spoken in, so the answer can come back in that language."""
    from google.genai import types

    client = _require_client()

    try:
        audio_bytes = base64.b64decode(audio_base64)
    except Exception as exc:  # noqa: BLE001
        raise GoogleVoiceError(f"Could not decode base64 audio: {exc}") from exc

    pcm, frame_rate = _wav_to_pcm16_mono(audio_bytes)
    # Browsers record at 44.1/48kHz; 16kHz mono is all speech needs and
    # cuts the upload to a third.
    wav_16k = _pcm_to_wav(_resample_to_target(pcm, frame_rate), TARGET_SAMPLE_RATE)

    try:
        response = client.models.generate_content(
            model=settings.google_asr_model,
            contents=[types.Part.from_bytes(data=wav_16k, mime_type="audio/wav"), _ASR_PROMPT],
            config=types.GenerateContentConfig(thinking_config=types.ThinkingConfig(thinking_level="minimal")),
        )
        transcript = (response.text or "").strip()
    except Exception as exc:  # noqa: BLE001 - any SDK error is a real ASR failure
        raise GoogleVoiceError(f"Gemini transcription failed: {exc}") from exc

    if not transcript:
        raise GoogleVoiceError("Gemini returned an empty transcript")
    return transcript


def _extract_audio(response) -> Optional[bytes]:
    """Pulls the audio payload out of a generate_content response.

    Written defensively on purpose. The old one-liner
    (`response.candidates[0].content.parts[0].inline_data.data`) assumed
    every link in that chain was populated, and intermittently it isn't -
    a candidate can come back with no parts, or with a text part ahead of
    the audio one. When that happened the chain raised "'NoneType' object
    is not subscriptable", which told nobody anything. Scan for the audio
    instead, and let the caller report honestly when there is none.
    """
    for candidate in response.candidates or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            inline_data = getattr(part, "inline_data", None)
            data = getattr(inline_data, "data", None)
            if data:
                return data
    return None


def text_to_speech(text: str) -> str:
    """Returns base64-encoded WAV audio for the given text, spoken in
    whatever language the text is written in - the answer reaching this
    point is already in the user's own language (see module docstring)."""
    from google.genai import types

    client = _require_client()

    try:
        response = client.models.generate_content(
            model=settings.google_tts_model,
            contents=text,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config={"voice_config": {"prebuilt_voice_config": {"voice_name": _TTS_VOICE}}},
            ),
        )
        pcm_bytes: Optional[bytes] = _extract_audio(response)
    except Exception as exc:  # noqa: BLE001
        raise GoogleVoiceError(f"Gemini TTS failed: {exc}") from exc

    if not pcm_bytes:
        finish_reason = getattr((response.candidates or [None])[0], "finish_reason", None)
        raise GoogleVoiceError(f"Gemini TTS returned no audio (finish_reason={finish_reason})")

    if not pcm_bytes:
        raise GoogleVoiceError("Gemini TTS returned no audio data")

    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_TTS_OUTPUT_SAMPLE_RATE)
        wf.writeframes(pcm_bytes)
    return base64.b64encode(wav_buffer.getvalue()).decode("ascii")
