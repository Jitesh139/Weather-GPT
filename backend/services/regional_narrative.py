"""Optional narrative for the researcher dashboard's regional panel.

This is the only part of the dashboard that touches the LLM, and it
deliberately reuses the core pipeline rather than opening a second,
unverified generation path:

- Generation goes through LLMClient.generate_with_tools, so the existing
  enforcement applies unchanged - the model cannot state a number without
  having called the tool that returns the real data first
  (LLMUngroundedClaimError).
- Verification goes through the same run_verifier, so every number in the
  narrative is diffed against the fetched regional data, and the
  qualitative claims get the same LLM check as a conversational answer.

The prompt below is also where the "no causal claims" constraint is
enforced. A description of conditions in two places is grounded; "this
system will cause flooding in Kerala" is a forecast this platform has no
basis to make, and is exactly what the hallucination-prevention design
exists to prevent.
"""
import logging
from typing import Any

from models.schemas import DraftAnswer, VerificationResult
from services.llm_client import LLMClient, ToolSpec
from services.verifier import run_verifier

logger = logging.getLogger(__name__)

TOOL_NAME = "fetch_regional_conditions"

SYSTEM_PROMPT = (
    "You are describing current weather conditions across two sea regions (the Arabian Sea "
    "and the Bay of Bengal) and the Indian coastal cities near them, for a researcher "
    "audience. You MUST call the "
    f"{TOOL_NAME} tool to get the real data before stating any numeric value. Never invent "
    "or estimate a number - every number must come directly from the tool result.\n\n"
    "WHAT TO WRITE: a short, factual comparison - 3 to 5 sentences. Note where sea-level "
    "pressure is lowest, where winds and gusts are strongest, and how the coastal cities on "
    "the corresponding side currently compare. Use the exact values from the tool result.\n\n"
    "WHAT YOU MUST NOT WRITE: any causal or predictive claim linking the sea conditions to "
    "what will happen over land. Do not write that a system 'will bring', 'will cause', "
    "'is heading towards', or 'will intensify into' anything. You are not forecasting and "
    "you have no cyclone-track data. Describe what the numbers currently say, in both "
    "places, and stop there. If you want to note that low pressure over a sea region is "
    "generally associated with developing systems, you may say so ONCE and must frame it "
    "explicitly as general meteorological context rather than a prediction about this "
    "situation. Write in English.\n\n"
    "NUMBERS: quote values exactly as they appear in the tool result. Do not compute "
    "differences, ranges, averages or totals from them - a number you worked out yourself "
    "is not in the source data, and the verifier will reject the whole description for it."
)


def get_regional_tool_spec() -> ToolSpec:
    return {
        "name": TOOL_NAME,
        "description": (
            "Fetch current conditions and short-range forecast for the Arabian Sea and Bay of "
            "Bengal sampling points and the Indian coastal cities. Always call this before "
            "stating any numeric value."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    }


def build_regional_narrative(
    context: dict[str, Any],
    generator_client: LLMClient,
    verifier_client: LLMClient,
) -> tuple[DraftAnswer, VerificationResult]:
    """Generates and verifies the narrative. Returns the draft and its
    verification result; the caller decides what to surface, and must not
    surface an unverified draft."""

    def tool_executor(tool_name: str, _tool_input: dict) -> dict:
        if tool_name != TOOL_NAME:
            raise ValueError(f"Unknown tool requested: {tool_name}")
        # The data is already fetched and cached by the time we get here -
        # the "tool" hands the model exactly what the dashboard displays,
        # so the narrative and the charts can never disagree.
        return context

    query = (
        "Describe and compare the current conditions across the Arabian Sea, the Bay of "
        "Bengal, and the Indian coastal cities."
    )

    def generate(system_prompt: str) -> DraftAnswer:
        result = generator_client.generate_with_tools(
            system_prompt=system_prompt,
            user_query=query,
            tools=[get_regional_tool_spec()],
            tool_executor=tool_executor,
            max_tool_iterations=2,
        )
        return DraftAnswer(
            text=result.draft_text,
            raw_tool_data=result.raw_tool_data,
            tool_call_count=result.tool_call_count,
        )

    draft = generate(SYSTEM_PROMPT)
    verification = run_verifier(draft, verifier_client)

    if not verification.passed:
        # One retry with the mismatch fed back, exactly as the
        # conversational path does. In practice the first draft fails
        # mostly by computing a difference between two quoted values,
        # which the programmatic diff correctly rejects as a number that
        # isn't in the source data - and which the model fixes when told.
        logger.info(
            "Regional narrative failed verification (attempt 1): %s - retrying once",
            verification.mismatches,
        )
        hint = "; ".join(verification.mismatches)
        draft = generate(
            SYSTEM_PROMPT
            + f"\n\nNOTE: a previous draft failed verification for this reason: '{hint}'. "
            "Produce a corrected description that only states values appearing verbatim in "
            "the tool result."
        )
        verification = run_verifier(draft, verifier_client)

    if not verification.passed:
        logger.warning("Regional narrative failed verification twice: %s", verification.mismatches)
    return draft, verification
