"""SQLAlchemy ORM models. Four tables per spec Section 8a - query_log,
verification_log, cached_forecast, user_preference. This is what makes
logging (Section 11) and caching (Section 6) durable across restarts,
rather than console-only / in-memory-only.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Generic cross-dialect types (Uuid, JSON) are used instead of
# postgresql.UUID/JSONB so the same models work against both the real
# Postgres service (docker-compose) and an in-memory SQLite DB in tests,
# without needing a live Postgres just to run the test suite.
UUID = Uuid
JSONB = JSON


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class QueryLog(Base):
    __tablename__ = "query_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(String(10), nullable=False)  # "fast" | "slow"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    latency_fetch_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_generation_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_verification_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_total_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    final_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    verification_logs: Mapped[list["VerificationLog"]] = relationship(back_populates="query_log")


class VerificationLog(Base):
    __tablename__ = "verification_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query_log_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("query_log.id"), nullable=False)
    draft_answer: Mapped[str] = mapped_column(Text, nullable=False)
    source_data_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    mismatch_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    query_log: Mapped["QueryLog"] = relationship(back_populates="verification_logs")


class CachedForecast(Base):
    """Backs both the forecast cache (10-15 min TTL) and, via the
    '__geocode__' sentinel parameter value, the long-TTL geocoding cache -
    reusing one table rather than adding a 5th, per spec's "minimum
    tables" framing.
    """

    __tablename__ = "cached_forecast"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    location_key: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    parameter: Mapped[str | None] = mapped_column(String(50), nullable=True)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)


class UserPreference(Base):
    """Minimal, forward-looking table per spec Section 8a - not read or
    written by the query pipeline yet. Keyed by an anonymous client id
    (browser localStorage) since no auth/session system exists elsewhere
    in this spec.
    """

    __tablename__ = "user_preference"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    language: Mapped[str] = mapped_column(String(20), default="en-IN")
    default_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
