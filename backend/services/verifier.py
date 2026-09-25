"""Stage 2 - Verifier (spec Section 5.2). A programmatic number-extraction-
and-diff check runs first (cheap, reliable for pure number matching); the
LLM verifier is reserved for qualitative claims once numbers pass.
"""
import re
from typing import Any

from models.schemas import DraftAnswer, VerificationResult
from services.llm_client import LLMClient

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
NUMBER_TOLERANCE = 0.5


def extract_numbers(text: str) -> list[float]:
    return [float(m) for m in _NUMBER_RE.findall(text)]


def _flatten_numbers(obj: Any) -> list[float]:
    numbers: list[float] = []
    if isinstance(obj, bool):
        return numbers
    if isinstance(obj, (int, float)):
        numbers.append(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            numbers.extend(_flatten_numbers(v))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            numbers.extend(_flatten_numbers(v))
    return numbers


def diff_numbers(draft_numbers: list[float], source_numbers: list[float], tolerance: float = NUMBER_TOLERANCE) -> list[str]:
    mismatches = []
    for n in draft_numbers:
        if not any(abs(n - s) <= tolerance for s in source_numbers):
            mismatches.append(f"Draft states {n}, which does not match any value in the source data")
    return mismatches


def run_verifier(
    draft: DraftAnswer,
    llm_client: LLMClient,
    farmer_profile: dict[str, Any] | None = None,
) -> VerificationResult:
    source_data: dict[str, Any] = {"tool_results": draft.raw_tool_data}
    # The farmer's own crop details are grounding too - "planted 21 days
    # ago" must not fail the number diff for being absent from the forecast.
    if farmer_profile:
        source_data["farmer_profile"] = farmer_profile

    source_numbers = _flatten_numbers(source_data)
    draft_numbers = extract_numbers(draft.text)

    numeric_mismatches = diff_numbers(draft_numbers, source_numbers)
    if numeric_mismatches:
        return VerificationResult(passed=False, mismatches=numeric_mismatches)

    # Numbers all check out programmatically - the LLM verifier only needs
    # to judge qualitative/descriptive claims now.
    return llm_client.verify(draft.text, source_data)
