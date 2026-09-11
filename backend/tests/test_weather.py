"""Weather service tests against mocked and malformed Open-Meteo
responses (spec Section 12). The "real response" case is marked slow/
network and skipped by default so the suite runs offline.
"""
import httpx
import pytest
import respx

from services import weather
from services.weather import WeatherServiceError
from tests.conftest import SAMPLE_FORECAST_RESPONSE, SAMPLE_GEOCODE_RESPONSE


@respx.mock
def test_geocode_success(db_session):
    respx.get(weather.GEOCODING_URL).mock(return_value=httpx.Response(200, json=SAMPLE_GEOCODE_RESPONSE))
    result = weather.geocode(db_session, "Mumbai")
    assert result.location_name == "Mumbai"
    assert result.latitude == pytest.approx(19.076)


@respx.mock
def test_geocode_not_found_raises(db_session):
    respx.get(weather.GEOCODING_URL).mock(return_value=httpx.Response(200, json={"results": []}))
    with pytest.raises(WeatherServiceError):
        weather.geocode(db_session, "Nowhereatallxyz")


@respx.mock
def test_geocode_is_cached(db_session):
    route = respx.get(weather.GEOCODING_URL).mock(return_value=httpx.Response(200, json=SAMPLE_GEOCODE_RESPONSE))
    weather.geocode(db_session, "CacheTestCity")
    weather.geocode(db_session, "CacheTestCity")
    assert route.call_count == 1


@respx.mock
def test_fetch_forecast_success(db_session):
    respx.get(weather.FORECAST_URL).mock(return_value=httpx.Response(200, json=SAMPLE_FORECAST_RESPONSE))
    geo = weather.GeocodeResult(location_name="Mumbai", latitude=19.076, longitude=72.8777)
    forecast = weather.fetch_forecast(db_session, geo)
    assert forecast.current["temperature_2m"] == 29.5
    validation = weather.validate_forecast(forecast)
    assert validation.ok


@respx.mock
def test_fetch_forecast_missing_current_fails_validation(db_session):
    malformed = {k: v for k, v in SAMPLE_FORECAST_RESPONSE.items() if k != "current"}
    respx.get(weather.FORECAST_URL).mock(return_value=httpx.Response(200, json=malformed))
    geo = weather.GeocodeResult(location_name="Nowhere", latitude=1.0, longitude=1.0)
    forecast = weather.fetch_forecast(db_session, geo)
    validation = weather.validate_forecast(forecast)
    assert not validation.ok


@respx.mock
def test_fetch_forecast_null_temperature_fails_validation(db_session):
    malformed = {**SAMPLE_FORECAST_RESPONSE, "current": {**SAMPLE_FORECAST_RESPONSE["current"], "temperature_2m": None}}
    respx.get(weather.FORECAST_URL).mock(return_value=httpx.Response(200, json=malformed))
    geo = weather.GeocodeResult(location_name="Nowhere", latitude=2.0, longitude=2.0)
    forecast = weather.fetch_forecast(db_session, geo)
    validation = weather.validate_forecast(forecast)
    assert not validation.ok


@respx.mock
def test_fetch_forecast_out_of_range_temperature_fails_validation(db_session):
    malformed = {**SAMPLE_FORECAST_RESPONSE, "current": {**SAMPLE_FORECAST_RESPONSE["current"], "temperature_2m": 200.0}}
    respx.get(weather.FORECAST_URL).mock(return_value=httpx.Response(200, json=malformed))
    geo = weather.GeocodeResult(location_name="Nowhere", latitude=3.0, longitude=3.0)
    forecast = weather.fetch_forecast(db_session, geo)
    validation = weather.validate_forecast(forecast)
    assert not validation.ok
    assert "out of plausible range" in validation.reason


@respx.mock
def test_transient_failure_is_retried_and_recovers(db_session):
    route = respx.get(weather.FORECAST_URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json=SAMPLE_FORECAST_RESPONSE)]
    )
    geo = weather.GeocodeResult(location_name="RetryCity", latitude=4.0, longitude=4.0)
    forecast = weather.fetch_forecast(db_session, geo)
    assert forecast.current["temperature_2m"] == 29.5
    assert route.call_count == 2


@pytest.mark.skip(reason="Hits the real Open-Meteo network API - run manually, not part of offline CI")
def test_real_open_meteo_geocode(db_session):
    result = weather.geocode(db_session, "London")
    assert result.latitude != 0


def test_fetch_combined_forecast_without_google_key_matches_fetch_forecast(db_session, monkeypatch):
    monkeypatch.setattr(weather.settings, "google_weather_api_key", None)
    with respx.mock:
        respx.get(weather.FORECAST_URL).mock(return_value=httpx.Response(200, json=SAMPLE_FORECAST_RESPONSE))
        geo = weather.GeocodeResult(location_name="NoGoogleKeyCity", latitude=5.0, longitude=5.0)
        combined = weather.fetch_combined_forecast(db_session, geo)
    assert combined.google is None
    assert combined.best_estimate is None
    assert combined.current["temperature_2m"] == 29.5


def test_fetch_combined_forecast_blends_both_sources(db_session, monkeypatch):
    monkeypatch.setattr(weather.settings, "google_weather_api_key", "fake-key")
    google_conditions = {
        "temperature_c": 30.5,
        "feels_like_c": 31.0,
        "humidity_pct": 65,
        "precip_probability_pct": 10,
        "precip_qpf_mm": 0.0,
        "wind_speed_kmh": 14.4,  # 4.0 m/s
        "wind_gust_kmh": 20.0,
        "condition": "Sunny",
    }
    monkeypatch.setattr(
        weather.google_weather, "fetch_current_conditions", lambda lat, lon: google_conditions
    )
    with respx.mock:
        respx.get(weather.FORECAST_URL).mock(return_value=httpx.Response(200, json=SAMPLE_FORECAST_RESPONSE))
        geo = weather.GeocodeResult(location_name="BlendCity", latitude=6.0, longitude=6.0)
        combined = weather.fetch_combined_forecast(db_session, geo)

    assert combined.google == google_conditions
    # Open-Meteo temp 29.5, Google 30.5 -> best estimate 30.0
    assert combined.best_estimate["temperature_c"] == pytest.approx(30.0)
    # Open-Meteo wind 3.2 m/s, Google 14.4 km/h == 4.0 m/s -> best estimate 3.6
    assert combined.best_estimate["wind_speed_ms"] == pytest.approx(3.6)


def test_fetch_combined_forecast_falls_back_when_google_fails(db_session, monkeypatch):
    monkeypatch.setattr(weather.settings, "google_weather_api_key", "fake-key")

    def _raise(lat, lon):
        raise weather.google_weather.GoogleWeatherError("boom")

    monkeypatch.setattr(weather.google_weather, "fetch_current_conditions", _raise)
    with respx.mock:
        respx.get(weather.FORECAST_URL).mock(return_value=httpx.Response(200, json=SAMPLE_FORECAST_RESPONSE))
        geo = weather.GeocodeResult(location_name="GoogleFailsCity", latitude=7.0, longitude=7.0)
        combined = weather.fetch_combined_forecast(db_session, geo)

    assert combined.google is None
    assert combined.best_estimate is None
    assert combined.current["temperature_2m"] == 29.5
