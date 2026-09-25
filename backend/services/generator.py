"""Stage 1 - Generator (spec Section 5.1). Must call the weather-fetch
tool mid-turn via real tool-use before making any numeric claim; this is
enforced by LLMClient.generate_with_tools (see llm_client.py), not just
prompted.
"""
import logging
import re

from sqlalchemy.orm import Session

from models.schemas import CropContext, DraftAnswer
from services import weather
from services.llm_client import GenerationResult, LLMClient, ToolSpec
from services.weather import WeatherServiceError

logger = logging.getLogger(__name__)

_DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")

SYSTEM_PROMPT = (
    "You are WeatherGPT's answer generator. You MUST call the fetch_weather tool to get "
    "real data BEFORE stating any temperature, precipitation, wind, or other numeric value. "
    "Never invent or estimate a number yourself - every number in your answer must come "
    "directly from the tool result. The tool result may include a 'google' block (a second, "
    "independent weather source) and a 'best_estimate' block (the two sources blended) - when "
    "present, prefer citing the best_estimate value. Don't name the data sources unless the "
    "user asks. If the tool reports an error (e.g. location not found), "
    "say so plainly instead of guessing a value. Answer in one to three short, conversational "
    "sentences suitable for being spoken aloud - lead with what the user asked for and skip "
    "preamble. If the question involves crop/agricultural advice, "
    "explicitly caveat that this is general guidance, not a verified agronomic recommendation."
    "\n\nLANGUAGE: Write your entire answer in the SAME language and script the user used in "
    "their question. If they asked in Hindi, answer in Hindi; if they wrote Hindi in Latin "
    "script ('aaj ka mausam kaisa hai'), answer the same way, in Latin script; if they asked "
    "in English, answer in English. This applies to any language, not only these. Keep place "
    "names and the numbers themselves exactly as the tool returned them - translate the "
    "sentence around them, never the data."
)


def crop_context_prompt(crop_context: CropContext) -> str:
    planted = (
        f"planted {crop_context.planted_days_ago} days ago"
        if crop_context.planted_days_ago is not None
        else "planting date unknown"
    )
    return (
        f"\n\nFARMER CONTEXT: this user is a farmer growing {crop_context.crop} ({planted}). "
        "Work out the crop's likely growth stage from that. When it is relevant to the question, "
        "add one short sentence relating the weather to this crop and stage - irrigation timing, "
        "pest or disease risk, frost or heat stress at a sensitive stage. Don't repeat the crop "
        "details back unless it helps, and keep the general-guidance caveat brief. This context "
        "is written in English, but the LANGUAGE rule still applies: answer in the language AND "
        "script of the user's question (a question in Devanagari gets an answer in Devanagari)."
    )


def get_weather_tool_spec() -> ToolSpec:
    return {
        "name": "fetch_weather",
        "description": (
            "Fetch real, current weather data for a location from Open-Meteo. Always call "
            "this before stating any numeric weather value."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "City or place name, e.g. 'Mumbai'"},
                "parameter": {
                    "type": "string",
                    "enum": ["temperature", "precipitation", "wind", "general"],
                    "description": "Which aspect of the weather is most relevant to the question",
                },
            },
            "required": ["location"],
        },
    }


def _make_tool_executor(db: Session):
    def tool_executor(tool_name: str, tool_input: dict) -> dict:
        if tool_name != "fetch_weather":
            raise WeatherServiceError(f"Unknown tool requested: {tool_name}")

        location = tool_input.get("location")
        if not location:
            raise WeatherServiceError("No location provided to fetch_weather")

        geo = weather.geocode(db, location)
        forecast = weather.fetch_combined_forecast(db, geo)
        validation = weather.validate_forecast(forecast, tool_input.get("parameter", "general"))
        if not validation.ok:
            raise WeatherServiceError(f"Weather data failed validation: {validation.reason}")

        return forecast.model_dump(mode="json")

    return tool_executor


def run_generator(
    query: str,
    llm_client: LLMClient,
    db: Session,
    mismatch_hint: str | None = None,
    language: str | None = None,
    crop_context: CropContext | None = None,
) -> DraftAnswer:
    system_prompt = SYSTEM_PROMPT
    if crop_context:
        system_prompt += crop_context_prompt(crop_context)
    # The prompt already tells the model to mirror the user's language;
    # naming the detected one as well removes the ambiguity on short or
    # romanized queries, where "same language as the user" is a genuinely
    # harder call for the model to make than it looks.
    if language and language != "en":
        system_prompt += (
            f"\n\nThe user's question was detected as language code '{language}' - answer in it."
        )
        # Without this the model drifts into romanized Hindi on Devanagari
        # questions, most often when the English farmer context is present.
        if _DEVANAGARI_RE.search(query):
            system_prompt += (
                " The question is written in Devanagari script, so write the whole answer in "
                "Devanagari - not in Latin letters, and with no words from any other language."
            )
    if mismatch_hint:
        system_prompt += (
            f"\n\nNOTE: a previous draft of your answer failed verification for this reason: "
            f"'{mismatch_hint}'. Re-fetch the data and produce a corrected answer that only "
            f"states values you can see in the tool result."
        )

    result: GenerationResult = llm_client.generate_with_tools(
        system_prompt=system_prompt,
        user_query=query,
        tools=[get_weather_tool_spec()],
        tool_executor=_make_tool_executor(db),
        max_tool_iterations=3,
    )

    return DraftAnswer(
        text=result.draft_text,
        raw_tool_data=result.raw_tool_data,
        tool_call_count=result.tool_call_count,
    )
