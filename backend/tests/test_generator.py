"""Generator (Stage 1) tests. Exercises both the orchestration in
generator.py (via FakeLLMClient) and the real tool-call enforcement in
AnthropicClient.generate_with_tools (spec Section 12: "simulate provider
never calling the tool - assert generator... never accepts an untooled
numeric answer"), with the Anthropic SDK itself mocked out.
"""
import respx
import httpx
import pytest

from models.schemas import DraftAnswer
from services.generator import run_generator
from services.llm_client import AnthropicClient, GenerationResult, LLMUngroundedClaimError
from tests.conftest import FakeLLMClient, SAMPLE_FORECAST_RESPONSE, SAMPLE_GEOCODE_RESPONSE
from services import weather


class _Block:
    def __init__(self, type_, text=None, name=None, input=None, id=None):
        self.type = type_
        self.text = text
        self.name = name
        self.input = input
        self.id = id


class _Response:
    def __init__(self, stop_reason, content):
        self.stop_reason = stop_reason
        self.content = content


class _FakeMessagesAPI:
    def __init__(self, responses):
        self._responses = iter(responses)

    def create(self, **kwargs):
        return next(self._responses)


class _FakeAnthropic:
    def __init__(self, responses):
        self.messages = _FakeMessagesAPI(responses)


def test_run_generator_returns_draft_after_one_tool_call():
    def generate_fn(system_prompt, user_query, tools, tool_executor, max_tool_iterations):
        result = tool_executor("fetch_weather", {"location": "Mumbai"})
        temp = result["current"]["temperature_2m"]
        return GenerationResult(
            draft_text=f"It's {temp}°C in Mumbai.",
            raw_tool_data=[result],
            tool_call_count=1,
        )

    fake_client = FakeLLMClient(generate_fn=generate_fn)

    # run_generator builds its own tool_executor internally (wired to the
    # real weather service, via respx-mocked Open-Meteo calls below), and
    # generate_fn (standing in for the LLM provider) calls it directly.
    with respx.mock:
        respx.get(weather.GEOCODING_URL).mock(return_value=httpx.Response(200, json=SAMPLE_GEOCODE_RESPONSE))
        respx.get(weather.FORECAST_URL).mock(return_value=httpx.Response(200, json=SAMPLE_FORECAST_RESPONSE))

        from db.session import SessionLocal

        db = SessionLocal()
        try:
            draft = run_generator("what's the temperature in Mumbai", fake_client, db)
        finally:
            db.close()

    assert isinstance(draft, DraftAnswer)
    assert "29.5" in draft.text
    assert draft.tool_call_count == 1
    assert fake_client.generate_calls[0]["user_query"] == "what's the temperature in Mumbai"


def test_run_generator_injects_mismatch_hint_into_system_prompt():
    def generate_fn(system_prompt, user_query, tools, tool_executor, max_tool_iterations):
        assert "wrong number" in system_prompt
        return GenerationResult(draft_text="Corrected answer.", raw_tool_data=[], tool_call_count=0)

    fake_client = FakeLLMClient(generate_fn=generate_fn)
    from db.session import SessionLocal

    db = SessionLocal()
    try:
        draft = run_generator("temperature in Pune", fake_client, db, mismatch_hint="wrong number")
    finally:
        db.close()
    assert draft.text == "Corrected answer."


def test_anthropic_client_enforces_tool_call_before_numeric_claim(monkeypatch):
    """The provider never calls the tool but states a number anyway -
    this must be rejected, never silently accepted.
    """
    import anthropic

    responses = [_Response("end_turn", [_Block("text", text="It's about 30 degrees today.")])]
    monkeypatch.setattr(anthropic, "Anthropic", lambda api_key=None: _FakeAnthropic(responses))

    client = AnthropicClient(api_key="fake-key", model="claude-opus-5")

    with pytest.raises(LLMUngroundedClaimError):
        client.generate_with_tools(
            system_prompt="system",
            user_query="what's the temperature",
            tools=[],
            tool_executor=lambda name, inp: {},
        )


def test_anthropic_client_executes_tool_then_returns_grounded_draft(monkeypatch):
    import anthropic

    tool_use_block = _Block("tool_use", name="fetch_weather", input={"location": "Mumbai"}, id="tool_1")
    responses = [
        _Response("tool_use", [tool_use_block]),
        _Response("end_turn", [_Block("text", text="It's 29.5°C in Mumbai.")]),
    ]
    monkeypatch.setattr(anthropic, "Anthropic", lambda api_key=None: _FakeAnthropic(responses))

    client = AnthropicClient(api_key="fake-key", model="claude-opus-5")
    calls = []

    def tool_executor(name, tool_input):
        calls.append((name, tool_input))
        return {"temperature_2m": 29.5}

    result = client.generate_with_tools(
        system_prompt="system",
        user_query="what's the temperature in Mumbai",
        tools=[{"name": "fetch_weather", "description": "d", "input_schema": {}}],
        tool_executor=tool_executor,
    )

    assert result.tool_call_count == 1
    assert result.raw_tool_data == [{"temperature_2m": 29.5}]
    assert "29.5" in result.draft_text
    assert calls == [("fetch_weather", {"location": "Mumbai"})]
