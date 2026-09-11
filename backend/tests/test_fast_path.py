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
