"""Google Weather API client tests against mocked responses - shaped
exactly like the documented currentConditions.lookup response (spec
Section 12 pattern: offline, no real network/API key needed)."""
import httpx
import pytest
import respx

from services import google_weather
from services.google_weather import GoogleWeatherConfigError, GoogleWeatherError

SAMPLE_CURRENT_CONDITIONS = {
    "temperature": {"degrees": 13.7, "unit": "CELSIUS"},
    "feelsLikeTemperature": {"degrees": 13.1, "unit": "CELSIUS"},
    "relativeHumidity": 42,
    "precipitation": {
        "probability": {"percent": 0, "type": "RAIN"},
        "qpf": {"quantity": 0, "unit": "MILLIMETERS"},
    },
    "wind": {
        "direction": {"degrees": 335, "cardinal": "NORTH_NORTHWEST"},
        "speed": {"value": 8, "unit": "KILOMETERS_PER_HOUR"},
        "gust": {"value": 18, "unit": "KILOMETERS_PER_HOUR"},
    },
    "weatherCondition": {"description": {"text": "Clear", "languageCode": "en"}},
}


def test_fetch_current_conditions_raises_without_api_key(monkeypatch):
    monkeypatch.setattr(google_weather.settings, "google_weather_api_key", None)
    with pytest.raises(GoogleWeatherConfigError):
        google_weather.fetch_current_conditions(19.076, 72.8777)


@respx.mock
def test_fetch_current_conditions_success(monkeypatch):
    monkeypatch.setattr(google_weather.settings, "google_weather_api_key", "fake-key")
    respx.get(google_weather.CURRENT_CONDITIONS_URL).mock(
        return_value=httpx.Response(200, json=SAMPLE_CURRENT_CONDITIONS)
    )
    result = google_weather.fetch_current_conditions(19.076, 72.8777)
    assert result["temperature_c"] == 13.7
    assert result["wind_speed_kmh"] == 8
    assert result["condition"] == "Clear"


@respx.mock
def test_fetch_current_conditions_malformed_response_raises(monkeypatch):
    monkeypatch.setattr(google_weather.settings, "google_weather_api_key", "fake-key")
    respx.get(google_weather.CURRENT_CONDITIONS_URL).mock(return_value=httpx.Response(200, json={"unexpected": True}))
    with pytest.raises(GoogleWeatherError):
        google_weather.fetch_current_conditions(19.076, 72.8777)


@respx.mock
def test_transient_failure_is_retried_and_recovers(monkeypatch):
    monkeypatch.setattr(google_weather.settings, "google_weather_api_key", "fake-key")
    route = respx.get(google_weather.CURRENT_CONDITIONS_URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json=SAMPLE_CURRENT_CONDITIONS)]
    )
    result = google_weather.fetch_current_conditions(19.076, 72.8777)
    assert result["temperature_c"] == 13.7
    assert route.call_count == 2
