"""Stage 1 - Generator (spec Section 5.1). Must call the weather-fetch
tool mid-turn via real tool-use before making any numeric claim; this is
enforced by LLMClient.generate_with_tools (see llm_client.py), not just
prompted.
"""
import logging

from sqlalchemy.orm import Session

from models.schemas import DraftAnswer
from services import weather
from services.llm_client import GenerationResult, LLMClient, ToolSpec
from services.weather import WeatherServiceError

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are WeatherGPT's answer generator. You MUST call the fetch_weather tool to get "
    "real data BEFORE stating any temperature, precipitation, wind, or other numeric value. "
    "Never invent or estimate a number yourself - every number in your answer must come "
    "directly from the tool result. The tool result may include a 'google' block (a second, "
    "independent weather source) and a 'best_estimate' block (the two sources blended) - when "
    "present, prefer citing the best_estimate value and note that it's cross-checked against "
    "both Open-Meteo and Google Weather. If the tool reports an error (e.g. location not found), "
    "say so plainly instead of guessing a value. Keep the answer conversational and concise, "
    "suitable for being spoken aloud. If the question involves crop/agricultural advice, "
    "explicitly caveat that this is general guidance, not a verified agronomic recommendation."
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


def run_generator(query: str, llm_client: LLMClient, db: Session, mismatch_hint: str | None = None) -> DraftAnswer:
    system_prompt = SYSTEM_PROMPT
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
