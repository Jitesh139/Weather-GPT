"""initial schema - query_log, verification_log, cached_forecast, user_preference

Revision ID: 0001
Revises:
Create Date: 2026-09-10

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "query_log",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("path", sa.String(length=10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latency_fetch_ms", sa.Integer(), nullable=True),
        sa.Column("latency_generation_ms", sa.Integer(), nullable=True),
        sa.Column("latency_verification_ms", sa.Integer(), nullable=True),
        sa.Column("latency_total_ms", sa.Integer(), nullable=False),
        sa.Column("final_answer", sa.Text(), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
    )

    op.create_table(
        "verification_log",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("query_log_id", sa.Uuid(), sa.ForeignKey("query_log.id"), nullable=False),
        sa.Column("draft_answer", sa.Text(), nullable=False),
        sa.Column("source_data_snapshot", sa.JSON(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("mismatch_detail", sa.Text(), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "cached_forecast",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("location_key", sa.String(length=255), nullable=False),
        sa.Column("parameter", sa.String(length=50), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_cached_forecast_location_key", "cached_forecast", ["location_key"])
    op.create_index("ix_cached_forecast_expires_at", "cached_forecast", ["expires_at"])

    op.create_table(
        "user_preference",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("client_id", sa.String(length=255), nullable=False, unique=True),
        sa.Column("language", sa.String(length=20), nullable=False),
        sa.Column("default_location", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_user_preference_client_id", "user_preference", ["client_id"])


def downgrade() -> None:
    op.drop_table("user_preference")
    op.drop_index("ix_cached_forecast_expires_at", table_name="cached_forecast")
    op.drop_index("ix_cached_forecast_location_key", table_name="cached_forecast")
    op.drop_table("cached_forecast")
    op.drop_table("verification_log")
    op.drop_table("query_log")
