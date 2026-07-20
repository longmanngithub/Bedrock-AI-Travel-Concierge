"""ORM models.

Beyond the original `TripRecord` (one row per generated trip), this now carries
the accounts + background-jobs schema: users, OAuth links, refresh-token
families, conversations/messages (chat history moved server-side off
localStorage), and jobs (the crew now runs in a background worker, not inline in
the request). Schema changes go through Alembic — see backend/alembic/.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Stored lowercased; unique across the system.
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    # NULL for OAuth-only accounts (no local password set).
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Personalization is OPT-IN (default off) — the defensible default.
    memory_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)
    # Completion email is enabled by default but can be disabled in Settings.
    completion_email_opt_in: Mapped[bool] = mapped_column(Boolean, default=True)
    # Browser-geolocation-derived origin for flight pricing — OPT-IN (default
    # off), same posture as memory_opt_in (precise location is more sensitive
    # than a preference summary, not less).
    location_share_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)
    # A Google-only user displays the verified provider image. A local account
    # may instead own an uploaded avatar object stored behind AvatarStorage.
    avatar_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    avatar_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    google_avatar_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    avatar_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set once the user completes the /start <code> deep-link flow with the
    # delivery bot (see delivery/telegram.py). NULL = not linked.
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OAuthAccount(Base):
    """A linked third-party identity. Separate table so a user may add Google
    later without disturbing their local password login."""

    __tablename__ = "oauth_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(40))                # "google"
    provider_account_id: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (UniqueConstraint("provider", "provider_account_id"),)


class RefreshToken(Base):
    """One row per issued refresh token. Rotation: each refresh revokes the
    presented token and issues a new one in the same `family_id`. Presenting a
    revoked token means it leaked -> the whole family is revoked."""

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    # sha256 of the opaque token; the raw value is never stored.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)


class EmailVerification(Base):
    """One-time activation codes. Only a salted server-side digest is stored."""

    __tablename__ = "email_verifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    purpose: Mapped[str] = mapped_column(String(32), default="registration")
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Conversations (chat history, moved server-side)
# ---------------------------------------------------------------------------
class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, index=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))          # user | assistant
    content: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(16), default="text")  # text | itinerary | error
    trip_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("trip_records.id", ondelete="SET NULL"), nullable=True
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    # Assistant-side UI metadata (suggested replies, response source, etc.).
    # Attribute deliberately avoids the reserved DeclarativeBase name `metadata`.
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Monotonic per conversation — makes ordering and edit-truncation exact.
    seq: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")

    __table_args__ = (UniqueConstraint("conversation_id", "seq"),)


class ResponseEmailDelivery(Base):
    """Exactly-once completion-notification audit keyed to an assistant message."""

    __tablename__ = "response_email_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), unique=True, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    provider_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Jobs (background crew runs)
# ---------------------------------------------------------------------------
class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    expired = "expired"   # worker died mid-run; reaped by the stale-job sweeper


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(40), default="crew_itinerary")
    status: Mapped[JobStatus] = mapped_column(
        SAEnum(JobStatus, name="job_status"), default=JobStatus.queued, index=True
    )
    # Client-supplied; unique index makes a double-submit a no-op at the DB
    # layer even if the arq _job_id dedupe races.
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)  # TripRequest + previous ref
    result_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("trip_records.id", ondelete="SET NULL"), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)  # NEVER an exc str
    # Internal only — the traceback. NEVER serialized into any response model.
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress: Mapped[dict] = mapped_column(JSONB, default=dict)  # last checklist snapshot
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Written on every progress event; the sweeper reaps `running` rows whose
    # heartbeat is older than a threshold (worker OOM/crash).
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


# ---------------------------------------------------------------------------
# Trip records (one row per generated itinerary)
# ---------------------------------------------------------------------------
class TripRecord(Base):
    __tablename__ = "trip_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    system: Mapped[str] = mapped_column(String(20), default="crew")
    destination: Mapped[str] = mapped_column(String(200))
    request: Mapped[dict] = mapped_column(JSONB)
    itinerary: Mapped[dict] = mapped_column(JSONB)
    within_budget: Mapped[bool] = mapped_column(default=True)
    elapsed_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Legacy opaque client id — kept for migrating anonymous localStorage trips
    # to a real account on first login (see routers/conversations import). NOT
    # an auth credential.
    client_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    # New ownership: once accounts exist, a trip belongs to a user + conversation.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    # Audit/reproducibility: which model + crew config produced this row.
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    generation_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # Outcome of each delivery attempt, keyed by channel — e.g.
    # {"email": {"status": "sent", "sent_at": "...", "to": "a@b.com"}}. Updated
    # in place by the deliver_itinerary_job worker; lets the UI show a
    # sent/failed state without a separate audit table.
    deliveries: Mapped[dict] = mapped_column(JSONB, default=dict)


# ---------------------------------------------------------------------------
# Agent memory (Phase 4; only ever populated for users with memory_opt_in=True)
# ---------------------------------------------------------------------------
class UserPreference(Base):
    """A single rolling summary per user, rebuilt after each completed trip —
    not a history of every trip, just enough signal (recurring interests,
    typical pace, typical daily spend) to bias the Personalization agent on
    the *next* trip. See delivery/memory.py."""

    __tablename__ = "user_preferences"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    interests: Mapped[list] = mapped_column(JSONB, default=list)
    pace: Mapped[str | None] = mapped_column(String(32), nullable=True)
    avg_budget_per_day: Mapped[float | None] = mapped_column(Float, nullable=True)
    trip_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
