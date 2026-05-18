"""Watches and events endpoints."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models.user import WatchORM
from ..deps import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["watches"])


class WatchCreate(BaseModel):
    name: str | None = None
    interests: list[str]
    sources: list[str] = []
    digest_schedule: str = "daily"


@router.get("/watches")
def list_watches(db: Session = Depends(get_db)) -> list[dict]:
    stmt = select(WatchORM).where(WatchORM.user_id == "default")
    rows = db.execute(stmt).scalars().all()
    return [w.to_pydantic().model_dump() for w in rows]


@router.post("/watches", status_code=201)
def create_watch(body: WatchCreate, db: Session = Depends(get_db)) -> dict:
    w = WatchORM(
        user_id="default",
        name=body.name or ", ".join(body.interests[:2]),
        interests_json=json.dumps(body.interests),
        sources_json=json.dumps(body.sources),
        active=True,
    )
    db.add(w)
    db.flush()
    return w.to_pydantic().model_dump()


@router.put("/watches/{watch_id}")
def update_watch(watch_id: int, body: dict, db: Session = Depends(get_db)) -> dict:
    w = db.get(WatchORM, watch_id)
    if w is None:
        raise HTTPException(404)
    if "active" in body:
        w.active = body["active"]
    return w.to_pydantic().model_dump()


@router.delete("/watches/{watch_id}", status_code=204)
def delete_watch(watch_id: int, db: Session = Depends(get_db)) -> None:
    w = db.get(WatchORM, watch_id)
    if w:
        db.delete(w)


@router.get("/watches/{watch_id}/digests/latest")
def watch_digest(watch_id: int) -> dict:
    return {"id": None, "generated_at": None, "summary_md": "", "new_papers": [], "read": True}


@router.post("/events/paper_view", status_code=204)
def paper_view_event(body: dict) -> None:
    pass
