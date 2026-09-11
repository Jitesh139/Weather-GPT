"""Integration tests for the /voice/* endpoints - config discovery, and
ASR/TTS proxying to Bhashini (mocked, per its documented API contract).
"""
import base64
import io
import wave

import httpx
import respx

from services import bhashini_client, google_voice_client
from tests.test_bhashini import (
    ASR_COMPUTE_RESPONSE,
    CALLBACK_URL,
    CONFIG_RESPONSE_ASR,
    CONFIG_RESPONSE_TTS,
    TTS_COMPUTE_RESPONSE,
)


def _sample_wav_base64() -> str:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes((100).to_bytes(2, "little", signed=True) * 1600)
    return base64.b64encode(buf.getvalue()).decode()


def test_voice_config_reports_web_speech_by_default(client):
    response = client.get("/voice/config")
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "web_speech"


def test_voice_config_never_exposes_credentials(client, monkeypatch):
    monkeypatch.setattr(bhashini_client.settings, "voice_provider", "bhashini")
    monkeypatch.setattr(bhashini_client.settings, "bhashini_user_id", "secret-user")
    monkeypatch.setattr(bhashini_client.settings, "bhashini_api_key", "secret-key")

    response = client.get("/voice/config")
    body = response.json()

    assert body["provider"] == "bhashini"
    assert "secret-user" not in response.text
    assert "secret-key" not in response.text


def test_voice_asr_without_credentials_returns_honest_error(client, monkeypatch):
    monkeypatch.setattr(bhashini_client.settings, "bhashini_user_id", None)
    monkeypatch.setattr(bhashini_client.settings, "bhashini_api_key", None)

    response = client.post("/voice/asr", json={"audio_base64": "ZmFrZQ==", "language": "hi"})

    assert response.status_code == 200
    body = response.json()
    assert body["transcript"] is None
    assert body["error"] is not None


@respx.mock
def test_voice_asr_success(client, monkeypatch):
    monkeypatch.setattr(bhashini_client.settings, "bhashini_user_id", "test-user")
    monkeypatch.setattr(bhashini_client.settings, "bhashini_api_key", "test-key")
    bhashini_client._pipeline_config_cache.clear()

    respx.post(bhashini_client.CONFIG_URL).mock(return_value=httpx.Response(200, json=CONFIG_RESPONSE_ASR))
    respx.post(CALLBACK_URL).mock(return_value=httpx.Response(200, json=ASR_COMPUTE_RESPONSE))

    response = client.post("/voice/asr", json={"audio_base64": "ZmFrZWF1ZGlv", "language": "hi"})

    assert response.status_code == 200
    body = response.json()
    assert body["transcript"] == "मुंबई में मौसम कैसा है"
    assert body["error"] is None


@respx.mock
def test_voice_tts_success(client, monkeypatch):
    monkeypatch.setattr(bhashini_client.settings, "bhashini_user_id", "test-user")
    monkeypatch.setattr(bhashini_client.settings, "bhashini_api_key", "test-key")
    bhashini_client._pipeline_config_cache.clear()

    respx.post(bhashini_client.CONFIG_URL).mock(return_value=httpx.Response(200, json=CONFIG_RESPONSE_TTS))
    respx.post(CALLBACK_URL).mock(return_value=httpx.Response(200, json=TTS_COMPUTE_RESPONSE))

    response = client.post("/voice/tts", json={"text": "मुंबई में आज धूप है", "language": "hi"})

    assert response.status_code == 200
    body = response.json()
    assert body["audio_base64"] == "ZmFrZWJhc2U2NA=="
    assert body["error"] is None


def test_voice_tts_never_returns_500_on_unexpected_error(client, monkeypatch):
    monkeypatch.setattr(bhashini_client.settings, "bhashini_user_id", "test-user")
    monkeypatch.setattr(bhashini_client.settings, "bhashini_api_key", "test-key")

    def boom(*args, **kwargs):
        raise RuntimeError("simulated unexpected failure")

    monkeypatch.setattr(bhashini_client, "text_to_speech", boom)

    response = client.post("/voice/tts", json={"text": "hello", "language": "hi"})

    assert response.status_code == 200
    body = response.json()
    assert body["audio_base64"] is None
    assert "simulated unexpected failure" in body["error"]


def test_voice_asr_routes_to_google_when_configured(client, monkeypatch):
    monkeypatch.setattr(google_voice_client.settings, "voice_provider", "google")
    monkeypatch.setattr(google_voice_client, "speech_to_text", lambda audio_base64, sampling_rate: "hello from google")

    response = client.post("/voice/asr", json={"audio_base64": _sample_wav_base64(), "sampling_rate": 16000})

    assert response.status_code == 200
    body = response.json()
    assert body["transcript"] == "hello from google"
    assert body["error"] is None


def test_voice_asr_google_config_error_returns_honest_error(client, monkeypatch):
    monkeypatch.setattr(google_voice_client.settings, "voice_provider", "google")
    monkeypatch.setattr(google_voice_client.settings, "gemini_api_key", None)

    response = client.post("/voice/asr", json={"audio_base64": _sample_wav_base64()})

    assert response.status_code == 200
    body = response.json()
    assert body["transcript"] is None
    assert "GEMINI_API_KEY" in body["error"]


def test_voice_tts_routes_to_google_when_configured(client, monkeypatch):
    monkeypatch.setattr(google_voice_client.settings, "voice_provider", "google")
    monkeypatch.setattr(google_voice_client, "text_to_speech", lambda text: "ZmFrZWF1ZGlv")

    response = client.post("/voice/tts", json={"text": "hello"})

    assert response.status_code == 200
    body = response.json()
    assert body["audio_base64"] == "ZmFrZWF1ZGlv"
    assert body["error"] is None
