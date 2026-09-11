"""End-to-end integration tests (spec Section 12): one fast-path query,
one slow-path query, one invalid-location query - all against the real
FastAPI app, with Open-Meteo mocked via respx and LLM providers mocked
via FakeLLMClient (zero real API keys needed).
"""
import httpx
import respx

from models.schemas import VerificationResult
from services import weather
from services.llm_client import GenerationResult
from tests.conftest import FakeLLMClient, SAMPLE_FORECAST_RESPONSE, SAMPLE_GEOCODE_RESPONSE


@respx.mock
def test_fast_path_query_end_to_end(client):
    respx.get(weather.GEOCODING_URL).mock(return_value=httpx.Response(200, json=SAMPLE_GEOCODE_RESPONSE))
    respx.get(weather.FORECAST_URL).mock(return_value=httpx.Response(200, json=SAMPLE_FORECAST_RESPONSE))

    response = client.post("/query", json={"text": "what is the temperature in Mumbai", "input_mode": "text"})

    assert response.status_code == 200
    body = response.json()
    assert body["path"] == "fast"
    assert body["verified"] is True
    assert body["error"] is None
    assert "29.5" in body["answer"]


@respx.mock
def test_slow_path_query_end_to_end(client, monkeypatch):
    respx.get(weather.GEOCODING_URL).mock(return_value=httpx.Response(200, json=SAMPLE_GEOCODE_RESPONSE))
    respx.get(weather.FORECAST_URL).mock(return_value=httpx.Response(200, json=SAMPLE_FORECAST_RESPONSE))

    def generate_fn(system_prompt, user_query, tools, tool_executor, max_tool_iterations):
        result = tool_executor("fetch_weather", {"location": "Delhi", "parameter": "precipitation"})
        precip = result["current"]["precipitation"]
        return GenerationResult(
            draft_text=f"With {precip} mm of precipitation right now, you likely won't need an umbrella today.",
            raw_tool_data=[result],
            tool_call_count=1,
        )

    def verify_fn(draft_answer, source_data, claims_to_check):
        return VerificationResult(passed=True, mismatches=[])

    fake_generator = FakeLLMClient(generate_fn=generate_fn)
    fake_verifier = FakeLLMClient(verify_fn=verify_fn)

    import main as main_module

    monkeypatch.setattr(main_module, "get_generator_client", lambda: fake_generator)
    monkeypatch.setattr(main_module, "get_verifier_client", lambda: fake_verifier)

    response = client.post(
        "/query", json={"text": "should i carry an umbrella in Delhi today", "input_mode": "text"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["path"] == "slow"
    assert body["verified"] is True
    assert body["error"] is None
    assert "umbrella" in body["answer"]


@respx.mock
def test_slow_path_unexpected_exception_returns_honest_error_not_crash(client, monkeypatch):
    """Regression test for a real bug found during live testing: an
    unmocked provider-side error (e.g. an SDK exception not covered by
    our custom exception types) must never surface as a raw 500 - it
    must come back as the spec's honest {error, answer: null} shape.
    """
    respx.get(weather.GEOCODING_URL).mock(return_value=httpx.Response(200, json=SAMPLE_GEOCODE_RESPONSE))
    respx.get(weather.FORECAST_URL).mock(return_value=httpx.Response(200, json=SAMPLE_FORECAST_RESPONSE))

    def generate_fn(*args, **kwargs):
        raise RuntimeError("simulated unexpected provider error")

    fake_generator = FakeLLMClient(generate_fn=generate_fn)
    fake_verifier = FakeLLMClient()

    import main as main_module

    monkeypatch.setattr(main_module, "get_generator_client", lambda: fake_generator)
    monkeypatch.setattr(main_module, "get_verifier_client", lambda: fake_verifier)

    response = client.post(
        "/query", json={"text": "should i carry an umbrella in Delhi today", "input_mode": "text"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] is None
    assert body["error"] is not None
    assert "simulated unexpected provider error" in body["error"]


@respx.mock
def test_invalid_location_query_returns_explicit_error(client):
    respx.get(weather.GEOCODING_URL).mock(return_value=httpx.Response(200, json={"results": []}))

    response = client.post(
        "/query", json={"text": "what is the temperature in Xyzzyplaceabc", "input_mode": "text"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] is None
    assert body["error"] is not None
    assert body["verified"] is False
