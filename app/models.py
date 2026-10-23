from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Identity, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SessionRow(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    language: Mapped[str] = mapped_column(String(8))
    map_id: Mapped[str] = mapped_column(String(64))
    map_version: Mapped[str] = mapped_column(String(32))
    client: Mapped[dict[str, Any]] = mapped_column(JSON)

    state: Mapped[str] = mapped_column(String(32))
    revision: Mapped[int] = mapped_column(Integer)

    destination_poi_id: Mapped[str | None] = mapped_column(String(64))
    confirmed_node_id: Mapped[str | None] = mapped_column(String(64))
    confirmed_heading_deg: Mapped[float | None]
    active_route_id: Mapped[str | None] = mapped_column(String(40))

    # Conversation slots the agent fills in (train type, transit card) and user preferences.
    slots: Mapped[dict[str, Any]] = mapped_column(JSON)
    preferences: Mapped[dict[str, Any]] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MessageRow(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    # Insertion order; both messages of one turn share the same transaction timestamp.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), unique=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    # What the client needs to redraw the turn after a reconnect: the full reply for the assistant,
    # the chosen POI and whether a photo was attached for the user.
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LocalizationRow(Base):
    __tablename__ = "localizations"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(32))
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RouteRow(Base):
    __tablename__ = "routes"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    body: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NavigationEventRow(Base):
    __tablename__ = "navigation_events"

    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    route_id: Mapped[str | None] = mapped_column(String(40))
    type: Mapped[str] = mapped_column(String(32))
    client_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    context: Mapped[dict[str, Any]] = mapped_column(JSON)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FeedbackRow(Base):
    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    route_id: Mapped[str | None] = mapped_column(String(40))
    rating: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(32))
    comment: Mapped[str] = mapped_column(Text)
    allow_image_retention: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdempotencyRow(Base):
    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    # NULL while the first request with this key is still running; set once it succeeded.
    status_code: Mapped[int | None] = mapped_column(Integer)
    body: Mapped[bytes | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
