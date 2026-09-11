"""Persistence layer tests (spec Section 12): round-trip insert/read for
all four tables, plus cache-expiry behavior on cached_forecast.
"""
from datetime import datetime, timedelta, timezone

from cache.store import TTLCache
from db.models import CachedForecast, QueryLog, UserPreference, VerificationLog


def test_query_log_round_trip(db_session):
    row = QueryLog(query_text="weather in Pune", path="fast", latency_total_ms=120, verified=True)
    db_session.add(row)
    db_session.commit()

    fetched = db_session.query(QueryLog).filter_by(id=row.id).one()
    assert fetched.query_text == "weather in Pune"
    assert fetched.path == "fast"
    assert fetched.verified is True


def test_verification_log_round_trip(db_session):
    query_row = QueryLog(query_text="should i carry an umbrella", path="slow", latency_total_ms=800, verified=True)
    db_session.add(query_row)
    db_session.commit()

    verification_row = VerificationLog(
        query_log_id=query_row.id,
        draft_answer="Yes, bring an umbrella.",
        source_data_snapshot={"current": {"precipitation": 5.0}},
        passed=True,
        attempt_number=1,
    )
    db_session.add(verification_row)
    db_session.commit()

    fetched = db_session.query(VerificationLog).filter_by(query_log_id=query_row.id).one()
    assert fetched.passed is True
    assert fetched.source_data_snapshot["current"]["precipitation"] == 5.0


def test_cached_forecast_round_trip(db_session):
    now = datetime.now(timezone.utc)
    row = CachedForecast(
        location_key="19.0760,72.8777",
        parameter=None,
        data={"current": {"temperature_2m": 29.5}},
        fetched_at=now,
        expires_at=now + timedelta(minutes=15),
    )
    db_session.add(row)
    db_session.commit()

    fetched = db_session.query(CachedForecast).filter_by(location_key="19.0760,72.8777").one()
    assert fetched.data["current"]["temperature_2m"] == 29.5


def test_user_preference_round_trip(db_session):
    row = UserPreference(client_id="anon-123", language="en-IN", default_location="Mumbai")
    db_session.add(row)
    db_session.commit()

    fetched = db_session.query(UserPreference).filter_by(client_id="anon-123").one()
    assert fetched.language == "en-IN"
    assert fetched.default_location == "Mumbai"


def test_ttl_cache_treats_expired_row_as_miss(db_session):
    now = datetime.now(timezone.utc)
    expired_row = CachedForecast(
        location_key="expired-key",
        parameter=None,
        data={"current": {"temperature_2m": 10.0}},
        fetched_at=now - timedelta(hours=1),
        expires_at=now - timedelta(minutes=1),
    )
    db_session.add(expired_row)
    db_session.commit()

    fresh_cache = TTLCache()
    assert fresh_cache.get(db_session, "expired-key") is None


def test_ttl_cache_hits_on_unexpired_row(db_session):
    now = datetime.now(timezone.utc)
    row = CachedForecast(
        location_key="fresh-key",
        parameter=None,
        data={"current": {"temperature_2m": 22.0}},
        fetched_at=now,
        expires_at=now + timedelta(minutes=15),
    )
    db_session.add(row)
    db_session.commit()

    fresh_cache = TTLCache()
    result = fresh_cache.get(db_session, "fresh-key")
    assert result is not None
    assert result["current"]["temperature_2m"] == 22.0


def test_ttl_cache_set_persists_across_new_cache_instance(db_session):
    cache_a = TTLCache()
    cache_a.set(db_session, "persist-key", {"current": {"temperature_2m": 18.0}}, ttl_seconds=900)

    cache_b = TTLCache()  # simulates a fresh process - only the DB layer is shared
    result = cache_b.get(db_session, "persist-key")
    assert result is not None
    assert result["current"]["temperature_2m"] == 18.0
