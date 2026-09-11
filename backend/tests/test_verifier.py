"""Verifier (Stage 2) tests. Spec Section 12: feed a draft with a
deliberately wrong number and confirm the mismatch is caught - this must
be caught by the cheap programmatic diff, without needing an LLM call.
"""
from models.schemas import DraftAnswer, VerificationResult
from services.verifier import diff_numbers, extract_numbers, run_verifier
from tests.conftest import FakeLLMClient


def test_extract_numbers_finds_all_numeric_tokens():
    assert extract_numbers("It's 29.5°C with 10% chance of rain, wind 3 m/s") == [29.5, 10.0, 3.0]


def test_diff_numbers_flags_unmatched_value():
    mismatches = diff_numbers(draft_numbers=[32.0], source_numbers=[25.0, 3.0])
    assert len(mismatches) == 1
    assert "32.0" in mismatches[0]


def test_diff_numbers_allows_small_tolerance():
    mismatches = diff_numbers(draft_numbers=[25.3], source_numbers=[25.0], tolerance=0.5)
    assert mismatches == []


def test_run_verifier_catches_deliberately_wrong_number():
    draft = DraftAnswer(
        text="It's currently 32°C in Delhi with light wind.",
        raw_tool_data=[{"current": {"temperature_2m": 25.0, "wind_speed_10m": 3.0}}],
        tool_call_count=1,
    )
    # No verify_fn configured - if the programmatic check doesn't catch
    # the mismatch and falls through to the LLM verifier, this raises
    # NotImplementedError and the test fails, proving the numeric diff
    # runs first and is sufficient on its own.
    fake_llm = FakeLLMClient()

    result = run_verifier(draft, fake_llm)

    assert result.passed is False
    assert any("32" in m for m in result.mismatches)
    assert fake_llm.verify_calls == []


def test_run_verifier_passes_matching_numbers_and_defers_qualitative_check_to_llm():
    draft = DraftAnswer(
        text="It's currently 25°C in Delhi, unusually warm for this time of year.",
        raw_tool_data=[{"current": {"temperature_2m": 25.0, "wind_speed_10m": 3.0}}],
        tool_call_count=1,
    )

    def verify_fn(draft_answer, source_data, claims_to_check):
        assert "unusually warm" in draft_answer
        return VerificationResult(passed=True, mismatches=[])

    fake_llm = FakeLLMClient(verify_fn=verify_fn)

    result = run_verifier(draft, fake_llm)

    assert result.passed is True
    assert len(fake_llm.verify_calls) == 1


def test_run_verifier_surfaces_llm_detected_qualitative_mismatch():
    draft = DraftAnswer(
        text="It's 25°C, the hottest day on record for this city.",
        raw_tool_data=[{"current": {"temperature_2m": 25.0}}],
        tool_call_count=1,
    )

    def verify_fn(draft_answer, source_data, claims_to_check):
        return VerificationResult(passed=False, mismatches=["'hottest day on record' is not supported by the data"])

    fake_llm = FakeLLMClient(verify_fn=verify_fn)
    result = run_verifier(draft, fake_llm)

    assert result.passed is False
    assert "hottest day on record" in result.mismatches[0]
