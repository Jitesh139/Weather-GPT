"""Provider-agnostic LLM abstraction (spec Section 5.3). Pipeline code
(generator.py, verifier.py) only ever talks to this interface - never to
the Anthropic or Gemini SDKs directly - so the generator/verifier
provider split can be changed via config alone.

NOT FINALIZED (spec Section 14.1): the default split below is
Claude=generator, Gemini=verifier. This is a reasonable default, not a
locked-in decision - swap it via GENERATOR_PROVIDER/VERIFIER_PROVIDER in
.env without touching any pipeline code.
"""
import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional, TypedDict

from config import settings
from models.schemas import VerificationResult

logger = logging.getLogger(__name__)


class ToolSpec(TypedDict):
    name: str
    description: str
    input_schema: dict[str, Any]


class GenerationResult:
    def __init__(self, draft_text: str, raw_tool_data: list[dict[str, Any]], tool_call_count: int):
        self.draft_text = draft_text
        self.raw_tool_data = raw_tool_data
        self.tool_call_count = tool_call_count


class LLMConfigError(Exception):
    """Raised when a provider is invoked without its API key configured."""


class LLMToolLoopExceeded(Exception):
    """Raised when the generator doesn't converge to a final answer within
    the allowed tool-call iterations - fail loud, never guess."""


class LLMResponseTruncated(Exception):
    """The model hit max_tokens before finishing its response, so what came
    back is incomplete (and, for a JSON response, unparseable)."""


class LLMUngroundedClaimError(Exception):
    """Raised when the generator produces a numeric claim without ever
    calling the weather tool - the concrete enforcement of spec principle
    #1 ("no ungrounded numbers ever")."""


ToolExecutor = Callable[[str, dict[str, Any]], dict[str, Any]]

# Deliberately generous. On Claude Opus 5 (the configured verifier)
# adaptive thinking is on by default and its tokens count against
# max_tokens, so a tight cap gets spent on reasoning and truncates the
# actual answer mid-sentence. This bit us for real: the verifier at
# max_tokens=1024 returned a JSON object cut off at 389 characters, and
# every slow-path query died on "Unterminated string". Anthropic's own
# guidance for non-streaming requests is ~16k, which is a ceiling, not a
# reservation - we're billed for tokens produced, not tokens allowed.
_ANTHROPIC_MAX_TOKENS = 16000

# Thinking depth for the Stage 2 verifier. See the comment at the call
# site for why this is low rather than the default. Override per
# deployment if you'd rather trade latency back for scrutiny.
_VERIFIER_EFFORT = "low"

_VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "passed": {"type": "boolean"},
        "mismatches": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["passed", "mismatches"],
    "additionalProperties": False,
}

# Gemini's response_schema is Google's OpenAPI-derived Schema type, which
# does not recognize "additionalProperties" (confirmed against a live
# call - Gemini returns a 400 INVALID_ARGUMENT if it's present). Anthropic's
# json_schema output_config requires it. Same logical schema, two shapes.
_VERIFY_SCHEMA_GEMINI = {k: v for k, v in _VERIFY_SCHEMA.items() if k != "additionalProperties"}


def _verify_prompt(draft_answer: str, source_data: dict[str, Any], claims_to_check: Optional[list[str]]) -> str:
    prompt = (
        "You are a strict fact-checker for a weather assistant. You will be given a draft "
        "answer and the raw source data it must be grounded in. Numeric values have ALREADY "
        "been checked separately by a programmatic diff - do not re-check numbers. Your job "
        "is to check only qualitative/descriptive claims (e.g. 'unusually warm for this time "
        "of year', 'a good day for outdoor plans') and confirm each is a reasonable "
        "characterization of the source data. If a qualitative claim is not supported by the "
        "data, list it in mismatches and set passed to false. If there are no qualitative "
        "claims, or all are supported, set passed to true with an empty mismatches list.\n\n"
        f"Draft answer:\n{draft_answer}\n\n"
        f"Source data (JSON):\n{json.dumps(source_data, default=str)}\n"
    )
    if claims_to_check:
        prompt += f"\nClaims flagged for special attention: {claims_to_check}\n"
    return prompt


class LLMClient(ABC):
    @abstractmethod
    def generate_with_tools(
        self,
        system_prompt: str,
        user_query: str,
        tools: list[ToolSpec],
        tool_executor: ToolExecutor,
        max_tool_iterations: int = 3,
    ) -> GenerationResult: ...

    @abstractmethod
    def verify(
        self,
        draft_answer: str,
        source_data: dict[str, Any],
        claims_to_check: Optional[list[str]] = None,
    ) -> VerificationResult: ...


class AnthropicClient(LLMClient):
    def __init__(self, api_key: Optional[str], model: str):
        self.api_key = api_key
        self.model = model

    def _client(self):
        import anthropic

        if not self.api_key:
            raise LLMConfigError("ANTHROPIC_API_KEY is not set")
        return anthropic.Anthropic(api_key=self.api_key)

    def generate_with_tools(
        self,
        system_prompt: str,
        user_query: str,
        tools: list[ToolSpec],
        tool_executor: ToolExecutor,
        max_tool_iterations: int = 3,
    ) -> GenerationResult:
        client = self._client()
        anthropic_tools = [
            {"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]} for t in tools
        ]
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_query}]
        raw_tool_data: list[dict[str, Any]] = []
        tool_call_count = 0

        for _ in range(max_tool_iterations + 1):
            response = client.messages.create(
                model=self.model,
                max_tokens=_ANTHROPIC_MAX_TOKENS,
                system=system_prompt,
                tools=anthropic_tools,
                messages=messages,
            )

            if response.stop_reason == "max_tokens":
                raise LLMResponseTruncated(
                    f"Generator response hit the {_ANTHROPIC_MAX_TOKENS}-token cap before finishing"
                )

            if response.stop_reason == "end_turn":
                draft_text = next((b.text for b in response.content if b.type == "text"), "")
                if tool_call_count == 0 and re.search(r"\d", draft_text):
                    raise LLMUngroundedClaimError(
                        "Generator produced a numeric claim without calling the weather tool"
                    )
                return GenerationResult(
                    draft_text=draft_text, raw_tool_data=raw_tool_data, tool_call_count=tool_call_count
                )

            if response.stop_reason != "tool_use":
                raise LLMToolLoopExceeded(f"Unexpected stop_reason from generator: {response.stop_reason}")

            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in tool_use_blocks:
                tool_call_count += 1
                try:
                    result = tool_executor(block.name, block.input)
                    raw_tool_data.append(result)
                    tool_results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result, default=str)}
                    )
                except Exception as exc:  # noqa: BLE001 - tool failure must reach the model as an error result
                    tool_results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": str(exc), "is_error": True}
                    )
            messages.append({"role": "user", "content": tool_results})

        raise LLMToolLoopExceeded(f"Generator did not finish within {max_tool_iterations} tool-call iterations")

    def verify(
        self,
        draft_answer: str,
        source_data: dict[str, Any],
        claims_to_check: Optional[list[str]] = None,
    ) -> VerificationResult:
        client = self._client()
        prompt = _verify_prompt(draft_answer, source_data, claims_to_check)

        response = client.messages.create(
            model=self.model,
            max_tokens=_ANTHROPIC_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
            output_config={
                "format": {"type": "json_schema", "schema": _VERIFY_SCHEMA},
                # Low effort, deliberately. By the time this runs, every
                # number has already been checked by the programmatic diff
                # in verifier.py - all that's left is judging a couple of
                # qualitative phrases against the source data, which is
                # exactly the "simple task" case Anthropic recommends low
                # effort for. At the default (high), Opus 5 was spending
                # 3-24 seconds thinking about it and dominating total
                # response time; measured at low it is a fraction of that.
                "effort": _VERIFIER_EFFORT,
            },
        )

        # Checked before parsing: a truncated response still arrives as a
        # 200 with a half-written JSON string, which json.loads reports as
        # a baffling "Unterminated string" rather than what actually
        # happened. Fail loud and honestly instead.
        if response.stop_reason == "max_tokens":
            raise LLMResponseTruncated(
                f"Verifier response hit the {_ANTHROPIC_MAX_TOKENS}-token cap before the JSON was complete"
            )

        text = "".join(b.text for b in response.content if b.type == "text")
        if not text.strip():
            raise LLMToolLoopExceeded(
                f"Verifier returned no text content (stop_reason={response.stop_reason})"
            )
        data = json.loads(text)
        return VerificationResult(passed=data["passed"], mismatches=data.get("mismatches", []))


class GeminiClient(LLMClient):
    def __init__(self, api_key: Optional[str], model: str):
        self.api_key = api_key
        self.model = model

    def _client(self):
        from google import genai

        if not self.api_key:
            raise LLMConfigError("GEMINI_API_KEY is not set")
        return genai.Client(api_key=self.api_key)

    def generate_with_tools(
        self,
        system_prompt: str,
        user_query: str,
        tools: list[ToolSpec],
        tool_executor: ToolExecutor,
        max_tool_iterations: int = 3,
    ) -> GenerationResult:
        from google.genai import types

        client = self._client()
        function_declarations = [
            types.FunctionDeclaration(name=t["name"], description=t["description"], parameters=t["input_schema"])
            for t in tools
        ]
        gemini_tool = types.Tool(function_declarations=function_declarations)
        contents: list[Any] = [types.Content(role="user", parts=[types.Part(text=user_query)])]
        raw_tool_data: list[dict[str, Any]] = []
        tool_call_count = 0

        for _ in range(max_tool_iterations + 1):
            response = client.models.generate_content(
                model=self.model,
                contents=contents,
                config=types.GenerateContentConfig(system_instruction=system_prompt, tools=[gemini_tool]),
            )
            candidate = response.candidates[0]
            parts = candidate.content.parts or []
            function_calls = [p.function_call for p in parts if getattr(p, "function_call", None)]

            if not function_calls:
                draft_text = "".join(p.text for p in parts if getattr(p, "text", None))
                if tool_call_count == 0 and re.search(r"\d", draft_text):
                    raise LLMUngroundedClaimError(
                        "Generator produced a numeric claim without calling the weather tool"
                    )
                return GenerationResult(
                    draft_text=draft_text, raw_tool_data=raw_tool_data, tool_call_count=tool_call_count
                )

            contents.append(candidate.content)
            response_parts = []
            for fc in function_calls:
                tool_call_count += 1
                try:
                    result = tool_executor(fc.name, dict(fc.args or {}))
                    raw_tool_data.append(result)
                    response_parts.append(types.Part.from_function_response(name=fc.name, response={"result": result}))
                except Exception as exc:  # noqa: BLE001
                    response_parts.append(types.Part.from_function_response(name=fc.name, response={"error": str(exc)}))
            contents.append(types.Content(role="user", parts=response_parts))

        raise LLMToolLoopExceeded(f"Generator did not finish within {max_tool_iterations} tool-call iterations")

    def verify(
        self,
        draft_answer: str,
        source_data: dict[str, Any],
        claims_to_check: Optional[list[str]] = None,
    ) -> VerificationResult:
        from google.genai import types

        client = self._client()
        prompt = _verify_prompt(draft_answer, source_data, claims_to_check)

        response = client.models.generate_content(
            model=self.model,
            contents=[types.Content(role="user", parts=[types.Part(text=prompt)])],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_VERIFY_SCHEMA_GEMINI,
            ),
        )
        # response.text is None when the candidate was truncated or blocked;
        # json.loads(None) would raise a TypeError that says nothing useful.
        if not (response.text or "").strip():
            raise LLMResponseTruncated("Verifier returned an empty response")
        data = json.loads(response.text)
        return VerificationResult(passed=data["passed"], mismatches=data.get("mismatches", []))


def _build_client(provider: str, api_key: Optional[str], model: str) -> LLMClient:
    if provider == "claude":
        return AnthropicClient(api_key=api_key, model=model)
    if provider == "gemini":
        return GeminiClient(api_key=api_key, model=model)
    raise ValueError(f"Unknown LLM provider: {provider}")


def get_generator_client() -> LLMClient:
    api_key = settings.anthropic_api_key if settings.generator_provider == "claude" else settings.gemini_api_key
    return _build_client(settings.generator_provider, api_key, settings.generator_model)


def get_verifier_client() -> LLMClient:
    api_key = settings.anthropic_api_key if settings.verifier_provider == "claude" else settings.gemini_api_key
    return _build_client(settings.verifier_provider, api_key, settings.verifier_model)
