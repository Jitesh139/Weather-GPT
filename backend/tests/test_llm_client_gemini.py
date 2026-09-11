"""Regression test for a real bug found during live testing against the
actual Gemini API: the verifier's JSON schema included
"additionalProperties" (required by Anthropic's structured-output
format), which Gemini's response_schema rejects outright with a 400
INVALID_ARGUMENT. GeminiClient must use a schema variant without it.
"""
from services.llm_client import GeminiClient, _VERIFY_SCHEMA_GEMINI


class _FakeResponse:
    def __init__(self, text):
        self.text = text


class _FakeModels:
    def __init__(self, capture):
        self._capture = capture

    def generate_content(self, model, contents, config):
        self._capture["config"] = config
        return _FakeResponse('{"passed": true, "mismatches": []}')


class _FakeGenaiClient:
    def __init__(self, capture):
        self.models = _FakeModels(capture)


def test_verify_schema_omits_additional_properties():
    assert "additionalProperties" not in _VERIFY_SCHEMA_GEMINI
    assert "passed" in _VERIFY_SCHEMA_GEMINI["properties"]


def test_gemini_client_verify_sends_schema_without_additional_properties(monkeypatch):
    from google import genai

    capture: dict = {}
    monkeypatch.setattr(genai, "Client", lambda api_key=None: _FakeGenaiClient(capture))

    client = GeminiClient(api_key="fake-key", model="gemini-3.6-flash")
    result = client.verify("It's 25°C, unusually warm.", {"current": {"temperature_2m": 25.0}})

    assert result.passed is True
    sent_schema = capture["config"].response_schema
    assert "additionalProperties" not in sent_schema
