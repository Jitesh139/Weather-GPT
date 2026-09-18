"""Language detection and localized FAST-path output.

The bug behind these: a question asked in Hindi came back answered in
English. Three separate things caused it, all fixed - the Google ASR
returned an English translation instead of what was said (covered in
test_google_voice_client.py), the generator was never told to mirror the
user's language, and the FAST path had only English templates.
"""
import pytest

from models.schemas import WeatherData
from services import fast_path
from services.language import detect_language, message


@pytest.mark.parametrize(
    "query",
    [
        "आज का मौसम कैसा है",                       # Devanagari
        "भोपाल में बारिश होगी क्या",
        "Aaj Ka Mausam kaisa hai Bhopal mein",      # romanized, as voice returns it
        "aaj ka mausam kaisa hai",
        "bhopal mein barish hogi kya",
        "kal garmi kitni rahegi",
        "mumbai ka temperature batao",
    ],
)
def test_detects_hindi_in_both_scripts(query):
    assert detect_language(query) == "hi"


@pytest.mark.parametrize(
    "query",
    [
        "What's the weather in Bhopal these days?",
        "will it rain in Pune tomorrow",
        "temperature in Mumbai",
        # "me" and "in" are in the weak Hindi list; an English sentence
        # using them must not be misread as Hindi.
        "tell me the weather in Delhi",
        "",
    ],
)
def test_english_is_not_misdetected(query):
    assert detect_language(query) == "en"


def _weather() -> WeatherData:
    return WeatherData(
        location="Bhopal",
        latitude=23.25,
        longitude=77.41,
        current={"temperature_2m": 27.6, "precipitation": 0.0, "wind_speed_10m": 2.39},
        hourly={"precipitation_probability": [10]},
    )


def test_fast_path_answers_in_hindi():
    answer = fast_path.build_answer(_weather(), "temperature", "hi")
    assert "तापमान" in answer
    assert "27.6" in answer          # the number itself is never translated
    assert "Bhopal" in answer        # nor is the place name
    assert "The current temperature" not in answer


def test_fast_path_still_answers_in_english_by_default():
    assert fast_path.build_answer(_weather(), "temperature") == (
        "The current temperature in Bhopal is 27.6°C."
    )


def test_fast_path_falls_back_to_english_for_an_untranslated_language():
    answer = fast_path.build_answer(_weather(), "temperature", "fr")
    assert answer == "The current temperature in Bhopal is 27.6°C."


def test_cross_check_note_is_localized():
    weather = _weather()
    weather.google = {"temperature_c": 28.0}
    weather.best_estimate = {"temperature_c": 27.8}

    hindi = fast_path.build_answer(weather, "temperature", "hi")
    assert "मिलान किया गया" in hindi
    assert "27.8" in hindi
    assert "Cross-checked" not in hindi


def test_error_messages_are_localized_with_english_fallback():
    assert "शहर" in message("no_location", "hi")
    assert "please specify a city" in message("no_location", "en")
    # A language with no catalog entry must still produce a usable message.
    assert "please specify a city" in message("no_location", "fr")
    assert "404" in message("fetch_failed", "hi", error="404")


# Routing. Hindi questions used to match none of the router's English
# FAST patterns, so even "bhopal mein mausam kaisa hai" - a plain lookup -
# paid a full two-stage LLM round trip, about ten seconds, for an answer
# the template path returns from cache in milliseconds.
from router.query_classifier import classify_with_reason  # noqa: E402


@pytest.mark.parametrize(
    "query,expected_location",
    [
        ("Aaj Ka Mausam kaisa hai Bhopal mein", "Bhopal"),
        ("bhopal mein aaj ka mausam kaisa hai", "bhopal"),
        ("indore mein temperature kitna hai", "indore"),
        ("mumbai mein barish hogi", "mumbai"),
    ],
)
def test_simple_hindi_lookups_take_the_fast_path(query, expected_location):
    path, reason = classify_with_reason(query)
    assert path == "fast", reason
    location, _ = fast_path.extract_location_and_param(query)
    assert location == expected_location


def test_hindi_question_without_a_location_falls_back_to_slow():
    """It must not route FAST and answer 'please specify a city' to a
    question the SLOW path could have handled."""
    path, reason = classify_with_reason("aaj ka mausam kaisa hai")
    assert path == "slow"
    assert reason == "hi_pattern_without_location"


def test_hindi_reasoning_words_still_force_the_slow_path():
    path, reason = classify_with_reason("bhopal me kal kheti ke liye salah chahiye")
    assert path == "slow"
    assert reason.startswith("slow_keyword_hi")


@pytest.mark.parametrize(
    "query,expected",
    [
        ("What's the weather in Bhopal?", "fast"),
        ("Should I water my crops in Bhopal tomorrow?", "slow"),
        ("will it rain in Pune", "fast"),
        ("why is it so humid in Chennai", "slow"),
    ],
)
def test_english_routing_is_unchanged(query, expected):
    assert classify_with_reason(query)[0] == expected


def test_postposition_extraction_ignores_non_place_words():
    # "abhi mein" must not be geocoded as the city "abhi".
    location, _ = fast_path.extract_location_and_param("abhi mein mausam kaisa hai")
    assert location is None
