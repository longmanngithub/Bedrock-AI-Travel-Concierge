"""Conversation + message routes — chat history moved server-side off
localStorage, scoped to the authenticated user. Includes a one-shot import
endpoint for migrating a browser's existing local conversations on first login.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from starlette.requests import Request
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..auth.cookies import verify_csrf
from ..auth.deps import current_user
from ..database import get_db
from ..errors import AppError, ErrorCode
from ..models import Conversation, Message, TripRecord, User

router = APIRouter(prefix="/conversations", tags=["conversations"])


# --- schemas ----------------------------------------------------------------
class ConversationView(BaseModel):
    id: uuid.UUID
    title: str
    created_at: str
    updated_at: str


class QuickReplyView(BaseModel):
    label: str
    value: str


class MessageView(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    kind: str
    seq: int
    quick_replies: list[QuickReplyView] = Field(default_factory=list)
    trip_record_id: int | None
    # Set only while kind=="job" — lets a reloaded conversation find a still-
    # running (or orphaned) background job and reconnect to its SSE stream.
    # The worker resolves kind to "result"/"error" in place once the job
    # settles, so this is never populated on any other message kind.
    job_id: uuid.UUID | None
    created_at: str
    # Hydrated from the referenced TripRecord when kind == "result" — lets a
    # reloaded conversation re-render the boarding-pass card and rebuild the
    # replan fact-recap without a separate round trip per message.
    itinerary: dict | None = None
    trip_request: dict | None = None
    elapsed_seconds: float | None = None


class CreateConversationBody(BaseModel):
    title: str = Field(default="", max_length=200)


class AppendMessageBody(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(default="", max_length=100_000)
    kind: str = Field(default="text", max_length=16)
    trip_record_id: int | None = None


class PatchConversationBody(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    archived: bool | None = None


class ImportMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(default="", max_length=100_000)
    kind: str = Field(default="text", max_length=16)


class ImportConversation(BaseModel):
    title: str = Field(default="", max_length=200)
    messages: list[ImportMessage] = Field(default_factory=list, max_length=500)


class ImportBody(BaseModel):
    conversations: list[ImportConversation] = Field(default_factory=list, max_length=200)
    client_id: str | None = Field(default=None, max_length=100)


def _conv_view(c: Conversation) -> ConversationView:
    return ConversationView(
        id=c.id, title=c.title,
        created_at=c.created_at.isoformat(), updated_at=c.updated_at.isoformat(),
    )


def _owned(conv_id: uuid.UUID, user: User, db: Session) -> Conversation:
    conv = db.get(Conversation, conv_id)
    if conv is None or conv.user_id != user.id:
        raise AppError(ErrorCode.E_NOT_FOUND, log_detail="conversation not found/owned")
    return conv


def _next_seq(db: Session, conv_id: uuid.UUID) -> int:
    current = db.scalar(select(func.max(Message.seq)).where(Message.conversation_id == conv_id))
    return (current or 0) + 1


def _quick_replies(message: Message) -> list[QuickReplyView]:
    raw = (message.extra or {}).get("quick_replies", [])
    if not isinstance(raw, list):
        return []
    out: list[QuickReplyView] = []
    for item in raw[:4]:
        if isinstance(item, dict) and isinstance(item.get("label"), str) and isinstance(item.get("value"), str):
            out.append(QuickReplyView(label=item["label"], value=item["value"]))
    return out


# --- routes -----------------------------------------------------------------
@router.get("", response_model=list[ConversationView])
def list_conversations(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[ConversationView]:
    rows = db.scalars(
        select(Conversation)
        .where(Conversation.user_id == user.id, Conversation.archived_at.is_(None))
        .order_by(Conversation.updated_at.desc())
    ).all()
    return [_conv_view(c) for c in rows]


@router.post("", response_model=ConversationView)
def create_conversation(
    body: CreateConversationBody, request: Request,
    user: User = Depends(current_user), db: Session = Depends(get_db),
) -> ConversationView:
    verify_csrf(request)
    conv = Conversation(user_id=user.id, title=(body.title.strip() or "New chat")[:200])
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return _conv_view(conv)


@router.get("/{conv_id}/messages", response_model=list[MessageView])
def get_messages(
    conv_id: uuid.UUID, before: int | None = Query(None),
    user: User = Depends(current_user), db: Session = Depends(get_db),
) -> list[MessageView]:
    _owned(conv_id, user, db)
    stmt = select(Message).where(Message.conversation_id == conv_id)
    if before is not None:
        stmt = stmt.where(Message.seq < before)
    stmt = stmt.order_by(Message.seq.asc())
    rows = db.scalars(stmt).all()

    record_ids = {m.trip_record_id for m in rows if m.trip_record_id is not None}
    records = {}
    if record_ids:
        # Ownership-filtered: `trip_record_id` on a message ultimately traces
        # back to client input (see append_message), so this must never trust
        # it enough to hydrate and leak another user's itinerary.
        for r in db.scalars(
            select(TripRecord).where(TripRecord.id.in_(record_ids), TripRecord.user_id == user.id)
        ).all():
            records[r.id] = r

    out = []
    for m in rows:
        record = records.get(m.trip_record_id) if m.trip_record_id is not None else None
        out.append(MessageView(
            id=m.id, role=m.role, content=m.content, kind=m.kind, seq=m.seq,
            quick_replies=_quick_replies(m),
            trip_record_id=m.trip_record_id, job_id=m.job_id, created_at=m.created_at.isoformat(),
            itinerary=record.itinerary if record else None,
            trip_request=record.request if record else None,
            elapsed_seconds=record.elapsed_seconds if record else None,
        ))
    return out


@router.post("/{conv_id}/messages", response_model=MessageView)
def append_message(
    conv_id: uuid.UUID, body: AppendMessageBody, request: Request,
    user: User = Depends(current_user), db: Session = Depends(get_db),
) -> MessageView:
    verify_csrf(request)
    conv = _owned(conv_id, user, db)
    # `trip_record_id` is client-supplied — never trust it enough to link a
    # message to an itinerary that isn't this user's own, or a later fetch
    # would hydrate and leak someone else's trip.
    record = None
    if body.trip_record_id is not None:
        candidate = db.get(TripRecord, body.trip_record_id)
        if candidate is not None and candidate.user_id == user.id:
            record = candidate
    msg = Message(
        conversation_id=conv_id, role=body.role, content=body.content,
        kind=body.kind, trip_record_id=record.id if record else None, seq=_next_seq(db, conv_id),
    )
    db.add(msg)
    conv.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(msg)
    return MessageView(
        id=msg.id, role=msg.role, content=msg.content, kind=msg.kind, seq=msg.seq,
        quick_replies=_quick_replies(msg),
        trip_record_id=msg.trip_record_id, job_id=msg.job_id, created_at=msg.created_at.isoformat(),
        itinerary=record.itinerary if record else None,
        trip_request=record.request if record else None,
        elapsed_seconds=record.elapsed_seconds if record else None,
    )


@router.delete("/{conv_id}/messages/{message_id}")
def truncate_from_message(
    conv_id: uuid.UUID, message_id: uuid.UUID, request: Request,
    user: User = Depends(current_user), db: Session = Depends(get_db),
) -> dict:
    """Delete `message_id` and every later message in the conversation (by
    `seq`) — backs the edit-a-past-message and regenerate flows, which both
    need to drop a tail of history and replay from an earlier point."""
    verify_csrf(request)
    conv = _owned(conv_id, user, db)
    target = db.get(Message, message_id)
    if target is None or target.conversation_id != conv_id:
        raise AppError(ErrorCode.E_NOT_FOUND, log_detail="message not found in conversation")
    db.execute(delete(Message).where(Message.conversation_id == conv_id, Message.seq >= target.seq))
    conv.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True}


@router.patch("/{conv_id}", response_model=ConversationView)
def patch_conversation(
    conv_id: uuid.UUID, body: PatchConversationBody, request: Request,
    user: User = Depends(current_user), db: Session = Depends(get_db),
) -> ConversationView:
    verify_csrf(request)
    conv = _owned(conv_id, user, db)
    if body.title is not None:
        conv.title = body.title[:200]
    if body.archived is not None:
        conv.archived_at = datetime.now(timezone.utc) if body.archived else None
    db.commit()
    db.refresh(conv)
    return _conv_view(conv)


@router.delete("/{conv_id}")
def delete_conversation(
    conv_id: uuid.UUID, request: Request,
    user: User = Depends(current_user), db: Session = Depends(get_db),
) -> dict:
    verify_csrf(request)
    conv = _owned(conv_id, user, db)
    db.delete(conv)
    db.commit()
    return {"ok": True}


@router.delete("")
def clear_all(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Delete every conversation for the user (the sidebar 'Clear All')."""
    verify_csrf(request)
    db.execute(delete(Conversation).where(Conversation.user_id == user.id))
    db.commit()
    return {"ok": True}


@router.post("/import", response_model=list[ConversationView])
def import_conversations(
    body: ImportBody, request: Request,
    user: User = Depends(current_user), db: Session = Depends(get_db),
) -> list[ConversationView]:
    """One-shot migration of a browser's localStorage conversations. Also claims
    any unowned trip_records created under the given client_id."""
    verify_csrf(request)
    created: list[Conversation] = []
    for ic in body.conversations:
        conv = Conversation(user_id=user.id, title=(ic.title or "")[:200])
        db.add(conv)
        db.flush()
        for i, m in enumerate(ic.messages, start=1):
            db.add(Message(
                conversation_id=conv.id, role=m.role, content=m.content, kind=m.kind, seq=i,
            ))
        created.append(conv)

    # Best-effort claim of anonymous trips created by this browser.
    if body.client_id:
        db.query(TripRecord).filter(
            TripRecord.client_id == body.client_id, TripRecord.user_id.is_(None)
        ).update({TripRecord.user_id: user.id}, synchronize_session=False)

    db.commit()
    for c in created:
        db.refresh(c)
    return [_conv_view(c) for c in created]
