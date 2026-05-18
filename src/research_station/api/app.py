"""FastAPI application factory.

Lifespan: ensures DB tables exist on startup.
CORS: open in dev (all origins); tighten for production.
WebSockets: /ws/health streams system stats every 5 seconds.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..config.settings import get_settings
from ..database.engine import build_engine, build_session_factory, get_session
from ..database.repository import CitationRepository, PaperRepository, SummaryRepository
from ..models.entity import (  # noqa: F401 — registers entity tables
    EntityRelationshipORM,
    PaperEntityORM,
)
from ..models.paper import Base as PaperBase
from ..models.summary import PaperSummaryORM  # noqa: F401 — registers table
from ..models.user import (  # noqa: F401 — registers tables
    CollectionItemORM,
    CollectionORM,
    IngestHistoryORM,
    PinORM,
    UserORM,
    WatchORM,
)
from .routes import chat as chat_router
from .routes import export as export_router
from .routes import graph as graph_router
from .routes import ingest as ingest_router
from .routes import papers as papers_router
from .routes import processing as processing_router
from .routes import reader as reader_router
from .routes import search as search_router
from .routes import system as system_router
from .routes import users as users_router
from .routes import watches as watches_router
from .routes import web as web_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_directories()
    engine = build_engine(settings.database.sqlite_path)
    PaperBase.metadata.create_all(engine)
    yield
    engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="ResearchStation API",
        description="ML paper corpus management and exploration API",
        version="0.3.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(search_router.router)  # /papers/search, /search/omni — before /papers/{id}
    # Specific /papers/* paths must all come before papers_router's /{id} catch-all
    app.include_router(
        graph_router.router
    )  # /papers/graph, /papers/traverse, /papers/traversals, /papers/compare, /models/catalog
    app.include_router(
        processing_router.router
    )  # /papers/embed/batch, /papers/manually-added, /batch/*, /processing/*
    app.include_router(
        reader_router.router
    )  # /papers/{id}/reader, /fulltext, /entities, /cache, /ocr, /pdf.pdf
    app.include_router(export_router.router)  # /papers/{id}/export.*
    app.include_router(chat_router.router)  # /chats/*
    app.include_router(watches_router.router)  # /watches/*, /events/*
    app.include_router(web_router.router)  # /web/ingest, /papers/{id}/screenshots
    app.include_router(papers_router.router)  # /papers/queue, /papers/{id} — catch-all last
    app.include_router(ingest_router.router)  # /ingest/*, ws /ws/ingest/{id}
    app.include_router(users_router.router)  # /users/me/*
    app.include_router(system_router.router)  # /system/health, /taxonomy/lanes, /sources

    # ── Dashboard static files ────────────────────────────────────────────
    # app.py lives at src/research_station/api/app.py → 4 parents up = project root
    # Prefer the Vite build output (frontend/dist/); fall back to legacy frontend/
    _root = Path(__file__).resolve().parents[3]
    _ui_dir = _root / "frontend" / "dist"
    if not _ui_dir.exists():
        _ui_dir = _root / "frontend"
    _html = _ui_dir / "index.html"

    # ── Agent workspace — served before UI so /workspace/* resolves correctly
    _workspace_dir = Path(__file__).resolve().parents[3] / "workspace"
    _workspace_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/workspace", StaticFiles(directory=str(_workspace_dir)), name="workspace")

    # ── Web page screenshots — served at /web-screenshots/<paper_safe_id>/<viewport>.jpg
    _web_screenshots_root = Path(get_settings().web_screenshots_dir)
    _web_screenshots_root.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/web-screenshots",
        StaticFiles(directory=str(_web_screenshots_root)),
        name="web-screenshots",
    )

    if _ui_dir.exists():

        @app.get("/", include_in_schema=False)
        @app.get("/dashboard", include_in_schema=False)
        def dashboard_index() -> FileResponse:
            return FileResponse(str(_html))

        # Serve all other assets (CSS, JS, components/, etc.) under the same root.
        # This mount must come AFTER all @app.get() / include_router() calls so
        # API routes take precedence over static file matching.
        app.mount("/", StaticFiles(directory=str(_ui_dir)), name="ui")

    # ── WebSocket: live health stream ─────────────────────────────────────

    @app.websocket("/ws/health")
    async def ws_health(websocket: WebSocket) -> None:
        await websocket.accept()
        settings = get_settings()
        engine = build_engine(settings.database.sqlite_path)
        factory = build_session_factory(engine)
        try:
            while True:
                with get_session(factory) as db:
                    paper_repo = PaperRepository(db)
                    citation_repo = CitationRepository(db)
                    summary_repo = SummaryRepository(db)
                    payload = {
                        "type": "health",
                        "ts": datetime.utcnow().isoformat(),
                        "papers": paper_repo.count(),
                        "citations": citation_repo.count(),
                        "summaries": summary_repo.count(),
                    }
                await websocket.send_text(json.dumps(payload))
                await asyncio.sleep(5)
        except WebSocketDisconnect:
            pass

    return app


app = create_app()
