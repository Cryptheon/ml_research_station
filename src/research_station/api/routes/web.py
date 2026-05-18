"""Web ingestion and screenshot endpoints."""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text as _sql
from sqlalchemy.orm import Session

from ...config.settings import Settings, get_settings
from ...database.engine import build_engine, build_session_factory, get_session
from ...models.paper import CACHE_FULLTEXT, PaperORM, PaperStatus
from ..background import _build_ocr_backend
from ..deps import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["web"])


class WebIngestRequest(BaseModel):
    url: str
    paper_id: str | None = None
    force_ocr: bool = False


@router.post("/web/ingest")
def ingest_webpage_endpoint(
    req: WebIngestRequest,
    settings: Settings = Depends(get_settings),
):
    """Fetch a web page, extract text, and add it to the corpus."""
    from datetime import datetime, timezone

    from ...models.web_link import WebPaperLinkORM
    from ...processing.web_ingest import WebPageIngestor

    url = req.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://")

    ocr_backend = _build_ocr_backend(settings) if req.force_ocr else None
    if req.force_ocr and ocr_backend is None:
        raise HTTPException(
            status_code=503,
            detail="force_ocr=true but no OCR backend is configured (check ocr.provider in settings).",
        )

    ingestor = WebPageIngestor(
        ocr_dir=settings.ocr_dir,
        screenshots_dir=settings.web_screenshots_dir,
        ocr_backend=ocr_backend,
    )
    try:
        result = ingestor.ingest(url, force_ocr=req.force_ocr)
    except Exception as exc:
        logger.exception("web_ingest: failed for %s", url)
        raise HTTPException(status_code=500, detail=str(exc))

    paper_id = result["paper_id"]
    title = result["title"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    shot_rel_paths = []
    for abs_path in result.get("screenshots", []):
        try:
            rel = Path(abs_path).relative_to(settings.web_screenshots_dir)
            shot_rel_paths.append(str(rel))
        except ValueError:
            pass

    meta = {"url": url, "method": result["method"], "screenshots": shot_rel_paths}

    sf = build_session_factory(build_engine(settings.database.sqlite_path))
    with get_session(sf) as db:
        existing = db.get(PaperORM, paper_id)
        if existing is None:
            row = PaperORM(
                id=paper_id,
                title=title,
                abstract=None,
                authors_json="[]",
                categories_json="[]",
                keywords_json="[]",
                source="web",
                published_date=now,
                updated_date=now,
                raw_metadata_json=json.dumps(meta),
                topics_json="[]",
                velocity_12w_json="[]",
                cache_flags=CACHE_FULLTEXT,
                status=PaperStatus.QUEUED.value,
            )
            db.add(row)
        else:
            try:
                existing_meta = json.loads(existing.raw_metadata_json or "{}")
            except Exception:
                existing_meta = {}
            existing_meta.update(meta)
            db.execute(
                _sql(
                    "UPDATE papers SET cache_flags = (COALESCE(cache_flags, 0) | :flag), updated_at = :now, raw_metadata_json = :meta WHERE id = :id"
                ),
                {
                    "flag": CACHE_FULLTEXT,
                    "now": now,
                    "meta": json.dumps(existing_meta),
                    "id": paper_id,
                },
            )

        if req.paper_id:
            existing_link = db.get(WebPaperLinkORM, (paper_id, req.paper_id))
            if existing_link is None:
                db.add(
                    WebPaperLinkORM(
                        web_paper_id=paper_id,
                        paper_id=req.paper_id,
                        url=url,
                    )
                )

    try:
        import urllib.request as _ur

        api_base = os.environ.get("RS_API_BASE", "http://localhost:8080")
        enc = urllib.parse.quote(paper_id, safe="")
        _ur.urlopen(
            _ur.Request(f"{api_base}/papers/{enc}/embed", method="POST"),
            timeout=5,
        )
    except Exception as e:
        logger.warning("web_ingest: embed trigger failed (non-fatal): %s", e)

    return {
        "paper_id": paper_id,
        "title": title,
        "url": url,
        "char_count": result["char_count"],
        "method": result["method"],
        "screenshot_count": len(shot_rel_paths),
    }


@router.get("/papers/{paper_id:path}/screenshots")
def get_web_screenshots(paper_id: str, db: Session = Depends(get_db)):
    """Return screenshot URLs for a web: paper, served at /web-screenshots/..."""
    pid = urllib.parse.unquote(paper_id)
    orm = db.get(PaperORM, pid)
    if orm is None:
        return []
    try:
        meta = json.loads(orm.raw_metadata_json or "{}")
        shots = meta.get("screenshots", [])
    except Exception:
        shots = []
    api_base = os.environ.get("RS_API_BASE", "http://localhost:8080")
    return [
        {"index": i, "url": f"{api_base}/web-screenshots/{path}"} for i, path in enumerate(shots)
    ]
