"""delivery + agent memory: user_preferences, telegram link, delivery log

Adds the Phase 4 schema: a per-user `user_preferences` row built from past
trips (only ever populated when `users.memory_opt_in` is true), the Telegram
chat id a user's account links to for delivery, and a `deliveries` JSONB
column on trip_records recording the outcome of each email/Telegram send
attempt (keyed by channel) so the UI can show "sent"/"failed" without a
separate audit table.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-19
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True))
    op.create_unique_constraint("uq_users_telegram_chat_id", "users", ["telegram_chat_id"])

    op.add_column(
        "trip_records",
        sa.Column("deliveries", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )

    op.create_table(
        "user_preferences",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("interests", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("pace", sa.String(length=32), nullable=True),
        sa.Column("avg_budget_per_day", sa.Float(), nullable=True),
        sa.Column("trip_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", name="uq_user_preferences_user_id"),
    )


def downgrade() -> None:
    op.drop_table("user_preferences")
    op.drop_column("trip_records", "deliveries")
    op.drop_constraint("uq_users_telegram_chat_id", "users", type_="unique")
    op.drop_column("users", "telegram_chat_id")
