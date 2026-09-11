"""Unit tests for the Bhashini ASR/TTS client, against mocked HTTP
responses shaped exactly per Bhashini's own API documentation
(bhashini.gitbook.io/bhashini-apis) - config call then compute call.
No real Bhashini account was available during this build, so these
tests validate the client's request/response handling logic against the
documented contract, not a live account.
"""
import httpx
import pytest
import respx

from services import bhashini_client
from services.bhashini_client import (
    BhashiniConfigError,
    BhashiniError,
    speech_to_text,
    text_to_speech,
)

CALLBACK_URL = "https://dhruva-api.bhashini.gov.in/services/inference/pipeline"

CONFIG_RESPONSE_ASR = {
    "pipelineResponseConfig": [{"taskType": "asr", "config": [{"serviceId": "ai4bharat/conformer-hi-gpu--t4"}]}],
    "pipelineInferenceAPIEndPoint": {
        "callbackUrl": CALLBACK_URL,
        "inferenceApiKey": {"name": "Authorization", "value": "test-dynamic-token"},
    },
}

CONFIG_RESPONSE_TTS = {
    "pipelineResponseConfig": [{"taskType": "tts", "config": [{"serviceId": "ai4bharat/vakyansh-tts-hi"}]}],
    "pipelineInferenceAPIEndPoint": {
        "callbackUrl": CALLBACK_URL,
        "inferenceApiKey": {"name": "Authorization", "value": "test-dynamic-token"},
    },
}

ASR_COMPUTE_RESPONSE = {
    "pipelineResponse": [{"taskType": "asr", "output": [{"source": "मुंबई में मौसम कैसा है"}], "audio": None}]
}

TTS_COMPUTE_RESPONSE = {
    "pipelineResponse": [{"taskType": "tts", "output": None, "audio": [{"audioContent": "ZmFrZWJhc2U2NA==", "audioUri": None}]}]
}


@pytest.fixture(autouse=True)
def _reset_pipeline_cache():
    bhashini_client._pipeline_config_cache.clear()
    yield
    bhashini_client._pipeline_config_cache.clear()


@pytest.fixture
def with_credentials(monkeypatch):
    monkeypatch.setattr(bhashini_client.settings, "bhashini_user_id", "test-user")
    monkeypatch.setattr(bhashini_client.settings, "bhashini_api_key", "test-key")


def test_missing_credentials_raises_config_error(monkeypatch):
    monkeypatch.setattr(bhashini_client.settings, "bhashini_user_id", None)
    monkeypatch.setattr(bhashini_client.settings, "bhashini_api_key", None)

    with pytest.raises(BhashiniConfigError):
        speech_to_text("ZmFrZQ==", "hi")


@respx.mock
def test_speech_to_text_round_trip(with_credentials):
    respx.post(bhashini_client.CONFIG_URL).mock(return_value=httpx.Response(200, json=CONFIG_RESPONSE_ASR))
    respx.post(CALLBACK_URL).mock(return_value=httpx.Response(200, json=ASR_COMPUTE_RESPONSE))

    transcript = speech_to_text("ZmFrZWF1ZGlv", "hi")

    assert transcript == "मुंबई में मौसम कैसा है"


@respx.mock
def test_text_to_speech_round_trip(with_credentials):
    respx.post(bhashini_client.CONFIG_URL).mock(return_value=httpx.Response(200, json=CONFIG_RESPONSE_TTS))
    respx.post(CALLBACK_URL).mock(return_value=httpx.Response(200, json=TTS_COMPUTE_RESPONSE))

    audio_b64 = text_to_speech("मुंबई में आज धूप है", "hi")

    assert audio_b64 == "ZmFrZWJhc2U2NA=="


@respx.mock
def test_pipeline_config_is_cached_across_calls(with_credentials):
    config_route = respx.post(bhashini_client.CONFIG_URL).mock(
        return_value=httpx.Response(200, json=CONFIG_RESPONSE_ASR)
    )
    respx.post(CALLBACK_URL).mock(return_value=httpx.Response(200, json=ASR_COMPUTE_RESPONSE))

    speech_to_text("ZmFrZQ==", "hi")
    speech_to_text("ZmFrZQ==", "hi")

    assert config_route.call_count == 1


@respx.mock
def test_config_call_sends_userid_and_apikey_headers(with_credentials):
    route = respx.post(bhashini_client.CONFIG_URL).mock(return_value=httpx.Response(200, json=CONFIG_RESPONSE_ASR))
    respx.post(CALLBACK_URL).mock(return_value=httpx.Response(200, json=ASR_COMPUTE_RESPONSE))

    speech_to_text("ZmFrZQ==", "hi")

    sent_request = route.calls.last.request
    assert sent_request.headers["userID"] == "test-user"
    assert sent_request.headers["ulcaApiKey"] == "test-key"


@respx.mock
def test_compute_call_uses_dynamic_auth_header_not_static_credentials(with_credentials):
    respx.post(bhashini_client.CONFIG_URL).mock(return_value=httpx.Response(200, json=CONFIG_RESPONSE_ASR))
    compute_route = respx.post(CALLBACK_URL).mock(return_value=httpx.Response(200, json=ASR_COMPUTE_RESPONSE))

    speech_to_text("ZmFrZQ==", "hi")

    sent_request = compute_route.calls.last.request
    assert sent_request.headers["Authorization"] == "test-dynamic-token"


@respx.mock
def test_malformed_config_response_raises_bhashini_error(with_credentials):
    respx.post(bhashini_client.CONFIG_URL).mock(return_value=httpx.Response(200, json={"pipelineResponseConfig": []}))

    with pytest.raises(BhashiniError):
        speech_to_text("ZmFrZQ==", "hi")


@respx.mock
def test_malformed_compute_response_raises_bhashini_error(with_credentials):
    respx.post(bhashini_client.CONFIG_URL).mock(return_value=httpx.Response(200, json=CONFIG_RESPONSE_ASR))
    respx.post(CALLBACK_URL).mock(return_value=httpx.Response(200, json={"unexpected": "shape"}))

    with pytest.raises(BhashiniError):
        speech_to_text("ZmFrZQ==", "hi")
