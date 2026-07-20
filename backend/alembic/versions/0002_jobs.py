"""jobs table + trip_records.job_id

The crew now runs in a background worker; a Job row tracks its lifecycle.
user_id/conversation_id are plain nullable UUID columns here (no FK yet — the
users/conversations tables arrive in 0003, where the FK constraints are added).

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-18
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# create_type=False: we create/drop the type EXPLICITLY below (with checkfirst),
# so create_table must not also try to CREATE TYPE (that races to a duplicate).
_JOB_STATUS = ENUM(
    "queued", "running", "succeeded", "failed", "cancelled", "expired",
    name="job_status", create_type=False,
)


def upgrade() -> None:
    _JOB_STATUS.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(length=40), nullable=False, server_default="crew_itinerary"),
        sa.Column("status", _JOB_STATUS, nullable=False, server_default="queued"),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("conversation_id", UUID(as_uuid=True), nullable=True),
        sa.Column("payload", JSONB(), nullable=False, server_default="{}"),
        sa.Column("result_record_id", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=40), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("progress", JSONB(), nullable=False, server_default="{}"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["result_record_id"], ["trip_records.id"], ondelete="SET NULL"),
    )
    op.create_unique_constraint("uq_jobs_idempotency_key", "jobs", ["idempotency_key"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_user_id", "jobs", ["user_id"])
    op.create_index("ix_jobs_conversation_id", "jobs", ["conversation_id"])
    op.create_index("ix_jobs_heartbeat_at", "jobs", ["heartbeat_at"])

    op.add_column("trip_records", sa.Column("job_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_trip_records_job_id", "trip_records", "jobs", ["job_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    op.drop_constraint("fk_trip_records_job_id", "trip_records", type_="foreignkey")
    op.drop_column("trip_records", "job_id")
    op.drop_index("ix_jobs_heartbeat_at", table_name="jobs")
    op.drop_index("ix_jobs_conversation_id", table_name="jobs")
    op.drop_index("ix_jobs_user_id", table_name="jobs")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_constraint("uq_jobs_idempotency_key", "jobs", type_="unique")
    op.drop_table("jobs")
    _JOB_STATUS.drop(op.get_bind(), checkfirst=True)
