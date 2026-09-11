"""Fast path (spec Section 4): rule-based location/parameter extraction,
Open-Meteo fetch + validation, template fill. No generation LLM call.
"""
import re
from typing import Literal, Optional

from models.schemas import WeatherData

Parameter = Literal["temperature", "precipitation", "wind", "general"]

PARAM_KEYWORDS: list[tuple[re.Pattern, Parameter]] = [
    (re.compile(r"\b(temperature|temp|hot|cold|warm|cool|degrees?)\b", re.I), "temperature"),
    (re.compile(r"\b(rain|precipitation|drizzle|shower|snow)\b", re.I), "precipitation"),
    (re.compile(r"\b(wind|windy|breeze|gust)\b", re.I), "wind"),
]

# Trailing words that are not part of a location name.
_STOPWORDS = {"today", "tomorrow", "now", "please", "right now", "currently"}

_LOCATION_PREPOSITION = re.compile(
    r"\b(?:in|at|for|near)\s+([a-zA-Z][a-zA-Z\s]{1,40}?)(?=[?.,!]|\s+(?:today|tomorrow|now|this|next)\b|$)",
    re.I,
)
_TITLE_CASE_RUN = re.compile(r"\b([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*)\b")


def _strip_stopwords(location: str) -> str:
    words = [w for w in location.strip().split() if w.lower() not in _STOPWORDS]
    return " ".join(words).strip()


def extract_location_and_param(query: str) -> tuple[Optional[str], Parameter]:
    """Lightweight rule-based extraction (spec Section 4.1). Location
    extraction does NOT require capitalization, since voice-transcribed
    queries are frequently all-lowercase - relying on Title-Case as a
    hard requirement would silently break the voice input path.
    """
    parameter: Parameter = "general"
    for pattern, param in PARAM_KEYWORDS:
        if pattern.search(query):
            parameter = param
            break

    location: Optional[str] = None

    match = _LOCATION_PREPOSITION.search(query)
    if match:
        location = _strip_stopwords(match.group(1))

    if not location:
        title_matches = _TITLE_CASE_RUN.findall(query)
        if title_matches:
            location = _strip_stopwords(title_matches[-1])

    if not location:
        return None, parameter

    return location, parameter


_TEMPLATES = {
    "temperature": "The current temperature in {location} is {temp}°C.",
    "precipitation": "In {location}, current precipitation is {precip} mm, with a {precip_prob} chance in the next hours.",
    "wind": "Wind speed in {location} right now is {wind} m/s.",
    "general": (
        "In {location}: {temp}°C, precipitation {precip} mm, wind {wind} m/s. "
        "Data from Open-Meteo, fetched just now."
    ),
}


def build_answer(weather: WeatherData, parameter: Parameter) -> str:
    current = weather.current
    hourly = weather.hourly or {}
    precip_prob_list = hourly.get("precipitation_probability") or []

    values = {
        "location": weather.location,
        "temp": current.get("temperature_2m"),
        "precip": current.get("precipitation"),
        "precip_prob": f"{precip_prob_list[0]}%" if precip_prob_list else "unknown",
        "wind": current.get("wind_speed_10m"),
    }
    template = _TEMPLATES.get(parameter, _TEMPLATES["general"])
    answer = template.format(**values)

    # Only appended when the Google Weather cross-check succeeded (spec:
    # additive, never a hard dependency) - keeps the answer unchanged when
    # only Open-Meteo data is available.
    if weather.google and weather.best_estimate:
        note = _cross_check_note(current, weather.google, weather.best_estimate)
        if note:
            answer += f" {note}"
    return answer


def _cross_check_note(open_meteo_current: dict, google: dict, best: dict) -> str:
    parts = []
    if "temperature_c" in best:
        parts.append(
            f"best estimate {best['temperature_c']}°C "
            f"(Open-Meteo {open_meteo_current.get('temperature_2m')}°C, "
            f"Google {google.get('temperature_c')}°C)"
        )
    if "wind_speed_ms" in best:
        parts.append(f"wind best estimate {best['wind_speed_ms']} m/s")
    if not parts:
        return ""
    return "Cross-checked with Google Weather: " + "; ".join(parts) + "."
