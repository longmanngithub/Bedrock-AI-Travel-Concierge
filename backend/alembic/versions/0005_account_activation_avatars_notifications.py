"""activation OTPs, avatars, message UI metadata, response-email audit

Revision ID: 0005
Revises: 0004
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("completion_email_opt_in", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("users", sa.Column("avatar_source", sa.String(length=16), nullable=True))
    op.add_column("users", sa.Column("avatar_key", sa.String(length=512), nullable=True))
    op.add_column("users", sa.Column("google_avatar_url", sa.String(length=2048), nullable=True))
    op.add_column("users", sa.Column("avatar_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("messages", sa.Column("extra", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))

    op.create_table(
        "email_verifications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False, server_default="registration"),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("code_hash", name="uq_email_verifications_code_hash"),
    )
    op.create_index("ix_email_verifications_user_id", "email_verifications", ["user_id"])
    op.create_index("ix_email_verifications_code_hash", "email_verifications", ["code_hash"])
    op.create_index("ix_email_verifications_expires_at", "email_verifications", ["expires_at"])

    op.create_table(
        "response_email_deliveries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("message_id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provider_id", sa.String(length=255), nullable=True),
        sa.Column("error_code", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("message_id", name="uq_response_email_deliveries_message_id"),
    )
    op.create_index("ix_response_email_deliveries_user_id", "response_email_deliveries", ["user_id"])
    op.create_index("ix_response_email_deliveries_status", "response_email_deliveries", ["status"])


def downgrade() -> None:
    op.drop_index("ix_response_email_deliveries_status", table_name="response_email_deliveries")
    op.drop_index("ix_response_email_deliveries_user_id", table_name="response_email_deliveries")
    op.drop_table("response_email_deliveries")
    op.drop_index("ix_email_verifications_expires_at", table_name="email_verifications")
    op.drop_index("ix_email_verifications_code_hash", table_name="email_verifications")
    op.drop_index("ix_email_verifications_user_id", table_name="email_verifications")
    op.drop_table("email_verifications")
    op.drop_column("messages", "extra")
    op.drop_column("users", "avatar_updated_at")
    op.drop_column("users", "google_avatar_url")
    op.drop_column("users", "avatar_key")
    op.drop_column("users", "avatar_source")
    op.drop_column("users", "completion_email_opt_in")
