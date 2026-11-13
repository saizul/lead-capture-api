from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from leadcapture.db import Base, UTCDateTime, utcnow


def new_id() -> str:
    return str(uuid.uuid4())


class LeadStatus(str, Enum):
    new = "new"
    contacted = "contacted"
    won = "won"
    lost = "lost"


class OutboxStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    delivered = "delivered"
    dead = "dead"


class Form(Base):
    __tablename__ = "forms"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    fields: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    allowed_origins: Mapped[list[str]] = mapped_column(JSON, default=list)
    notifiers: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    honeypot_field: Mapped[str] = mapped_column(String(64), default="website_url")
    min_submit_seconds: Mapped[int] = mapped_column(Integer, default=3)
    require_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    spam_threshold: Mapped[int] = mapped_column(Integer, default=50)
    blocked_words: Mapped[list[str]] = mapped_column(JSON, default=list)
    rate_limit_per_ip: Mapped[int] = mapped_column(Integer, default=10)
    rate_limit_per_form: Mapped[int] = mapped_column(Integer, default=300)
    success_redirect_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    leads: Mapped[list[Lead]] = relationship(back_populates="form", passive_deletes=True)


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    form_id: Mapped[str] = mapped_column(ForeignKey("forms.id", ondelete="CASCADE"), index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True, index=True)
    consent: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    spam_score: Mapped[int] = mapped_column(Integer, default=0)
    is_spam: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    spam_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default=LeadStatus.new.value, index=True)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    form: Mapped[Form] = relationship(back_populates="leads")


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (Index("ix_outbox_due", "status", "next_attempt_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    notifier_type: Mapped[str] = mapped_column(String(32))
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default=OutboxStatus.pending.value)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=6)
    next_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    locked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class DeliveryLog(Base):
    __tablename__ = "delivery_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    outbox_id: Mapped[str] = mapped_column(
        ForeignKey("outbox_events.id", ondelete="CASCADE"), index=True
    )
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    notifier_type: Mapped[str] = mapped_column(String(32))
    attempt: Mapped[int] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(Boolean)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(12))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
