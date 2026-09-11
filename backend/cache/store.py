"""TTL cache for geocoding + forecast lookups. In-memory dict for the hot
path, backed by the `cached_forecast` Postgres table so the cache
survives a process restart (spec: "cache before you call" + "persist,
don't just log to console").
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from db.models import CachedForecast

GEOCODE_SENTINEL = "__geocode__"


class TTLCache:
    def __init__(self) -> None:
        self._mem: dict[tuple[str, Optional[str]], tuple[dict[str, Any], datetime]] = {}

    def get(self, db: Session, key: str, parameter: Optional[str] = None) -> Optional[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        cache_key = (key, parameter)

        mem_entry = self._mem.get(cache_key)
        if mem_entry is not None and mem_entry[1] > now:
            return mem_entry[0]

        row = (
            db.query(CachedForecast)
            .filter(
                CachedForecast.location_key == key,
                CachedForecast.parameter == parameter,
                CachedForecast.expires_at > now,
            )
            .order_by(CachedForecast.fetched_at.desc())
            .first()
        )
        if row is not None:
            expires_at = row.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            self._mem[cache_key] = (row.data, expires_at)
            return row.data

        return None

    def set(
        self,
        db: Session,
        key: str,
        data: dict[str, Any],
        ttl_seconds: int,
        parameter: Optional[str] = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=ttl_seconds)
        cache_key = (key, parameter)

        self._mem[cache_key] = (data, expires_at)

        row = CachedForecast(
            location_key=key,
            parameter=parameter,
            data=data,
            fetched_at=now,
            expires_at=expires_at,
        )
        db.add(row)
        db.commit()


# Module-level singleton - the in-memory half of the cache needs to
# persist across requests within one process, not be recreated per call.
cache = TTLCache()
