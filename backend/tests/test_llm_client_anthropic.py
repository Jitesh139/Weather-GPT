"""Regression tests for a real bug found while running the app: the
verifier called Claude with max_tokens=1024, but the configured verifier
model (claude-opus-5) has adaptive thinking on by default and those
tokens count against max_tokens. The budget went to reasoning, the JSON
body was cut off mid-string, and every slow-path query failed with
"Unterminated string starting at: line 1 column 390" - a JSONDecodeError
surfaced to the user, with nothing pointing at the real cause.

Two things had to change: a cap generous enough to leave room for the
answer, and an explicit stop_reason check so a truncated response fails
loud and honestly instead of crashing in json.loads.
"""
import json

import pytest

from services.llm_client import (
    AnthropicClient,
    LLMResponseTruncated,
    _ANTHROPIC_MAX_TOKENS,
)


class _Block:
    def __init__(self, text, type="text"):
        self.text = text
        self.type = type


class _FakeResponse:
    def __init__(self, content, stop_reason="end_turn"):
        self.content = content
        self.stop_reason = stop_reason


class _FakeMessages:
    def __init__(self, response, capture):
        self._response = response
        self._capture = capture

    def create(self, **kwargs):
        self._capture.update(kwargs)
        return self._response


class _FakeAnthropic:
    def __init__(self, response, capture):
        self.messages = _FakeMessages(response, capture)


def _client_returning(monkeypatch, response) -> tuple[AnthropicClient, dict]:
    import anthropic

    capture: dict = {}
    monkeypatch.setattr(anthropic, "Anthropic", lambda api_key=None: _FakeAnthropic(response, capture))
    return AnthropicClient(api_key="fake-key", model="claude-opus-5"), capture


def test_verify_parses_a_complete_response(monkeypatch):
    payload = json.dumps({"passed": False, "mismatches": ["'unusually warm' is not supported"]})
    client, capture = _client_returning(monkeypatch, _FakeResponse([_Block(payload)]))

    result = client.verify("It's 25°C, unusually warm.", {"current": {"temperature_2m": 25.0}})

    assert result.passed is False
    assert result.mismatches == ["'unusually warm' is not supported"]
    assert capture["max_tokens"] == _ANTHROPIC_MAX_TOKENS


def test_verify_budget_leaves_room_for_thinking_plus_answer():
    # The value that caused the bug. Guards against anyone tightening it
    # back down without knowing why it's this large.
    assert _ANTHROPIC_MAX_TOKENS >= 16000


def test_verify_raises_honestly_when_response_was_truncated(monkeypatch):
    # Exactly the failure seen in production: valid-looking JSON, cut off
    # mid-string, returned with stop_reason="max_tokens".
    truncated = '{"passed": false, "mismatches": ["the draft says it is a good day for outdoor'
    client, _ = _client_returning(monkeypatch, _FakeResponse([_Block(truncated)], stop_reason="max_tokens"))

    with pytest.raises(LLMResponseTruncated):
        client.verify("It's a good day for outdoor plans.", {"current": {"temperature_2m": 25.0}})


def test_verify_skips_thinking_blocks_and_joins_text(monkeypatch):
    # Adaptive thinking puts a thinking block ahead of the JSON.
    content = [
        _Block("weighing the qualitative claims...", type="thinking"),
        _Block('{"passed": true, '),
        _Block('"mismatches": []}'),
    ]
    client, _ = _client_returning(monkeypatch, _FakeResponse(content))

    assert client.verify("It's 25°C.", {"current": {"temperature_2m": 25.0}}).passed is True


def test_verify_raises_when_no_text_block_came_back(monkeypatch):
    client, _ = _client_returning(monkeypatch, _FakeResponse([_Block("thinking only", type="thinking")]))

    with pytest.raises(Exception) as excinfo:
        client.verify("It's 25°C.", {"current": {"temperature_2m": 25.0}})
    assert "no text content" in str(excinfo.value)
