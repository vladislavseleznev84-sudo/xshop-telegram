from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; everything we store is UTC."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class Base(DeclarativeBase):
    pass


_DT = DateTime(timezone=True)


class AppState(Base):
    """Runtime switches editable from the bot / panel (JSON-encoded values)."""

    __tablename__ = "app_state"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class Connection(Base):
    """A Telegram Business connection (owner account <-> this bot)."""

    __tablename__ = "connections"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(BigInteger)
    owner_chat_id: Mapped[int] = mapped_column(BigInteger, default=0)
    owner_name: Mapped[str] = mapped_column(String(256), default="")
    can_reply: Mapped[bool] = mapped_column(Boolean, default=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(_DT, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(_DT, default=utcnow)


class Chat(Base):
    """One interlocutor inside one connection. Consent is per row."""

    __tablename__ = "chats"
    __table_args__ = (UniqueConstraint("connection_id", "tg_chat_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey("connections.id"))
    tg_chat_id: Mapped[int] = mapped_column(BigInteger)
    name_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    lang: Mapped[str] = mapped_column(String(8), default="ru")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # owner allowlist
    consent_status: Mapped[str] = mapped_column(String(16), default="none")  # none|pending|granted|declined|revoked
    consent_at: Mapped[datetime | None] = mapped_column(_DT, nullable=True)
    notice_sent_at: Mapped[datetime | None] = mapped_column(_DT, nullable=True)
    last_incoming_at: Mapped[datetime | None] = mapped_column(_DT, nullable=True)
    last_owner_message_at: Mapped[datetime | None] = mapped_column(_DT, nullable=True)
    created_at: Mapped[datetime] = mapped_column(_DT, default=utcnow)


class Message(Base):
    """Conversation content — exists ONLY for chats with granted consent, encrypted."""

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("chat_id", "tg_message_id", "role"),
        Index("ix_messages_chat_created", "chat_id", "created_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(Integer, index=True)
    tg_message_id: Mapped[int] = mapped_column(BigInteger)
    role: Mapped[str] = mapped_column(String(3))  # in | out
    content_enc: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(_DT, default=utcnow)


class Profile(Base):
    __tablename__ = "profiles"
    chat_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    data_enc: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(_DT, default=utcnow)


class SeenMessage(Base):
    """Dedup ledger: ids only, never content."""

    __tablename__ = "seen_messages"
    connection_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tg_chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tg_message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(_DT, default=utcnow)


class ProcessedUpdate(Base):
    __tablename__ = "processed_updates"
    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(_DT, default=utcnow)


class Token(Base):
    """One-time tokens: consent buttons and panel magic links."""

    __tablename__ = "tokens"
    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    chat_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(_DT)
    used_at: Mapped[datetime | None] = mapped_column(_DT, nullable=True)


class Event(Base):
    """Audit/stat trail. `detail` must never contain message content."""

    __tablename__ = "events"
    __table_args__ = (Index("ix_events_kind_ts", "kind", "ts"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(_DT, default=utcnow)
    kind: Mapped[str] = mapped_column(String(32))
    chat_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[str] = mapped_column(String(200), default="")


class Usage(Base):
    __tablename__ = "usage"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(_DT, default=utcnow)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    model: Mapped[str] = mapped_column(String(64), default="")
