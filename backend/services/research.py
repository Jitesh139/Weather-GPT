"""Researcher-dashboard data services: multi-model forecast comparison,
historical climate trends, and regional (Arabian Sea / Bay of Bengal)
context.

These are direct data-fetch-and-shape functions. No LLM is involved in
any of them - the statistics are arithmetic, and arithmetic is not
something to delegate to a language model. The one place this dashboard
touches the LLM pipeline is the optional regional narrative, which lives
in main.py and goes through the same generate-then-verify flow as every
other generated sentence in this product.

Caching reuses the existing TTL cache (cache/store.py) rather than
introducing a second mechanism, keyed per query shape.
"""
import logging
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any, Optional

from sqlalchemy.orm import Session

from cache.store import cache
from config import settings
from models.schemas import GeocodeResult
from services.weather import FORECAST_URL, _get_json, geocode

logger = logging.getLogger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# Past observations don't change, so they're cached far longer than the
# forecast TTL - same cache layer, different lifetime.
HISTORICAL_CACHE_TTL_SECONDS = 24 * 60 * 60

# Earliest date the Open-Meteo archive covers.
ARCHIVE_START = date(1940, 1, 1)


# Every identifier below was confirmed against the live API while building
# this - the docs list some names (e.g. "ecmwf_ifs", "meteo_france_seamless")
# that the API rejects. Re-probe before adding to this list; model
# availability changes.
FORECAST_MODELS: list[dict[str, str]] = [
    {"id": "ecmwf_ifs025", "label": "ECMWF IFS", "centre": "ECMWF"},
    {"id": "gfs_seamless", "label": "GFS", "centre": "NOAA"},
    {"id": "icon_seamless", "label": "ICON", "centre": "DWD"},
    {"id": "ukmo_seamless", "label": "UKMO", "centre": "Met Office"},
    {"id": "meteofrance_seamless", "label": "ARPEGE/AROME", "centre": "Météo-France"},
    {"id": "jma_seamless", "label": "JMA", "centre": "Japan Met Agency"},
    {"id": "gem_seamless", "label": "GEM", "centre": "Env. Canada"},
]
_MODEL_IDS = {m["id"] for m in FORECAST_MODELS}
DEFAULT_MODELS = ["ecmwf_ifs025", "gfs_seamless", "icon_seamless"]

COMPARE_PARAMETERS: list[dict[str, str]] = [
    {"id": "temperature_2m", "label": "Temperature", "unit": "°C"},
    {"id": "precipitation_probability", "label": "Precipitation probability", "unit": "%"},
    {"id": "wind_speed_10m", "label": "Wind speed", "unit": "m/s"},
]

# Historical: which archive variables back each selectable parameter, and
# which of them is the headline series.
HISTORICAL_PARAMETERS: dict[str, dict[str, Any]] = {
    "temperature": {
        "label": "Temperature",
        "unit": "°C",
        "daily_vars": ["temperature_2m_mean", "temperature_2m_max", "temperature_2m_min"],
        "primary": "temperature_2m_mean",
        "aggregate": "mean",
    },
    "precipitation": {
        "label": "Precipitation",
        "unit": "mm",
        "daily_vars": ["precipitation_sum"],
        "primary": "precipitation_sum",
        "aggregate": "sum",
    },
    "wind": {
        "label": "Wind speed (daily max)",
        "unit": "m/s",
        "daily_vars": ["wind_speed_10m_max"],
        "primary": "wind_speed_10m_max",
        "aggregate": "mean",
    },
}


class ResearchError(Exception):
    """Bad request to a research endpoint - an unknown model, parameter,
    or date range. Surfaced to the caller as a 400, never as a 500."""


# ---------------------------------------------------------------------
# Feature 1 - multi-model forecast comparison
# ---------------------------------------------------------------------


def compare_models(
    db: Session,
    location: str,
    models: Optional[list[str]] = None,
    forecast_days: int = 3,
) -> dict[str, Any]:
    """Same location, same time axis, one series per underlying model, so
    model divergence is visible rather than averaged away."""
    selected = models or DEFAULT_MODELS
    unknown = [m for m in selected if m not in _MODEL_IDS]
    if unknown:
        raise ResearchError(f"Unknown model(s): {', '.join(unknown)}")
    if not 1 <= forecast_days <= 14:
        raise ResearchError("forecast_days must be between 1 and 14")

    geo = geocode(db, location)
    model_list = ",".join(sorted(selected))
    cache_key = f"research:compare:{geo.latitude:.4f},{geo.longitude:.4f}:{forecast_days}:{model_list}"

    cached = cache.get(db, cache_key)
    if cached is not None:
        logger.info("model-comparison cache hit for %s", cache_key)
        return cached

    hourly_vars = ",".join(p["id"] for p in COMPARE_PARAMETERS)
    data = _get_json(
        FORECAST_URL,
        {
            "latitude": geo.latitude,
            "longitude": geo.longitude,
            "hourly": hourly_vars,
            "models": ",".join(selected),
            "wind_speed_unit": "ms",
            "timezone": "auto",
            "forecast_days": forecast_days,
        },
    )

    hourly = data.get("hourly") or {}
    # Open-Meteo suffixes each variable with the model id when several are
    # requested ("temperature_2m_gfs_seamless") - verified live.
    parameters = []
    for spec in COMPARE_PARAMETERS:
        series = []
        for model_id in selected:
            if len(selected) == 1:
                values = hourly.get(spec["id"])
            else:
                values = hourly.get(f"{spec['id']}_{model_id}")
            if values is None:
                continue
            series.append(
                {
                    "model": model_id,
                    "label": _model_label(model_id),
                    "values": values,
                }
            )
        if series:
            parameters.append({**spec, "series": series, "spread": _spread(series)})

    result = {
        "location": geo.location_name,
        "latitude": geo.latitude,
        "longitude": geo.longitude,
        "time": hourly.get("time") or [],
        "models": [m for m in FORECAST_MODELS if m["id"] in selected],
        "parameters": parameters,
        "forecast_days": forecast_days,
    }
    cache.set(db, cache_key, result, settings.cache_ttl_seconds)
    return result


def _model_label(model_id: str) -> str:
    for m in FORECAST_MODELS:
        if m["id"] == model_id:
            return m["label"]
    return model_id


def _spread(series: list[dict[str, Any]]) -> Optional[float]:
    """Largest disagreement between models at any single timestep - the
    number a researcher actually wants out of this chart."""
    columns = [s["values"] for s in series if s["values"]]
    if len(columns) < 2:
        return None
    widest = 0.0
    for values in zip(*columns):
        present = [v for v in values if v is not None]
        if len(present) >= 2:
            widest = max(widest, max(present) - min(present))
    return round(widest, 2)


# ---------------------------------------------------------------------
# Feature 2 - historical climate trend
# ---------------------------------------------------------------------


def historical_trend(
    db: Session,
    location: str,
    parameter: str = "temperature",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    aggregation: str = "monthly",
) -> dict[str, Any]:
    """Observed past data for a location, optionally aggregated, with a
    rolling average and summary statistics computed here in Python."""
    spec = HISTORICAL_PARAMETERS.get(parameter)
    if spec is None:
        raise ResearchError(
            f"Unknown parameter '{parameter}'. Choose one of: {', '.join(HISTORICAL_PARAMETERS)}"
        )
    if aggregation not in ("daily", "monthly", "yearly"):
        raise ResearchError("aggregation must be daily, monthly or yearly")

    # The archive lags real time by about five days.
    default_end = date.today() - timedelta(days=5)
    start = _parse_date(start_date) if start_date else default_end.replace(year=default_end.year - 5)
    end = _parse_date(end_date) if end_date else default_end
    if start > end:
        raise ResearchError("start_date must be before end_date")
    if start < ARCHIVE_START:
        raise ResearchError(f"The archive starts at {ARCHIVE_START.isoformat()}")

    geo = geocode(db, location)
    cache_key = (
        f"research:history:{geo.latitude:.4f},{geo.longitude:.4f}:"
        f"{parameter}:{start.isoformat()}:{end.isoformat()}"
    )

    daily = cache.get(db, cache_key)
    if daily is None:
        logger.info("historical cache miss for %s - calling Open-Meteo archive", cache_key)
        data = _get_json(
            ARCHIVE_URL,
            {
                "latitude": geo.latitude,
                "longitude": geo.longitude,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "daily": ",".join(spec["daily_vars"]),
                "wind_speed_unit": "ms",
                "timezone": "auto",
            },
        )
        daily = data.get("daily") or {}
        cache.set(db, cache_key, daily, HISTORICAL_CACHE_TTL_SECONDS)

    times: list[str] = daily.get("time") or []
    values: list[Optional[float]] = daily.get(spec["primary"]) or []

    labels, points = _aggregate(times, values, aggregation, spec["aggregate"])
    present = [v for v in points if v is not None]

    return {
        "location": geo.location_name,
        "latitude": geo.latitude,
        "longitude": geo.longitude,
        "parameter": parameter,
        "label": spec["label"],
        "unit": spec["unit"],
        "aggregation": aggregation,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "labels": labels,
        "values": points,
        "rolling_average": rolling_average(points, _rolling_window(aggregation)),
        "rolling_window": _rolling_window(aggregation),
        "stats": summary_stats(present),
        "observations": len(times),
    }


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ResearchError(f"Invalid date '{value}' - expected YYYY-MM-DD") from exc


def _rolling_window(aggregation: str) -> int:
    return {"daily": 30, "monthly": 12, "yearly": 5}[aggregation]


def _aggregate(
    times: list[str], values: list[Optional[float]], aggregation: str, how: str
) -> tuple[list[str], list[Optional[float]]]:
    """Daily observations collapsed to monthly/yearly buckets. Temperature
    averages; precipitation sums - taking the mean of daily rainfall
    totals would understate a month by roughly thirtyfold."""
    if aggregation == "daily":
        return times, values

    buckets: dict[str, list[float]] = {}
    for stamp, value in zip(times, values):
        if value is None:
            continue
        key = stamp[:7] if aggregation == "monthly" else stamp[:4]
        buckets.setdefault(key, []).append(value)

    labels = sorted(buckets)
    if how == "sum":
        points = [round(sum(buckets[k]), 2) for k in labels]
    else:
        points = [round(statistics.fmean(buckets[k]), 2) for k in labels]
    return labels, points


def rolling_average(values: list[Optional[float]], window: int) -> list[Optional[float]]:
    """Trailing mean over `window` points. Positions without a full window
    are None so the chart can simply not draw them, rather than showing a
    misleadingly jumpy line at the start of the range."""
    out: list[Optional[float]] = []
    buffer: list[float] = []
    for value in values:
        if value is not None:
            buffer.append(value)
        if len(buffer) > window:
            buffer.pop(0)
        out.append(round(statistics.fmean(buffer), 2) if len(buffer) == window else None)
    return out


def summary_stats(values: list[float]) -> dict[str, Optional[float]]:
    if not values:
        return {"mean": None, "min": None, "max": None, "count": 0}
    return {
        "mean": round(statistics.fmean(values), 2),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
        "count": len(values),
    }


# ---------------------------------------------------------------------
# Feature 3 - regional context (Arabian Sea / Bay of Bengal)
# ---------------------------------------------------------------------

# Fixed sampling points. Sea points are open-ocean coordinates well clear
# of the coast; the coastal cities are the landfall-relevant ones on each
# side. No geocoding needed - these are literal coordinates.
SEA_POINTS: list[dict[str, Any]] = [
    {"name": "Arabian Sea - North", "region": "arabian_sea", "latitude": 20.0, "longitude": 66.0},
    {"name": "Arabian Sea - Central", "region": "arabian_sea", "latitude": 15.5, "longitude": 68.5},
    {"name": "Arabian Sea - South", "region": "arabian_sea", "latitude": 10.0, "longitude": 71.0},
    {"name": "Bay of Bengal - North", "region": "bay_of_bengal", "latitude": 19.0, "longitude": 88.5},
    {"name": "Bay of Bengal - Central", "region": "bay_of_bengal", "latitude": 15.0, "longitude": 87.0},
    {"name": "Bay of Bengal - South", "region": "bay_of_bengal", "latitude": 10.5, "longitude": 85.0},
]

COASTAL_POINTS: list[dict[str, Any]] = [
    {"name": "Mumbai", "region": "west_coast", "latitude": 19.0760, "longitude": 72.8777},
    {"name": "Panaji", "region": "west_coast", "latitude": 15.4909, "longitude": 73.8278},
    {"name": "Kochi", "region": "west_coast", "latitude": 9.9312, "longitude": 76.2673},
    {"name": "Chennai", "region": "east_coast", "latitude": 13.0827, "longitude": 80.2707},
    {"name": "Visakhapatnam", "region": "east_coast", "latitude": 17.6868, "longitude": 83.2185},
    {"name": "Kolkata", "region": "east_coast", "latitude": 22.5726, "longitude": 88.3639},
]

# pressure_msl and wind gusts are the fields that actually say something
# about a developing system, so they're fetched here even though the
# conversational path doesn't use them.
_REGIONAL_CURRENT = "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m,wind_gusts_10m,pressure_msl"
_REGIONAL_DAILY = "precipitation_sum,wind_speed_10m_max,precipitation_probability_max"


def _fetch_point(point: dict[str, Any]) -> dict[str, Any]:
    data = _get_json(
        FORECAST_URL,
        {
            "latitude": point["latitude"],
            "longitude": point["longitude"],
            "current": _REGIONAL_CURRENT,
            "daily": _REGIONAL_DAILY,
            "wind_speed_unit": "ms",
            "timezone": "auto",
            "forecast_days": 3,
        },
    )
    return {**point, "current": data.get("current") or {}, "daily": data.get("daily") or {}}


def regional_context(db: Session) -> dict[str, Any]:
    """Current + short-range forecast for the sea sampling points and the
    coastal cities, side by side.

    This deliberately asserts nothing about causation. It fetches both
    regions and hands them over for the researcher to compare; any
    narrative text is generated separately and verified (see main.py).
    """
    cache_key = "research:regional-context"
    cached = cache.get(db, cache_key)
    if cached is not None:
        logger.info("regional-context cache hit")
        return cached

    points = SEA_POINTS + COASTAL_POINTS
    # Twelve independent HTTP calls; sequentially this is ~12s, so they go
    # out together. No Session touches these threads.
    with ThreadPoolExecutor(max_workers=len(points)) as pool:
        fetched = list(pool.map(_fetch_point, points))

    sea = [p for p in fetched if p["region"] in ("arabian_sea", "bay_of_bengal")]
    coastal = [p for p in fetched if p["region"] in ("west_coast", "east_coast")]

    result = {
        "sea_points": sea,
        "coastal_points": coastal,
        "lowest_pressure": _lowest_pressure(sea),
        "strongest_gust": _strongest_gust(sea),
        "fetched_points": len(fetched),
    }
    cache.set(db, cache_key, result, settings.cache_ttl_seconds)
    return result


# ---------------------------------------------------------------------
# Feature 4 - pinned cities for the Live Cloud View map
# ---------------------------------------------------------------------

# Literal coordinates, like the regional sampling points above - these are
# map pins, so the marker and the forecast behind it must agree exactly.
# Geocoding them would risk the two drifting apart.
MAP_CITIES: list[dict[str, Any]] = [
    {"id": "bhopal", "name": "Bhopal", "latitude": 23.2599, "longitude": 77.4126},
    {"id": "indore", "name": "Indore", "latitude": 22.7196, "longitude": 75.8577},
    {"id": "delhi", "name": "Delhi", "latitude": 28.6139, "longitude": 77.2090},
    {"id": "mumbai", "name": "Mumbai", "latitude": 19.0760, "longitude": 72.8777},
    {"id": "chennai", "name": "Chennai", "latitude": 13.0827, "longitude": 80.2707},
    {"id": "kolkata", "name": "Kolkata", "latitude": 22.5726, "longitude": 88.3639},
]


def map_cities(db: Session) -> dict[str, Any]:
    """Current conditions for every pinned city in one call.

    Fetched together rather than per marker click: six independent calls
    are ~6s sequentially but ~1s in parallel, and doing it upfront means
    opening a popup costs nothing.
    """
    cache_key = "research:map-cities"
    cached = cache.get(db, cache_key)
    if cached is not None:
        logger.info("map-cities cache hit")
        return cached

    with ThreadPoolExecutor(max_workers=len(MAP_CITIES)) as pool:
        fetched = list(pool.map(_fetch_point, MAP_CITIES))

    result = {"cities": fetched}
    cache.set(db, cache_key, result, settings.cache_ttl_seconds)
    return result


def _lowest_pressure(points: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Lowest mean-sea-level pressure across the sea points. Flagged
    because it's the field a researcher scans for first, not because the
    system is claiming a system is forming."""
    candidates = [p for p in points if p["current"].get("pressure_msl") is not None]
    if not candidates:
        return None
    point = min(candidates, key=lambda p: p["current"]["pressure_msl"])
    return {"name": point["name"], "value": point["current"]["pressure_msl"], "unit": "hPa"}


def _strongest_gust(points: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    candidates = [p for p in points if p["current"].get("wind_gusts_10m") is not None]
    if not candidates:
        return None
    point = max(candidates, key=lambda p: p["current"]["wind_gusts_10m"])
    return {"name": point["name"], "value": point["current"]["wind_gusts_10m"], "unit": "m/s"}


def geocode_point(name: str, latitude: float, longitude: float) -> GeocodeResult:
    return GeocodeResult(location_name=name, latitude=latitude, longitude=longitude)
