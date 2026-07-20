"""baseline: trip_records as it exists in production today

This represents the PRE-EXISTING schema (created by the old
Base.metadata.create_all + _add_missing_columns hack). On a fresh DB, upgrade()
creates it. On the existing production DB, run `alembic stamp 0001` ONCE
instead of upgrade — the table already exists there.

Revision ID: 0001
Revises:
Create Date: 2026-07-18
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trip_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("system", sa.String(length=20), nullable=True, server_default="crew"),
        sa.Column("destination", sa.String(length=200), nullable=False),
        sa.Column("request", JSONB(), nullable=False),
        sa.Column("itinerary", JSONB(), nullable=False),
        sa.Column("within_budget", sa.Boolean(), nullable=True, server_default=sa.true()),
        sa.Column("elapsed_seconds", sa.Float(), nullable=True, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("client_id", sa.String(length=100), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("generation_config", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_trip_records_client_id", "trip_records", ["client_id"])


def downgrade() -> None:
    op.drop_index("ix_trip_records_client_id", table_name="trip_records")
    op.drop_table("trip_records")
