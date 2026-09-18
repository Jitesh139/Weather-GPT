"""Fast-path template tests, focused on the Google Weather cross-check
note added to services/fast_path.py - additive only, must not change the
answer when Google data isn't present.
"""
from models.schemas import WeatherData
from services import fast_path


def _weather(**overrides) -> WeatherData:
    base = dict(
        location="Mumbai",
        latitude=19.076,
        longitude=72.8777,
        current={"temperature_2m": 29.5, "precipitation": 0.0, "wind_speed_10m": 3.2},
        hourly={"precipitation_probability": [10, 15]},
    )
    base.update(overrides)
    return WeatherData(**base)


def test_build_answer_unchanged_without_google_data():
    weather = _weather()
    answer = fast_path.build_answer(weather, "general")
    assert "Cross-checked" not in answer
    assert "29.5" in answer


def test_build_answer_appends_cross_check_note_when_google_present():
    weather = _weather(google={"temperature_c": 30.5}, best_estimate={"temperature_c": 30.0})
    answer = fast_path.build_answer(weather, "general")
    assert "Cross-checked with Google Weather" in answer
    assert "best estimate 30.0" in answer
    assert "Open-Meteo 29.5" in answer
    assert "Google 30.5" in answer


def test_build_answer_no_note_when_google_present_but_best_estimate_empty():
    # e.g. Google succeeded but didn't report a comparable temperature.
    weather = _weather(google={"condition": "Clear"}, best_estimate={})
    answer = fast_path.build_answer(weather, "general")
    assert "Cross-checked" not in answer


# Location extraction. The regression these start from: "weather in Bhopal
# these days" was geocoded as the place name "Bhopal these days", which
# Open-Meteo has never heard of, so a perfectly ordinary question failed
# with "Could not find a location matching 'Bhopal these days'".
import pytest  # noqa: E402


@pytest.mark.parametrize(
    "query,expected",
    [
        ("What's the weather in Bhopal these days?", "Bhopal"),
        ("what's the weather in bhopal these days", "bhopal"),
        ("How is the weather in Mumbai today?", "Mumbai"),
        ("weather in new delhi right now", "new delhi"),
        ("what's it like in Pune over the weekend", "Pune"),
        ("weather in Chennai this week", "Chennai"),
        ("temperature in Kolkata tomorrow morning", "Kolkata"),
        ("weather in Jaipur at the moment", "Jaipur"),
        # Multi-word place names must survive the trimming.
        ("weather in New York these days", "New York"),
        # No preposition - the Title-Case fallback still works.
        ("Bhopal weather", "Bhopal"),
    ],
)
def test_extract_location_trims_trailing_time_phrases(query, expected):
    location, _ = fast_path.extract_location_and_param(query)
    assert location == expected


@pytest.mark.parametrize(
    "query",
    [
        "what is the weather",
        # Capitalised question words must not be geocoded as a place.
        "What's the weather?",
        "How hot is it today?",
        "weather in the morning",
    ],
)
def test_extract_location_returns_none_when_no_place_named(query):
    location, _ = fast_path.extract_location_and_param(query)
    assert location is None


@pytest.mark.parametrize(
    "query,expected",
    [
        ("temperature in Bhopal these days", "temperature"),
        ("will it rain in Bhopal these days", "precipitation"),
        ("how windy is it in Bhopal these days", "wind"),
        ("weather in Bhopal these days", "general"),
    ],
)
def test_extract_parameter_unaffected_by_trailing_phrases(query, expected):
    _, parameter = fast_path.extract_location_and_param(query)
    assert parameter == expected
