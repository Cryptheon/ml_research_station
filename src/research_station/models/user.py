"""Per-user data models.

Each user has their own pins, collections, notebooks, watches, chats, and
ingest history.  The default user_id "default" is used when auth is disabled.
"""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .paper import Base

# ── Pydantic schemas ─────────────────────────────────────────────────────────


class User(BaseModel):
    id: str
    display_name: str = "Researcher"
    email: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Pin(BaseModel):
    user_id: str
    paper_id: str
    note: str | None = None
    pinned_at: datetime = Field(default_factory=datetime.utcnow)


class Collection(BaseModel):
    id: int | None = None
    user_id: str
    name: str
    description: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class CollectionItem(BaseModel):
    collection_id: int
    paper_id: str
    added_at: datetime = Field(default_factory=datetime.utcnow)


class Watch(BaseModel):
    """Saved ingest interest set — triggers periodic re-pulls."""

    id: int | None = None
    user_id: str
    name: str | None = None
    interests: list[str]
    sources: list[str] = []
    active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_run_at: datetime | None = None


class ChatMessage(BaseModel):
    id: int | None = None
    chat_id: str
    role: str
    content: str
    thinking: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Chat(BaseModel):
    id: str
    user_id: str
    paper_id: str | None = None
    title: str = "New conversation"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class IngestHistory(BaseModel):
    id: int | None = None
    user_id: str
    interests: list[str]
    sources: list[str]
    found: int = 0
    scanned: int = 0
    duration_seconds: float = 0.0
    ran_at: datetime = Field(default_factory=datetime.utcnow)


# ── SQLAlchemy ORM ────────────────────────────────────────────────────────────


class UserORM(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False, default="Researcher")
    email: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class PinORM(Base):
    __tablename__ = "pins"

    user_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    paper_id: Mapped[str] = mapped_column(String(200), primary_key=True, index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    pinned_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class CollectionORM(Base):
    __tablename__ = "collections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class CollectionItemORM(Base):
    __tablename__ = "collection_items"

    collection_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("collections.id", ondelete="CASCADE"), primary_key=True
    )
    paper_id: Mapped[str] = mapped_column(String(200), primary_key=True, index=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class WatchORM(Base):
    __tablename__ = "watches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    interests_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    sources_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def to_pydantic(self) -> Watch:
        return Watch(
            id=self.id,
            user_id=self.user_id,
            name=self.name,
            interests=json.loads(self.interests_json),
            sources=json.loads(self.sources_json),
            active=self.active,
            created_at=self.created_at,
            last_run_at=self.last_run_at,
        )


class ManuallyAddedPaperORM(Base):
    """Tracks papers added one-by-one via POST /ingest/paper."""

    __tablename__ = "manually_added_papers"

    paper_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class IngestHistoryORM(Base):
    __tablename__ = "ingest_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    interests_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    sources_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scanned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_seconds: Mapped[float] = mapped_column(nullable=False, default=0.0)
    ran_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    paper_ids_json: Mapped[str] = mapped_column(Text, nullable=True, default="[]")

    def to_pydantic(self) -> IngestHistory:
        return IngestHistory(
            id=self.id,
            user_id=self.user_id,
            interests=json.loads(self.interests_json),
            sources=json.loads(self.sources_json),
            found=self.found,
            scanned=self.scanned,
            duration_seconds=self.duration_seconds,
            ran_at=self.ran_at,
        )


class ChatORM(Base):
    __tablename__ = "chats"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    paper_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="New conversation")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list[ChatMessageORM]] = relationship(
        "ChatMessageORM",
        back_populates="chat",
        cascade="all, delete-orphan",
        order_by="ChatMessageORM.id",
    )

    def to_dict(self, message_count: int = 0, last_message: str | None = None) -> dict:
        return {
            "id": self.id,
            "paper_id": self.paper_id,
            "title": self.title,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "message_count": message_count,
            "last_message": last_message,
        }


class PaperNoteORM(Base):
    __tablename__ = "paper_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    paper_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="user"
    )  # "user" | "agent"
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ChatMessageORM(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("chats.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    thinking: Mapped[str | None] = mapped_column(Text, nullable=True)
    images_json: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # JSON list of base64 data URIs
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    chat: Mapped[ChatORM] = relationship("ChatORM", back_populates="messages")

    def to_dict(self) -> dict:
        import json as _json

        d: dict = {
            "id": self.id,
            "role": self.role,
            "text": self.content,
            "thinking": self.thinking or None,
            "created_at": self.created_at.isoformat(),
        }
        if self.images_json:
            try:
                d["images"] = _json.loads(self.images_json)
            except Exception:
                pass
        return d
