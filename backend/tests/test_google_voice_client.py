"""Google voice client tests (VOICE_PROVIDER=google). The Gemini SDK
client/session is faked out (mirroring how test_llm_client_gemini.py fakes
the google-genai SDK) so the suite stays offline - only the WAV encode/
decode/resample helpers and this module's own orchestration are under
test here, not Gemini's actual behavior.
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
# ASR happy/failure paths (fake one-shot generate_content)
# ---------------------------------------------------------------------


class _FakeASRResponse:
    def __init__(self, text):
        self.text = text


class _FakeASRModels:
    def __init__(self, text):
        self._text = text
        self.calls = []

    def generate_content(self, model, contents, config=None):
        self.calls.append({"model": model, "contents": contents})
        return _FakeASRResponse(self._text)


class _FakeASRClient:
    def __init__(self, text):
        self.models = _FakeASRModels(text)


def test_speech_to_text_happy_path(monkeypatch):
    fake = _FakeASRClient("  aaj ka mausam kaisa hai \n")
    monkeypatch.setattr(google_voice_client, "_require_client", lambda: fake)

    wav_48k = _make_wav_bytes(frame_rate=48000, num_samples=4800)
    transcript = google_voice_client.speech_to_text(base64.b64encode(wav_48k).decode())

    assert transcript == "aaj ka mausam kaisa hai"
    assert len(fake.models.calls) == 1
    audio_part = fake.models.calls[0]["contents"][0]
    with wave.open(io.BytesIO(audio_part.inline_data.data), "rb") as wf:
        assert wf.getframerate() == 16000  # downsampled before upload


@pytest.mark.parametrize("text", ["", None])
def test_speech_to_text_empty_transcript_raises(monkeypatch, text):
    monkeypatch.setattr(google_voice_client, "_require_client", lambda: _FakeASRClient(text))

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
