"""Per-user endpoints — default single-user mode (no auth yet).

All /users/me/* routes operate on user_id="default", auto-created on first request.
"""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config.settings import get_settings
from ...models.paper import PaperORM
from ...models.user import (
    ChatORM,
    CollectionItemORM,
    CollectionORM,
    IngestHistoryORM,
    ManuallyAddedPaperORM,
    PaperNoteORM,
    PinORM,
    UserORM,
)
from ..deps import get_db
from ..schemas import PaperCard

router = APIRouter(prefix="/users/me", tags=["users"])

DEFAULT_USER = "default"


def _ensure_user(db: Session) -> UserORM:
    user = db.get(UserORM, DEFAULT_USER)
    if user is None:
        user = UserORM(id=DEFAULT_USER, display_name="Researcher")
        db.add(user)
        db.flush()
    return user


# ── User profile ──────────────────────────────────────────────────────────────


@router.get("")
def get_me(db: Session = Depends(get_db)) -> dict:
    user = _ensure_user(db)
    return {"id": user.id, "display_name": user.display_name, "email": user.email}


# ── Interests ─────────────────────────────────────────────────────────────────


class InterestsUpdate(BaseModel):
    phrases: list[str]


@router.get("/interests")
def get_interests(db: Session = Depends(get_db)) -> dict:
    # Use most recent ingest history for interests
    stmt = (
        select(IngestHistoryORM)
        .where(IngestHistoryORM.user_id == DEFAULT_USER)
        .order_by(IngestHistoryORM.ran_at.desc())
        .limit(5)
    )
    rows = db.execute(stmt).scalars().all()
    current = json.loads(rows[0].interests_json) if rows else []
    history = [json.loads(r.interests_json) for r in rows]
    return {"current": current, "history": history}


@router.put("/interests")
def update_interests(body: InterestsUpdate, db: Session = Depends(get_db)) -> dict:
    _ensure_user(db)
    orm = IngestHistoryORM(
        user_id=DEFAULT_USER,
        interests_json=json.dumps(body.phrases),
        sources_json="[]",
    )
    db.add(orm)
    return {"phrases": body.phrases}


# ── Routing prefs ─────────────────────────────────────────────────────────────


class RoutingUpdate(BaseModel):
    provider: str
    allow_api_fallback: bool = True


@router.put("/routing")
def update_routing(body: RoutingUpdate) -> dict:
    # Persist to settings at runtime (in-memory; restart resets)
    settings = get_settings()
    settings.llm.provider = body.provider
    return {"provider": body.provider, "allow_api_fallback": body.allow_api_fallback}


# ── Pins ──────────────────────────────────────────────────────────────────────


@router.get("/pins")
def list_pins(db: Session = Depends(get_db)) -> list[dict]:
    stmt = select(PinORM).where(PinORM.user_id == DEFAULT_USER)
    pins = db.execute(stmt).scalars().all()
    paper_ids = [p.paper_id for p in pins]
    papers = []
    for pid in paper_ids:
        orm = db.get(PaperORM, pid)
        if orm:
            papers.append(PaperCard.from_paper(orm.to_pydantic()).model_dump())
    return papers


@router.post("/pins/{paper_id:path}", status_code=201)
def pin_paper(paper_id: str, db: Session = Depends(get_db)) -> dict:
    _ensure_user(db)
    orm = db.get(PaperORM, paper_id)
    if orm is None:
        raise HTTPException(404, f"Paper '{paper_id}' not found")
    existing = db.get(PinORM, (DEFAULT_USER, paper_id))
    if existing is None:
        db.add(PinORM(user_id=DEFAULT_USER, paper_id=paper_id))
        orm.pinned = True
    return {"paper_id": paper_id, "pinned": True}


@router.delete("/pins/{paper_id:path}", status_code=204)
def unpin_paper(paper_id: str, db: Session = Depends(get_db)) -> None:
    existing = db.get(PinORM, (DEFAULT_USER, paper_id))
    if existing:
        db.delete(existing)
    orm = db.get(PaperORM, paper_id)
    if orm:
        # Only unpin global flag if no other user pins it (single-user: always)
        orm.pinned = False


# ── Collections ───────────────────────────────────────────────────────────────


class CollectionCreate(BaseModel):
    name: str
    swatch: str = "rust"


@router.get("/collections")
def list_collections(db: Session = Depends(get_db)) -> list[dict]:
    stmt = select(CollectionORM).where(CollectionORM.user_id == DEFAULT_USER)
    cols = db.execute(stmt).scalars().all()
    out = []
    for col in cols:
        item_stmt = (
            select(CollectionItemORM).where(CollectionItemORM.collection_id == col.id).limit(4)
        )
        items = db.execute(item_stmt).scalars().all()
        preview = []
        for item in items:
            p = db.get(PaperORM, item.paper_id)
            if p:
                preview.append(p.title)
        count_stmt = select(CollectionItemORM).where(CollectionItemORM.collection_id == col.id)
        count = len(db.execute(count_stmt).scalars().all())
        out.append(
            {
                "id": col.id,
                "name": col.name,
                "swatch": f"var(--{col.description or 'rust'})",
                "count": count,
                "preview": preview,
                "updated": col.created_at.isoformat(),
            }
        )
    return out


@router.post("/collections", status_code=201)
def create_collection(body: CollectionCreate, db: Session = Depends(get_db)) -> dict:
    _ensure_user(db)
    col = CollectionORM(user_id=DEFAULT_USER, name=body.name, description=body.swatch)
    db.add(col)
    db.flush()
    return {"id": col.id, "name": col.name}


@router.delete("/collections/{col_id}", status_code=204)
def delete_collection(
    col_id: int,
    delete_papers: bool = False,
    db: Session = Depends(get_db),
) -> None:
    col = db.get(CollectionORM, col_id)
    if col is None or col.user_id != DEFAULT_USER:
        raise HTTPException(404, "Collection not found")

    if delete_papers:
        item_stmt = select(CollectionItemORM).where(CollectionItemORM.collection_id == col_id)
        items = db.execute(item_stmt).scalars().all()
        for item in items:
            paper = db.get(PaperORM, item.paper_id)
            if paper:
                db.delete(paper)

    db.delete(col)


@router.post("/collections/{col_id}/papers/{paper_id:path}", status_code=201)
def add_to_collection(col_id: int, paper_id: str, db: Session = Depends(get_db)) -> dict:
    col = db.get(CollectionORM, col_id)
    if col is None or col.user_id != DEFAULT_USER:
        raise HTTPException(404, "Collection not found")
    existing = db.get(CollectionItemORM, (col_id, paper_id))
    if existing is None:
        db.add(CollectionItemORM(collection_id=col_id, paper_id=paper_id))
    return {"collection_id": col_id, "paper_id": paper_id}


# ── Notebooks ─────────────────────────────────────────────────────────────────


class NotebookCreate(BaseModel):
    title: str


@router.get("/notebooks")
def list_notebooks(db: Session = Depends(get_db)) -> list[dict]:
    # No NotebookORM yet — return empty list
    return []


@router.post("/notebooks", status_code=201)
def create_notebook(body: NotebookCreate) -> dict:
    return {"id": "nb-new", "title": body.title, "excerpt": "", "papers": 0, "notes": 0}


# ── Chat history ──────────────────────────────────────────────────────────────


@router.get("/chats")
def list_chats(paper_id: str | None = None, db: Session = Depends(get_db)) -> list[dict]:
    stmt = (
        select(ChatORM)
        .where(ChatORM.user_id == DEFAULT_USER)
        .order_by(ChatORM.updated_at.desc())
        .limit(100)
    )
    if paper_id is not None:
        stmt = stmt.where(ChatORM.paper_id == paper_id)
    chats = db.execute(stmt).scalars().all()
    result = []
    for chat in chats:
        msg_count = len(chat.messages)
        last_msg = chat.messages[-1].content[:120] if chat.messages else None
        result.append(chat.to_dict(message_count=msg_count, last_message=last_msg))
    return result


@router.delete("/chats/{chat_id}", status_code=204)
def delete_chat(chat_id: str, db: Session = Depends(get_db)) -> None:
    chat = db.get(ChatORM, chat_id)
    if chat and chat.user_id == DEFAULT_USER:
        db.delete(chat)
        db.commit()


# ── View history ──────────────────────────────────────────────────────────────


@router.post("/history/{paper_id:path}", status_code=204)
def record_view(paper_id: str) -> None:
    pass  # TODO: persist view events


@router.get("/history")
def view_history(limit: int = 20, db: Session = Depends(get_db)) -> list[dict]:
    return []


# ── Manually added papers ─────────────────────────────────────────────────────


@router.get("/manually-added")
def list_manually_added(db: Session = Depends(get_db)) -> list[dict]:
    stmt = select(ManuallyAddedPaperORM).order_by(ManuallyAddedPaperORM.added_at.desc())
    rows = db.execute(stmt).scalars().all()
    return [{"paper_id": r.paper_id, "added_at": r.added_at.isoformat()} for r in rows]


# ── Ingest history ────────────────────────────────────────────────────────────


@router.get("/ingests")
def list_ingests(limit: int = 5, db: Session = Depends(get_db)) -> list[dict]:
    stmt = (
        select(IngestHistoryORM)
        .where(IngestHistoryORM.user_id == DEFAULT_USER)
        .order_by(IngestHistoryORM.ran_at.desc())
        .limit(limit)
    )
    rows = db.execute(stmt).scalars().all()
    return [
        {
            "id": r.id,
            "interests": json.loads(r.interests_json),
            "sources": json.loads(r.sources_json),
            "found": r.found,
            "scanned": r.scanned,
            "duration_seconds": r.duration_seconds,
            "duration_ms": r.duration_seconds * 1000,
            "ran_at": r.ran_at.isoformat(),
            "paper_ids": json.loads(r.paper_ids_json or "[]"),
        }
        for r in rows
    ]


@router.delete("/ingests/{ingest_id}", status_code=204)
def delete_ingest(
    ingest_id: int,
    delete_papers: bool = False,
    db: Session = Depends(get_db),
) -> None:
    row = db.get(IngestHistoryORM, ingest_id)
    if row is None or row.user_id != DEFAULT_USER:
        return
    if delete_papers:
        for paper_id in json.loads(row.paper_ids_json or "[]"):
            paper = db.get(PaperORM, paper_id)
            if paper:
                db.delete(paper)
    db.delete(row)


# ── Library summary ───────────────────────────────────────────────────────────


@router.get("/library/summary")
def library_summary(db: Session = Depends(get_db)) -> dict:
    pin_stmt = select(PinORM).where(PinORM.user_id == DEFAULT_USER)
    pins = len(db.execute(pin_stmt).scalars().all())

    col_stmt = select(CollectionORM).where(CollectionORM.user_id == DEFAULT_USER)
    collections = len(db.execute(col_stmt).scalars().all())

    chat_stmt = select(ChatORM).where(ChatORM.user_id == DEFAULT_USER)
    chats = db.execute(chat_stmt).scalars().all()
    chat_count = len(chats)
    msg_total = sum(len(c.messages) for c in chats)

    return {
        "pins": pins,
        "papers_in_collections": 0,
        "collection_count": collections,
        "notebook_count": 0,
        "chat_count": chat_count,
        "chat_messages_total": msg_total,
    }


# ── Paper notes ──────────────────────────────────────────────────────────────


class NoteCreate(BaseModel):
    content: str
    source: str = "user"


class NoteUpdate(BaseModel):
    content: str


@router.get("/papers/{paper_id:path}/notes")
def list_notes(paper_id: str, db: Session = Depends(get_db)) -> list[dict]:
    stmt = (
        select(PaperNoteORM)
        .where(PaperNoteORM.paper_id == paper_id, PaperNoteORM.user_id == DEFAULT_USER)
        .order_by(PaperNoteORM.created_at.asc())
    )
    rows = db.execute(stmt).scalars().all()
    return [
        {
            "id": r.id,
            "content": r.content,
            "source": r.source,
            "created_at": r.created_at.isoformat(),
            "updated_at": r.updated_at.isoformat(),
        }
        for r in rows
    ]


@router.post("/papers/{paper_id:path}/notes", status_code=201)
def create_note(paper_id: str, body: NoteCreate, db: Session = Depends(get_db)) -> dict:
    _ensure_user(db)
    note = PaperNoteORM(
        paper_id=paper_id,
        user_id=DEFAULT_USER,
        content=body.content.strip(),
        source=body.source,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return {
        "id": note.id,
        "content": note.content,
        "source": note.source,
        "created_at": note.created_at.isoformat(),
        "updated_at": note.updated_at.isoformat(),
    }


@router.patch("/papers/{paper_id:path}/notes/{note_id}")
def update_note(
    paper_id: str, note_id: int, body: NoteUpdate, db: Session = Depends(get_db)
) -> dict:
    note = db.get(PaperNoteORM, note_id)
    if note is None or note.paper_id != paper_id or note.user_id != DEFAULT_USER:
        raise HTTPException(404, "Note not found")
    note.content = body.content.strip()
    note.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(note)
    return {
        "id": note.id,
        "content": note.content,
        "source": note.source,
        "created_at": note.created_at.isoformat(),
        "updated_at": note.updated_at.isoformat(),
    }


@router.delete("/papers/{paper_id:path}/notes/{note_id}", status_code=204)
def delete_note(paper_id: str, note_id: int, db: Session = Depends(get_db)) -> None:
    note = db.get(PaperNoteORM, note_id)
    if note and note.paper_id == paper_id and note.user_id == DEFAULT_USER:
        db.delete(note)
        db.commit()


# ── Quota ─────────────────────────────────────────────────────────────────────


@router.get("/quota")
def get_quota() -> dict:
    return {
        "ingest_jobs_today": 0,
        "ingest_jobs_limit": 20,
        "chat_messages_today": 0,
        "chat_messages_limit": 120,
    }
