"""Structured logging setup + durable per-request logging helpers (spec
Section 11). Console logging alone is explicitly insufficient per the
spec - these helpers write to Postgres so query/verification history
survives a restart, and are called directly from main.py's request
handler (not via a logging handler side-channel) so the write is part of
the request's own transaction.
"""
import logging
from typing import Optional

from sqlalchemy.orm import Session

from config import settings
from db.models import QueryLog, VerificationLog


def configure_logging() -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def log_query(
    db: Session,
    *,
    query_text: str,
    path: str,
    latency_total_ms: int,
    latency_fetch_ms: Optional[int] = None,
    latency_generation_ms: Optional[int] = None,
    latency_verification_ms: Optional[int] = None,
    final_answer: Optional[str] = None,
    verified: bool = False,
    error: Optional[str] = None,
) -> QueryLog:
    row = QueryLog(
        query_text=query_text,
        path=path,
        latency_total_ms=latency_total_ms,
        latency_fetch_ms=latency_fetch_ms,
        latency_generation_ms=latency_generation_ms,
        latency_verification_ms=latency_verification_ms,
        final_answer=final_answer,
        verified=verified,
        error=error,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def create_pending_query_log(db: Session, *, query_text: str, path: str) -> QueryLog:
    """Insert a placeholder row up front so verification_log rows (which
    have a non-null FK to query_log) have something to point at while the
    slow-path pipeline is still running. Finalized via update_query_log.
    """
    row = QueryLog(query_text=query_text, path=path, latency_total_ms=0, verified=False)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_query_log(
    db: Session,
    row: QueryLog,
    *,
    latency_total_ms: int,
    latency_fetch_ms: Optional[int] = None,
    latency_generation_ms: Optional[int] = None,
    latency_verification_ms: Optional[int] = None,
    final_answer: Optional[str] = None,
    verified: bool = False,
    error: Optional[str] = None,
) -> QueryLog:
    row.latency_total_ms = latency_total_ms
    row.latency_fetch_ms = latency_fetch_ms
    row.latency_generation_ms = latency_generation_ms
    row.latency_verification_ms = latency_verification_ms
    row.final_answer = final_answer
    row.verified = verified
    row.error = error
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def log_verification(
    db: Session,
    *,
    query_log_id,
    draft_answer: str,
    source_data_snapshot: dict,
    passed: bool,
    mismatch_detail: Optional[str] = None,
    attempt_number: int = 1,
) -> VerificationLog:
    row = VerificationLog(
        query_log_id=query_log_id,
        draft_answer=draft_answer,
        source_data_snapshot=source_data_snapshot,
        passed=passed,
        mismatch_detail=mismatch_detail,
        attempt_number=attempt_number,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
