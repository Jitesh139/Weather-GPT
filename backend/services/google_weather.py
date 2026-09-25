"""Google Weather API integration (weather.googleapis.com) - a second,
independent data source combined with Open-Meteo (see services/weather.py's
fetch_combined_forecast) to cross-check and blend a "best estimate". This
is purely additive: if GOOGLE_WEATHER_API_KEY is unset or this call fails,
Open-Meteo alone remains a fully grounded, valid answer - it is never a
hard dependency for a query to succeed.

Endpoint shape (URL, query params, response fields) confirmed against
Google's own currentConditions.lookup documentation
(developers.google.com/maps/documentation/weather/current-conditions).
"""
import logging
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config import settings

logger = logging.getLogger(__name__)

# One pooled client for the process: reusing the TLS connection cut each
# call from ~1.1s to ~0.2s against a fresh connection per request.
_http = httpx.Client(timeout=10.0)

CURRENT_CONDITIONS_URL = "https://weather.googleapis.com/v1/currentConditions:lookup"


class GoogleWeatherConfigError(Exception):
    """Raised when GOOGLE_WEATHER_API_KEY is not set."""


class GoogleWeatherError(Exception):
    """Non-retryable failure - bad response shape, invalid key, location not covered."""


class TransientGoogleWeatherError(Exception):
    """Retryable failure - network hiccup, upstream 5xx, timeout."""


def _raise_for_transient(exc: httpx.HTTPStatusError) -> None:
    if exc.response.status_code >= 500 or exc.response.status_code == 429:
        raise TransientGoogleWeatherError(str(exc)) from exc
    raise GoogleWeatherError(
        f"Google Weather request failed ({exc.response.status_code}): {exc.response.text}"
    ) from exc


@retry(
    retry=retry_if_exception_type(TransientGoogleWeatherError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
def _get_json(params: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
    try:
        response = _http.get(CURRENT_CONDITIONS_URL, params=params, headers=headers)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        _raise_for_transient(exc)
        raise  # unreachable, keeps type checkers happy
    except (httpx.TransportError, httpx.TimeoutException) as exc:
        raise TransientGoogleWeatherError(str(exc)) from exc


def fetch_current_conditions(latitude: float, longitude: float) -> dict[str, Any]:
    """Real-time current conditions from the Google Weather API, normalized
    to a flat dict of grounded numbers - never guessed/estimated.
    """
    if not settings.google_weather_api_key:
        raise GoogleWeatherConfigError("GOOGLE_WEATHER_API_KEY is not set")

    data = _get_json(
        params={
            "location.latitude": latitude,
            "location.longitude": longitude,
            "unitsSystem": "METRIC",
        },
        headers={
            "X-Goog-Api-Key": settings.google_weather_api_key,
        },
    )

    try:
        precip = data.get("precipitation") or {}
        wind = data.get("wind") or {}
        condition = ((data.get("weatherCondition") or {}).get("description") or {}).get("text")
        return {
            "temperature_c": data["temperature"]["degrees"],
            "feels_like_c": (data.get("feelsLikeTemperature") or {}).get("degrees"),
            "humidity_pct": data.get("relativeHumidity"),
            "precip_probability_pct": (precip.get("probability") or {}).get("percent"),
            "precip_qpf_mm": (precip.get("qpf") or {}).get("quantity"),
            "wind_speed_kmh": (wind.get("speed") or {}).get("value"),
            "wind_gust_kmh": (wind.get("gust") or {}).get("value"),
            "condition": condition,
        }
    except (KeyError, TypeError) as exc:
        raise GoogleWeatherError(f"Unexpected Google Weather response shape: {data}") from exc
