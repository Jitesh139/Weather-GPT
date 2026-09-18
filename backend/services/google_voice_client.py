"""Voice layer via Google (Gemini), used when VOICE_PROVIDER=google - the
Bhashini alternative requested once Bhashini credentials turned out not to
be available. Both directions authenticate with the same GEMINI_API_KEY
already used by the LLM pipeline - no separate signup needed.

ASR uses gemini-3.5-live-translate-preview (Gemini's Live API real-time
audio-to-audio translation model). It returns both an input transcription
(what the user actually said, in their own language) and an English
translation; this module returns the INPUT transcription, so the user's
language survives all the way to /query and the answer can come back in
it. The English translation is kept only as a fallback for when the input
transcription doesn't arrive.

That choice trades away one thing deliberately: a non-English transcript
matches none of the router's English FAST patterns, so non-English
questions always take the SLOW path. That is the right trade - the SLOW
path is where the LLM is, and the LLM is what can answer in the user's
language at all. The FAST path only has templates for the languages in
services/language.py.

TTS uses a separate, plain (non-live) Gemini text-to-speech model, since
the translate model above is audio-in/audio-out only and can't synthesize
speech from arbitrary text. It speaks whatever language the answer text is
written in, which is now the user's own.

Caveat: this integration is grounded in Google's own Live API and
speech-generation documentation (ai.google.dev/gemini-api/docs/live-api,
.../speech-generation), fetched live during this build, but - like the
original Bhashini integration - has not been exercised against a live
account with real audio in this environment beyond the one-shot testing
covered by this project's test suite.
"""
import asyncio
import audioop
import base64
import io
import logging
import wave
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)

TARGET_SAMPLE_RATE = 16000
_CHUNK_MS = 100
_TRAILING_SILENCE_CHUNKS = 10  # 1s of silence so server-side VAD detects end-of-speech
_RECEIVE_TIMEOUT_SECONDS = 30.0
_MAX_RECEIVE_MESSAGES = 60  # safety cap - turn_complete is not reliably sent by this preview model
_TTS_VOICE = "Kore"
_TTS_OUTPUT_SAMPLE_RATE = 24000


class GoogleVoiceConfigError(Exception):
    """Raised when GEMINI_API_KEY is not set."""


class GoogleVoiceError(Exception):
    """Non-retryable failure talking to Gemini's voice models, or a
    malformed/empty result."""


def _require_client():
    from google import genai

    if not settings.gemini_api_key:
        raise GoogleVoiceConfigError("GEMINI_API_KEY is not set")
    return genai.Client(api_key=settings.gemini_api_key)


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


async def _transcribe_async(client, pcm16_16k: bytes) -> str:
    """Live-tested finding: this model does not reliably send turn_complete
    for a pre-recorded (not truly live-mic) clip, and sending the whole
    clip in one instantaneous burst causes the server to only partially
    transcribe it. Two adjustments, confirmed against the real API, fix
    this: (1) pace the send to real-time (matching each chunk's actual
    duration) with trailing silence so server-side voice-activity
    detection can find the end of speech, and (2) don't gate completion
    on turn_complete - cap the receive loop by message count instead, run
    concurrently with the sender via asyncio.gather.
    """
    from google.genai import types

    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        translation_config=types.TranslationConfig(target_language_code="en", echo_target_language=True),
    )

    chunk_bytes = int(TARGET_SAMPLE_RATE * 2 * (_CHUNK_MS / 1000))
    chunk_seconds = _CHUNK_MS / 1000
    output_parts: list[str] = []
    input_parts: list[str] = []

    async with client.aio.live.connect(model=settings.google_translate_model, config=config) as session:

        async def sender() -> None:
            for i in range(0, len(pcm16_16k), chunk_bytes):
                chunk = pcm16_16k[i : i + chunk_bytes]
                await session.send_realtime_input(audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000"))
                await asyncio.sleep(chunk_seconds)
            silence = b"\x00\x00" * (TARGET_SAMPLE_RATE // 10)
            for _ in range(_TRAILING_SILENCE_CHUNKS):
                await session.send_realtime_input(audio=types.Blob(data=silence, mime_type="audio/pcm;rate=16000"))
                await asyncio.sleep(chunk_seconds)
            await session.send_realtime_input(audio_stream_end=True)

        async def receiver() -> None:
            count = 0
            async for response in session.receive():
                count += 1
                server_content = getattr(response, "server_content", None)
                if server_content is not None:
                    input_transcription = getattr(server_content, "input_transcription", None)
                    if input_transcription is not None and input_transcription.text:
                        input_parts.append(input_transcription.text)
                    output_transcription = getattr(server_content, "output_transcription", None)
                    if output_transcription is not None and output_transcription.text:
                        output_parts.append(output_transcription.text)
                    if getattr(server_content, "turn_complete", False):
                        break
                if count >= _MAX_RECEIVE_MESSAGES:
                    break

        try:
            await asyncio.wait_for(asyncio.gather(sender(), receiver()), timeout=_RECEIVE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as exc:
            raise GoogleVoiceError("Timed out waiting for Gemini transcription") from exc

    # The INPUT transcription is preferred: it is what the user actually
    # said, in the language they said it in, which is what makes a
    # multilingual answer possible at all. This used to return the English
    # translation instead, so a question asked in Hindi reached /query
    # already translated and came back answered in English - the language
    # was destroyed before any other component could see it.
    #
    # The translation is kept as the fallback for when the input
    # transcription doesn't arrive. Routing is unaffected: a non-English
    # transcript matches none of the router's English FAST patterns, so it
    # defaults to the SLOW path, where the generator handles the language.
    return "".join(input_parts).strip() or "".join(output_parts).strip()


def speech_to_text(audio_base64: str, sampling_rate: int = 16000) -> str:
    """Returns an English transcript/translation for the given base64-
    encoded WAV audio (any sample rate - resampled to 16kHz here, which is
    what the Live API requires)."""
    client = _require_client()

    try:
        audio_bytes = base64.b64decode(audio_base64)
    except Exception as exc:  # noqa: BLE001
        raise GoogleVoiceError(f"Could not decode base64 audio: {exc}") from exc

    pcm, frame_rate = _wav_to_pcm16_mono(audio_bytes)
    pcm16_16k = _resample_to_target(pcm, frame_rate)

    try:
        transcript = asyncio.run(_transcribe_async(client, pcm16_16k))
    except GoogleVoiceError:
        raise
    except Exception as exc:  # noqa: BLE001 - any SDK/session error is a real ASR failure
        raise GoogleVoiceError(f"Gemini live transcription failed: {exc}") from exc

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
