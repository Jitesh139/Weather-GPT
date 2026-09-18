"""Google voice client tests (VOICE_PROVIDER=google). The Gemini SDK
client/session is faked out (mirroring how test_llm_client_gemini.py fakes
the google-genai SDK) so the suite stays offline - only the WAV encode/
decode/resample helpers and this module's own orchestration are under
test here, not Gemini's actual Live API behavior.
"""
import base64
import io
import wave

import pytest

from services import google_voice_client
from services.google_voice_client import GoogleVoiceConfigError, GoogleVoiceError


def _make_wav_bytes(frame_rate=16000, num_samples=1600, value=1000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(frame_rate)
        wf.writeframes(value.to_bytes(2, "little", signed=True) * num_samples)
    return buf.getvalue()


# ---------------------------------------------------------------------
# Config-error paths
# ---------------------------------------------------------------------


def test_speech_to_text_raises_without_api_key(monkeypatch):
    monkeypatch.setattr(google_voice_client.settings, "gemini_api_key", None)
    with pytest.raises(GoogleVoiceConfigError):
        google_voice_client.speech_to_text(base64.b64encode(_make_wav_bytes()).decode())


def test_text_to_speech_raises_without_api_key(monkeypatch):
    monkeypatch.setattr(google_voice_client.settings, "gemini_api_key", None)
    with pytest.raises(GoogleVoiceConfigError):
        google_voice_client.text_to_speech("hello")


# ---------------------------------------------------------------------
# WAV decode/resample helpers
# ---------------------------------------------------------------------


def test_wav_to_pcm16_mono_passthrough_for_16bit_mono():
    wav_bytes = _make_wav_bytes(frame_rate=16000, num_samples=100)
    pcm, frame_rate = google_voice_client._wav_to_pcm16_mono(wav_bytes)
    assert frame_rate == 16000
    assert len(pcm) == 200  # 100 samples * 2 bytes


def test_resample_to_target_changes_length_when_rate_differs():
    pcm = (1000).to_bytes(2, "little", signed=True) * 4410  # 0.1s @ 44100Hz mono 16-bit
    resampled = google_voice_client._resample_to_target(pcm, 44100)
    # 0.1s @ 16000Hz mono 16-bit = 1600 samples * 2 bytes = 3200 bytes
    assert abs(len(resampled) - 3200) < 200


def test_resample_to_target_noop_when_already_16k():
    pcm = (1000).to_bytes(2, "little", signed=True) * 1600
    assert google_voice_client._resample_to_target(pcm, 16000) is pcm


def test_speech_to_text_raises_on_non_wav_audio(monkeypatch):
    monkeypatch.setattr(google_voice_client, "_require_client", lambda: object())
    bad_audio = base64.b64encode(b"not a wav file").decode()
    with pytest.raises(GoogleVoiceError):
        google_voice_client.speech_to_text(bad_audio)


# ---------------------------------------------------------------------
# ASR happy/failure paths (fake Live API session)
# ---------------------------------------------------------------------


class _FakeTranscription:
    def __init__(self, text):
        self.text = text


class _FakeServerContent:
    def __init__(self, text, complete, spoken_text=None):
        self.output_transcription = _FakeTranscription(text)
        # What the user actually said, in their own language. None here
        # means the model only sent back the translation.
        self.input_transcription = _FakeTranscription(spoken_text) if spoken_text else None
        self.turn_complete = complete


class _FakeLiveResponse:
    def __init__(self, text, complete, spoken_text=None):
        self.server_content = _FakeServerContent(text, complete, spoken_text)


class _FakeLiveSession:
    def __init__(self, transcript_text, spoken_text=None):
        self._transcript_text = transcript_text
        self._spoken_text = spoken_text
        self.sent_chunks = []
        self.stream_ended = False

    async def send_realtime_input(self, audio=None, audio_stream_end=None):
        if audio is not None:
            self.sent_chunks.append(audio)
        if audio_stream_end:
            self.stream_ended = True

    async def receive(self):
        yield _FakeLiveResponse(self._transcript_text, True, self._spoken_text)


class _FakeLiveConnectCM:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *args):
        return False


class _FakeLive:
    def __init__(self, session):
        self._session = session

    def connect(self, model, config):
        return _FakeLiveConnectCM(self._session)


class _FakeAio:
    def __init__(self, session):
        self.live = _FakeLive(session)


class _FakeGeminiClient:
    def __init__(self, session):
        self.aio = _FakeAio(session)


def test_speech_to_text_happy_path(monkeypatch):
    fake_session = _FakeLiveSession("hello there")
    monkeypatch.setattr(google_voice_client, "_require_client", lambda: _FakeGeminiClient(fake_session))

    transcript = google_voice_client.speech_to_text(base64.b64encode(_make_wav_bytes()).decode())

    assert transcript == "hello there"
    assert fake_session.stream_ended
    assert len(fake_session.sent_chunks) > 0


def test_speech_to_text_returns_what_was_actually_said_not_the_translation(monkeypatch):
    """Multilingual answers depend on this. The Live model returns both an
    input transcription (the user's own words) and an English translation;
    returning the translation threw away the language before /query could
    detect it, so a Hindi question always came back answered in English.
    """
    fake_session = _FakeLiveSession("How is the weather today?", spoken_text="aaj ka mausam kaisa hai")
    monkeypatch.setattr(google_voice_client, "_require_client", lambda: _FakeGeminiClient(fake_session))

    transcript = google_voice_client.speech_to_text(base64.b64encode(_make_wav_bytes()).decode())

    assert transcript == "aaj ka mausam kaisa hai"


def test_speech_to_text_falls_back_to_translation_when_no_input_transcription(monkeypatch):
    fake_session = _FakeLiveSession("How is the weather today?", spoken_text=None)
    monkeypatch.setattr(google_voice_client, "_require_client", lambda: _FakeGeminiClient(fake_session))

    transcript = google_voice_client.speech_to_text(base64.b64encode(_make_wav_bytes()).decode())

    assert transcript == "How is the weather today?"


def test_speech_to_text_empty_transcript_raises(monkeypatch):
    fake_session = _FakeLiveSession("")
    monkeypatch.setattr(google_voice_client, "_require_client", lambda: _FakeGeminiClient(fake_session))

    with pytest.raises(GoogleVoiceError):
        google_voice_client.speech_to_text(base64.b64encode(_make_wav_bytes()).decode())


# ---------------------------------------------------------------------
# TTS happy/failure paths (fake generate_content response)
# ---------------------------------------------------------------------


class _FakeInlineData:
    def __init__(self, data):
        self.data = data


class _FakePart:
    def __init__(self, data):
        self.inline_data = _FakeInlineData(data)


class _FakeContent:
    def __init__(self, data):
        self.parts = [_FakePart(data)]


class _FakeCandidate:
    def __init__(self, data):
        self.content = _FakeContent(data)


class _FakeGenerateContentResponse:
    def __init__(self, data):
        self.candidates = [_FakeCandidate(data)]


class _FakeModels:
    def __init__(self, pcm_bytes):
        self._pcm_bytes = pcm_bytes
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return _FakeGenerateContentResponse(self._pcm_bytes)


class _FakeTTSClient:
    def __init__(self, pcm_bytes):
        self.models = _FakeModels(pcm_bytes)


def test_text_to_speech_happy_path(monkeypatch):
    pcm_bytes = (500).to_bytes(2, "little", signed=True) * 100
    fake_client = _FakeTTSClient(pcm_bytes)
    monkeypatch.setattr(google_voice_client, "_require_client", lambda: fake_client)

    audio_base64 = google_voice_client.text_to_speech("hello world")
    decoded = base64.b64decode(audio_base64)
    with wave.open(io.BytesIO(decoded), "rb") as wf:
        assert wf.getframerate() == google_voice_client._TTS_OUTPUT_SAMPLE_RATE
        assert wf.getnchannels() == 1

    assert fake_client.models.calls[0]["model"] == google_voice_client.settings.google_tts_model


def test_text_to_speech_empty_audio_raises(monkeypatch):
    fake_client = _FakeTTSClient(b"")
    monkeypatch.setattr(google_voice_client, "_require_client", lambda: fake_client)

    with pytest.raises(GoogleVoiceError):
        google_voice_client.text_to_speech("hello")
