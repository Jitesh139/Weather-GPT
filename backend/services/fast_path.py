"""Fast path (spec Section 4): rule-based location/parameter extraction,
Open-Meteo fetch + validation, template fill. No generation LLM call.
"""
import re
from typing import Literal, Optional

from models.schemas import WeatherData
from services.language import DEFAULT_LANGUAGE

Parameter = Literal["temperature", "precipitation", "wind", "general"]

PARAM_KEYWORDS: list[tuple[re.Pattern, Parameter]] = [
    (re.compile(r"\b(temperature|temp|hot|cold|warm|cool|degrees?)\b", re.I), "temperature"),
    (re.compile(r"\b(rain|precipitation|drizzle|shower|snow)\b", re.I), "precipitation"),
    (re.compile(r"\b(wind|windy|breeze|gust)\b", re.I), "wind"),
]

# Words that are never part of a location name, removed wherever they
# appear in a captured span.
_STOPWORDS = {"today", "tomorrow", "now", "please", "currently"}

# Time/qualifier words that trail a location in ordinary speech - "weather
# in Bhopal these days", "rain in Pune over the weekend". Trimmed off the
# end of a captured span one token at a time, so a multi-word tail is
# removed in full rather than leaving "Bhopal these" behind. The regex
# lookahead below can only stop at phrases it lists; this catches the rest,
# which matters most for voice input, where users ramble more than they
# type.
_TRAILING_NOISE = {
    "the", "a", "an", "this", "that", "these", "those", "next", "last", "coming", "upcoming",
    "right", "just", "at", "in", "on", "for", "over", "around", "about", "like",
    "day", "days", "week", "weeks", "weekend", "month", "months", "year", "years",
    "morning", "afternoon", "evening", "night", "tonight", "yesterday", "moment", "time",
    "season", "hour", "hours", "today", "tomorrow", "now", "currently", "please",
}
_LEADING_NOISE = {"the", "a", "an"}

_LOCATION_PREPOSITION = re.compile(
    r"\b(?:in|at|for|near)\s+([a-zA-Z][a-zA-Z\s]{1,40}?)"
    r"(?=[?.,!]|\s+(?:today|tomorrow|tonight|now|this|these|those|next|last|over|during)\b|$)",
    re.I,
)
_TITLE_CASE_RUN = re.compile(r"\b([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*)\b")

# Hindi puts the postposition after the place name ("bhopal mein"), the
# mirror image of the English preposition above. Without this, a
# lowercase voice transcript like "bhopal mein aaj ka mausam" has no
# extractable location at all - the Title-Case fallback needs capitals
# that speech-to-text doesn't provide.
_LOCATION_POSTPOSITION = re.compile(
    r"\b([a-zA-Z][a-zA-Z\s]{1,30}?)\s+(?:mein|me|mai|men|mein\b)\b",
    re.I,
)

# The Title-Case fallback below also matches the capitalised first word of
# a sentence, so "What's the weather?" would otherwise be sent to the
# geocoder as the place name "What" - producing "Could not find a location
# matching 'What'" instead of the honest "please specify a city".
_NON_LOCATION_WORDS = {
    "what", "whats", "how", "hows", "when", "where", "why", "which", "who",
    "is", "are", "am", "will", "would", "should", "could", "can", "do", "does", "did",
    "tell", "give", "show", "hey", "hi", "hello", "please", "thanks",
    "weather", "forecast", "temperature", "rain", "rainfall", "wind", "humidity", "climate",
    "i", "me", "my", "we", "you", "it",
}

# Hindi/Hinglish words that can sit right before a postposition without
# being the place name ("abhi mein", "yahan mein").
_ROMANIZED_NON_LOCATIONS = {
    "mausam", "mosam", "mausum", "aaj", "kal", "abhi", "yahan", "wahan",
    "barish", "baarish", "garmi", "sardi", "thand", "din", "raat", "hafte",
    "kitna", "kitni", "kya", "hai", "ka", "ki", "ke", "iss", "is", "us",
}


def _strip_stopwords(location: str) -> str:
    words = [w for w in location.strip().split() if w.lower() not in _STOPWORDS]
    while words and words[-1].lower() in _TRAILING_NOISE:
        words.pop()
    while words and words[0].lower() in _LEADING_NOISE:
        words.pop(0)
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
        candidates = [_strip_stopwords(m) for m in _TITLE_CASE_RUN.findall(query)]
        candidates = [c for c in candidates if c and c.lower() not in _NON_LOCATION_WORDS]
        if candidates:
            location = candidates[-1]

    if not location:
        # "bhopal mein ..." - take the last word before the postposition,
        # not the whole run, since the words ahead of it are usually the
        # rest of the question ("aaj ka mausam bhopal mein").
        post = _LOCATION_POSTPOSITION.search(query)
        if post:
            words = [w for w in post.group(1).split() if w.lower() not in _NON_LOCATION_WORDS]
            candidate = _strip_stopwords(words[-1]) if words else ""
            if candidate and candidate.lower() not in _ROMANIZED_NON_LOCATIONS:
                location = candidate

    if not location:
        return None, parameter

    return location, parameter


# One template set per language the FAST path can answer in. The SLOW
# path needs nothing here - its LLM is told to reply in the user's own
# language, so it covers every language without a table (see
# services/language.py for why the two paths differ).
_TEMPLATES = {
    "en": {
        "temperature": "The current temperature in {location} is {temp}°C.",
        "precipitation": "In {location}, current precipitation is {precip} mm, with a {precip_prob} chance in the next hours.",
        "wind": "Wind speed in {location} right now is {wind} m/s.",
        "general": (
            "In {location}: {temp}°C, precipitation {precip} mm, wind {wind} m/s. "
            "Data from Open-Meteo, fetched just now."
        ),
    },
    "hi": {
        "temperature": "{location} में अभी तापमान {temp}°C है।",
        "precipitation": "{location} में अभी {precip} मिमी वर्षा हो रही है, और आने वाले घंटों में {precip_prob} संभावना है।",
        "wind": "{location} में अभी हवा की गति {wind} मीटर/सेकंड है।",
        "general": (
            "{location} में: {temp}°C, वर्षा {precip} मिमी, हवा {wind} मीटर/सेकंड। "
            "यह डेटा Open-Meteo से अभी लिया गया है।"
        ),
    },
}


def build_answer(weather: WeatherData, parameter: Parameter, language: str = DEFAULT_LANGUAGE) -> str:
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
    templates = _TEMPLATES.get(language, _TEMPLATES[DEFAULT_LANGUAGE])
    template = templates.get(parameter, templates["general"])
    answer = template.format(**values)

    # Only appended when the Google Weather cross-check succeeded (spec:
    # additive, never a hard dependency) - keeps the answer unchanged when
    # only Open-Meteo data is available.
    if weather.google and weather.best_estimate:
        note = _cross_check_note(current, weather.google, weather.best_estimate, language)
        if note:
            answer += f" {note}"
    return answer


_CROSS_CHECK = {
    "en": {
        "temperature": "best estimate {best}°C (Open-Meteo {open_meteo}°C, Google {google}°C)",
        "wind": "wind best estimate {best} m/s",
        "prefix": "Cross-checked with Google Weather: ",
        "suffix": ".",
    },
    "hi": {
        "temperature": "अनुमान {best}°C (Open-Meteo {open_meteo}°C, Google {google}°C)",
        "wind": "हवा का अनुमान {best} मीटर/सेकंड",
        "prefix": "Google Weather से मिलान किया गया: ",
        "suffix": "।",
    },
}


def _cross_check_note(open_meteo_current: dict, google: dict, best: dict, language: str = DEFAULT_LANGUAGE) -> str:
    phrases = _CROSS_CHECK.get(language, _CROSS_CHECK[DEFAULT_LANGUAGE])
    parts = []
    if "temperature_c" in best:
        parts.append(
            phrases["temperature"].format(
                best=best["temperature_c"],
                open_meteo=open_meteo_current.get("temperature_2m"),
                google=google.get("temperature_c"),
            )
        )
    if "wind_speed_ms" in best:
        parts.append(phrases["wind"].format(best=best["wind_speed_ms"]))
    if not parts:
        return ""
    return phrases["prefix"] + "; ".join(parts) + phrases["suffix"]
