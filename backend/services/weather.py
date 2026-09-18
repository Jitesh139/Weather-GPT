"""Open-Meteo integration: geocoding + forecast, with retry-with-backoff
on transient failures and TTL caching backed by Postgres. No API key
required (spec Section 6).
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from sqlalchemy.orm import Session

from cache.store import GEOCODE_SENTINEL, cache
from config import settings
from models.schemas import GeocodeResult, ValidationResult, WeatherData
from services import google_weather

logger = logging.getLogger(__name__)

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_CACHE_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days - place names don't move

CURRENT_FIELDS = "temperature_2m,relative_humidity_2m,precipitation,weather_code,wind_speed_10m"
HOURLY_FIELDS = "temperature_2m,precipitation_probability,precipitation,weather_code"
DAILY_FIELDS = "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,weather_code"

TEMP_MIN_C, TEMP_MAX_C = -60.0, 60.0
WIND_MIN_MS, WIND_MAX_MS = 0.0, 150.0
PRECIP_MIN_MM, PRECIP_MAX_MM = 0.0, 1000.0


class WeatherServiceError(Exception):
    """Non-retryable failure - bad input, location not found, etc."""


class TransientWeatherError(Exception):
    """Retryable failure - network hiccup, upstream 5xx, timeout."""


def _raise_for_transient(exc: httpx.HTTPStatusError) -> None:
    if exc.response.status_code >= 500 or exc.response.status_code == 429:
        raise TransientWeatherError(str(exc)) from exc
    raise WeatherServiceError(f"Open-Meteo request failed: {exc}") from exc


@retry(
    retry=retry_if_exception_type(TransientWeatherError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
def _get_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        response = httpx.get(url, params=params, timeout=10.0)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        _raise_for_transient(exc)
        raise  # unreachable, keeps type checkers happy
    except (httpx.TransportError, httpx.TimeoutException) as exc:
        raise TransientWeatherError(str(exc)) from exc


def geocode(db: Session, location: str) -> GeocodeResult:
    """Resolve a place name to lat/long. Cached long-term (place names
    don't move) via the shared cached_forecast table's geocode sentinel.
    """
    cache_key = location.strip().lower()
    cached = cache.get(db, cache_key, parameter=GEOCODE_SENTINEL)
    if cached is not None:
        logger.info("geocode cache hit for %r", location)
        return GeocodeResult(**cached)

    logger.info("geocode cache miss for %r - calling Open-Meteo", location)
    data = _get_json(GEOCODING_URL, {"name": location, "count": 1, "language": "en", "format": "json"})

    results = data.get("results")
    if not results:
        raise WeatherServiceError(f"Could not find a location matching '{location}'")

    top = results[0]
    result = GeocodeResult(
        location_name=top.get("name", location),
        latitude=top["latitude"],
        longitude=top["longitude"],
    )
    cache.set(db, cache_key, result.model_dump(), GEOCODE_CACHE_TTL_SECONDS, parameter=GEOCODE_SENTINEL)
    return result


def _fetch_forecast_json(geo: GeocodeResult) -> dict[str, Any]:
    """The bare Open-Meteo call. Split out from fetch_forecast so it can be
    run on a worker thread without a Session in scope."""
    logger.info("forecast cache miss for %.4f,%.4f - calling Open-Meteo", geo.latitude, geo.longitude)
    return _get_json(
        FORECAST_URL,
        {
            "latitude": geo.latitude,
            "longitude": geo.longitude,
            "current": CURRENT_FIELDS,
            "hourly": HOURLY_FIELDS,
            "daily": DAILY_FIELDS,
            "wind_speed_unit": "ms",
            "timezone": "auto",
            "forecast_days": 3,
        },
    )


def _build_weather(geo: GeocodeResult, data: dict[str, Any]) -> WeatherData:
    return WeatherData(
        location=geo.location_name,
        latitude=geo.latitude,
        longitude=geo.longitude,
        current=data.get("current") or {},
        hourly=data.get("hourly"),
        daily=data.get("daily"),
    )


def fetch_forecast(db: Session, geo: GeocodeResult) -> WeatherData:
    """Fetch current + hourly/daily forecast for a resolved location.
    Cached ~10-15 min (settings.cache_ttl_seconds) per location.
    """
    cache_key = f"{geo.latitude:.4f},{geo.longitude:.4f}"
    cached = cache.get(db, cache_key)
    if cached is not None:
        logger.info("forecast cache hit for %s", cache_key)
        return WeatherData(**cached)

    weather = _build_weather(geo, _fetch_forecast_json(geo))
    cache.set(db, cache_key, weather.model_dump(mode="json"), settings.cache_ttl_seconds)
    return weather


def _best_estimate(open_meteo_current: dict[str, Any], google_current: dict[str, Any]) -> dict[str, Any]:
    """Blends the two independent, real sources into a single "best
    estimate" for the values both report - both inputs are grounded
    numbers from real API calls made this request, so averaging them is
    not an ungrounded guess. Precipitation is deliberately NOT averaged:
    Open-Meteo's `precipitation` is an instantaneous/last-hour reading
    while Google's `qpf` is a forecast quantity over a different window -
    they aren't the same measurement, so both are surfaced raw instead of
    blended into a number that would misrepresent precision.
    """
    best: dict[str, Any] = {}

    om_temp = open_meteo_current.get("temperature_2m")
    g_temp = google_current.get("temperature_c")
    if om_temp is not None and g_temp is not None:
        best["temperature_c"] = round((om_temp + g_temp) / 2, 1)

    om_wind = open_meteo_current.get("wind_speed_10m")
    g_wind_kmh = google_current.get("wind_speed_kmh")
    if om_wind is not None and g_wind_kmh is not None:
        best["wind_speed_ms"] = round((om_wind + g_wind_kmh / 3.6) / 2, 2)

    return best


def fetch_combined_forecast(db: Session, geo: GeocodeResult) -> WeatherData:
    """Open-Meteo (authoritative, cached, range-validated) combined with
    the Google Weather API as a second, independent source when
    configured. Additive only: if GOOGLE_WEATHER_API_KEY is unset or the
    Google call fails for any reason, this returns exactly what
    fetch_forecast would - Google is a cross-check, never a hard
    dependency for a query to succeed.

    The two providers are independent once the location is resolved, so on
    a cache miss they are fetched concurrently rather than one after the
    other - this used to be two sequential ~1s round trips on every cold
    query. Only the HTTP calls run off-thread: the cache reads and writes
    stay on the calling thread, because the SQLAlchemy Session backing
    them is not safe to share between threads.
    """
    forecast_key = f"{geo.latitude:.4f},{geo.longitude:.4f}"
    google_key = f"google:{geo.latitude:.4f},{geo.longitude:.4f}"

    cached_forecast = cache.get(db, forecast_key)
    use_google = bool(settings.google_weather_api_key)
    cached_google = cache.get(db, google_key) if use_google else None

    fetched_forecast: Optional[dict[str, Any]] = None
    fetched_google: Optional[dict[str, Any]] = None
    google_error: Optional[Exception] = None

    need_forecast = cached_forecast is None
    need_google = use_google and cached_google is None

    if need_forecast and need_google:
        with ThreadPoolExecutor(max_workers=2) as pool:
            forecast_future = pool.submit(_fetch_forecast_json, geo)
            google_future = pool.submit(google_weather.fetch_current_conditions, geo.latitude, geo.longitude)
            fetched_forecast = forecast_future.result()
            try:
                fetched_google = google_future.result()
            except Exception as exc:  # noqa: BLE001 - Google is additive
                google_error = exc
    elif need_forecast:
        fetched_forecast = _fetch_forecast_json(geo)
    elif need_google:
        try:
            fetched_google = google_weather.fetch_current_conditions(geo.latitude, geo.longitude)
        except Exception as exc:  # noqa: BLE001 - Google is additive
            google_error = exc

    if cached_forecast is not None:
        logger.info("forecast cache hit for %s", forecast_key)
        forecast = WeatherData(**cached_forecast)
    else:
        forecast = _build_weather(geo, fetched_forecast or {})
        cache.set(db, forecast_key, forecast.model_dump(mode="json"), settings.cache_ttl_seconds)

    if google_error is not None:
        # Never let Google's failure block an otherwise-valid Open-Meteo answer.
        logger.warning("Google Weather cross-check failed, using Open-Meteo only: %s", google_error)
        return forecast

    google_current = cached_google if cached_google is not None else fetched_google
    if google_current is None:
        return forecast
    if cached_google is None:
        cache.set(db, google_key, google_current, settings.cache_ttl_seconds)

    forecast.google = google_current
    forecast.best_estimate = _best_estimate(forecast.current, google_current) or None
    return forecast


def validate_forecast(weather: WeatherData, parameter: str = "general") -> ValidationResult:
    """Non-null + physically-plausible-range check (spec Section 4.4).
    Never lets malformed/out-of-range upstream data pass through silently.
    """
    current = weather.current
    if not current:
        return ValidationResult(ok=False, reason="No current-conditions data returned")

    temp = current.get("temperature_2m")
    wind = current.get("wind_speed_10m")
    precip = current.get("precipitation")

    if parameter in ("temperature", "general") and temp is None:
        return ValidationResult(ok=False, reason="Temperature value missing")
    if temp is not None and not (TEMP_MIN_C <= temp <= TEMP_MAX_C):
        return ValidationResult(ok=False, reason=f"Temperature {temp} out of plausible range")

    if parameter in ("wind", "general") and wind is None:
        return ValidationResult(ok=False, reason="Wind speed value missing")
    if wind is not None and not (WIND_MIN_MS <= wind <= WIND_MAX_MS):
        return ValidationResult(ok=False, reason=f"Wind speed {wind} out of plausible range")

    if parameter in ("precipitation", "general") and precip is None:
        return ValidationResult(ok=False, reason="Precipitation value missing")
    if precip is not None and not (PRECIP_MIN_MM <= precip <= PRECIP_MAX_MM):
        return ValidationResult(ok=False, reason=f"Precipitation {precip} out of plausible range")

    return ValidationResult(ok=True)
