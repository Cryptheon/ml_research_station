"""Graph, traversal, edge classification, discover, compare, and neighbors endpoints."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config.settings import get_settings
from ...models.paper import CACHE_EMBEDDINGS, CitationORM, PaperEdgeORM, PaperORM
from ...models.traversal import TraversalORM, _utcnow
from ...processing.embedding_service import get_embedding_service
from ...processing.llm.base import Message
from ...processing.llm.factory import create_llm_client
from ...processing.prompts import render as render_prompt
from ..background import _bg_classify_edges, _classify_state
from ..deps import get_db
from ..schemas import PaperDetail

logger = logging.getLogger(__name__)

router = APIRouter(tags=["graph"])


def _compute_author_edges(papers: list[PaperORM], paper_id_set: set[str]) -> list[dict]:
    """Build undirected author co-authorship edges, annotated with shared author names."""
    author_to_papers: dict[str, list[str]] = {}
    for p in papers:
        try:
            authors = json.loads(p.authors_json or "[]")
        except Exception:
            authors = []
        for a in authors:
            name = (a.get("name") or "").strip()
            if len(name) < 4:
                continue
            author_to_papers.setdefault(name, []).append(p.id)

    pair_authors: dict[tuple[str, str], list[str]] = {}
    for name, pid_list in author_to_papers.items():
        for i in range(len(pid_list)):
            for j in range(i + 1, len(pid_list)):
                a, b = pid_list[i], pid_list[j]
                if a not in paper_id_set or b not in paper_id_set:
                    continue
                key = (min(a, b), max(a, b))
                pair_authors.setdefault(key, []).append(name)

    return [
        {"from": k[0], "to": k[1], "type": "author", "shared": v[:3]}
        for k, v in pair_authors.items()
    ]


@router.get("/papers/graph")
def get_corpus_graph(
    limit: int = Query(default=500),
    semantic: bool = Query(default=False),
    sim_threshold: float = Query(default=0.75, ge=0.0, le=1.0),
    layout: str = Query(default="force"),
    author_edges: bool = Query(default=False),
    llm_edges: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> dict:
    """Full corpus graph: nodes + citation edges + optional semantic/author/LLM edges."""
    all_papers = (
        db.execute(select(PaperORM).order_by(PaperORM.published_date.desc()).limit(limit))
        .scalars()
        .all()
    )

    paper_id_set = {p.id for p in all_papers}

    edge_rows = (
        db.execute(
            select(CitationORM).where(
                CitationORM.citing_paper_id.in_(paper_id_set),
                CitationORM.cited_paper_id.in_(paper_id_set),
            )
        )
        .scalars()
        .all()
    )

    pca_positions: dict = {}
    if layout in ("pca", "umap", "embedding"):
        try:
            svc = get_embedding_service()
            if layout == "umap":
                pca_positions = svc.get_umap_layout([p.id for p in all_papers])
            else:
                pca_positions = svc.get_pca_layout([p.id for p in all_papers])
            logger.info(
                "%s layout: %d/%d papers positioned", layout, len(pca_positions), len(all_papers)
            )
        except Exception as exc:
            logger.warning("%s layout failed: %s", layout, exc)

    nodes = []
    for p in all_papers:
        paper = p.to_pydantic()
        node: dict = {
            "id": paper.id,
            "title": paper.title,
            "topics": paper.topics,
            "date": paper.published_date.date().isoformat() if paper.published_date else None,
            "citedBy": paper.citation_count or 0,
            "venue": paper.venue or "",
            "source": paper.source.value if hasattr(paper.source, "value") else str(paper.source),
            "embedded": bool((p.cache_flags or 0) & CACHE_EMBEDDINGS),
        }
        if paper.id in pca_positions:
            node["px"], node["py"] = pca_positions[paper.id]
        nodes.append(node)

    edges = [
        {"from": e.citing_paper_id, "to": e.cited_paper_id, "influential": bool(e.is_influential)}
        for e in edge_rows
    ]

    semantic_edges: list[dict] = []
    if semantic:
        try:
            svc = get_embedding_service()
            semantic_edges = svc.get_semantic_edges(
                list(paper_id_set), threshold=sim_threshold, k=5
            )
        except Exception as exc:
            logger.warning("Semantic edges failed: %s", exc)

    author_edge_list: list[dict] = []
    if author_edges:
        try:
            author_edge_list = _compute_author_edges(all_papers, paper_id_set)
            logger.info("Author edges: %d", len(author_edge_list))
        except Exception as exc:
            logger.warning("Author edges failed: %s", exc)

    llm_edge_list: list[dict] = []
    if llm_edges:
        try:
            rows = (
                db.execute(
                    select(PaperEdgeORM).where(
                        PaperEdgeORM.from_id.in_(paper_id_set),
                        PaperEdgeORM.to_id.in_(paper_id_set),
                    )
                )
                .scalars()
                .all()
            )
            llm_edge_list = [
                {
                    "from": r.from_id,
                    "to": r.to_id,
                    "edge_type": r.edge_type,
                    "description": r.description,
                    "confidence": r.confidence,
                    "source": r.source,
                }
                for r in rows
            ]
            logger.info("LLM edges: %d", len(llm_edge_list))
        except Exception as exc:
            logger.warning("LLM edges failed: %s", exc)

    return {
        "nodes": nodes,
        "edges": edges,
        "semantic_edges": semantic_edges,
        "author_edges": author_edge_list,
        "llm_edges": llm_edge_list,
    }


@router.get("/papers/{paper_id:path}/lineage")
def get_lineage(
    paper_id: str,
    depth: int = Query(default=1),
    window_days: int = Query(default=365),
    db: Session = Depends(get_db),
) -> dict:
    from ...database.repository import CitationRepository

    orm = db.get(PaperORM, paper_id)
    if orm is None:
        raise HTTPException(404)
    citation_repo = CitationRepository(db)
    refs = citation_repo.get_references(paper_id)
    citing = citation_repo.get_citing_papers(paper_id)

    edges = [
        {"from": c.citing_paper_id, "to": c.cited_paper_id, "kind": "cites"} for c in refs + citing
    ]
    neighbour_ids = {e["from"] for e in edges} | {e["to"] for e in edges}
    neighbour_ids.discard(paper_id)

    nodes = []
    cutoff = datetime.utcnow() - timedelta(days=window_days)
    anchor = orm.to_pydantic()
    nodes.append(
        {
            "id": anchor.id,
            "title": anchor.title,
            "topics": anchor.topics,
            "date": anchor.published_date.date().isoformat(),
            "citedBy": anchor.citation_count or 0,
        }
    )
    for nid in neighbour_ids:
        n = db.get(PaperORM, nid)
        if n and n.published_date >= cutoff:
            np_ = n.to_pydantic()
            nodes.append(
                {
                    "id": np_.id,
                    "title": np_.title,
                    "topics": np_.topics,
                    "date": np_.published_date.date().isoformat(),
                    "citedBy": np_.citation_count or 0,
                }
            )

    return {"nodes": nodes[:80], "edges": edges, "center_id": paper_id}


class TraverseRequest(BaseModel):
    start_id: str
    edge_types: list[str] | None = None
    include_citations: bool = False
    include_semantic: bool = True
    semantic_k: int = 5
    semantic_threshold: float = 0.65
    max_depth: int = 3
    max_nodes: int = 30


@router.post("/papers/traverse")
def traverse_graph(body: TraverseRequest, db: Session = Depends(get_db)) -> dict:
    """BFS graph traversal starting from a paper."""
    start = db.get(PaperORM, body.start_id)
    if start is None:
        raise HTTPException(404, f"Paper '{body.start_id}' not found")

    _PSEUDO_TYPES = {"cites", "cited_by", "semantic"}
    include_citations = body.include_citations
    include_semantic = body.include_semantic
    llm_edge_filter: list[str] | None = body.edge_types
    if body.edge_types:
        pseudo_found = set(body.edge_types) & _PSEUDO_TYPES
        if "cites" in pseudo_found or "cited_by" in pseudo_found:
            include_citations = True
        if "semantic" in pseudo_found:
            include_semantic = True
        real_types = [t for t in body.edge_types if t not in _PSEUDO_TYPES]
        llm_edge_filter = real_types if real_types else None

    all_llm = db.execute(select(PaperEdgeORM)).scalars().all()
    adj: dict[str, list[dict]] = {}
    for e in all_llm:
        if llm_edge_filter and e.edge_type not in llm_edge_filter:
            continue
        for src, dst in [(e.from_id, e.to_id), (e.to_id, e.from_id)]:
            adj.setdefault(src, []).append(
                {
                    "neighbor": dst,
                    "edge_type": e.edge_type,
                    "description": e.description,
                    "confidence": e.confidence,
                    "direction": "out" if src == e.from_id else "in",
                }
            )

    if include_citations:
        cit_rows = db.execute(select(CitationORM)).scalars().all()
        for c in cit_rows:
            for src, dst, dir_ in [
                (c.citing_paper_id, c.cited_paper_id, "out"),
                (c.cited_paper_id, c.citing_paper_id, "in"),
            ]:
                adj.setdefault(src, []).append(
                    {
                        "neighbor": dst,
                        "edge_type": "cites" if dir_ == "out" else "cited_by",
                        "description": None,
                        "confidence": None,
                        "direction": dir_,
                    }
                )

    title_cache: dict[str, str] = {
        r.id: r.title for r in db.execute(select(PaperORM)).scalars().all()
    }

    visited: dict[str, dict] = {
        body.start_id: {
            "id": body.start_id,
            "title": title_cache.get(body.start_id, body.start_id),
            "depth": 0,
            "via_from": None,
            "via_edge_type": None,
            "via_description": None,
            "via_confidence": None,
            "via_direction": None,
        }
    }
    edges_walked: list[dict] = []
    queue = [(body.start_id, 0)]
    stopped_reason = "exhausted"

    svc = None
    if include_semantic:
        try:
            svc = get_embedding_service()
        except Exception:
            svc = None

    while queue:
        current_id, depth = queue.pop(0)
        if depth >= body.max_depth:
            stopped_reason = "max_depth"
            continue
        if len(visited) >= body.max_nodes:
            stopped_reason = "max_nodes"
            break

        neighbors: list[dict] = list(adj.get(current_id, []))

        if svc and include_semantic:
            try:
                sem = svc.get_neighbors(current_id, k=body.semantic_k)
                for n in sem:
                    if n["similarity"] >= body.semantic_threshold:
                        neighbors.append(
                            {
                                "neighbor": n["id"],
                                "edge_type": "semantic",
                                "description": f"similarity={n['similarity']:.3f}",
                                "confidence": n["similarity"],
                                "direction": "out",
                            }
                        )
            except Exception:
                pass

        for nb in neighbors:
            nid = nb["neighbor"]
            if nid not in title_cache:
                continue
            if nid in visited:
                continue
            if len(visited) >= body.max_nodes:
                stopped_reason = "max_nodes"
                break
            visited[nid] = {
                "id": nid,
                "title": title_cache[nid],
                "depth": depth + 1,
                "via_from": current_id,
                "via_edge_type": nb["edge_type"],
                "via_description": nb["description"],
                "via_confidence": nb["confidence"],
                "via_direction": nb["direction"],
            }
            edges_walked.append(
                {
                    "from_id": current_id,
                    "to_id": nid,
                    "edge_type": nb["edge_type"],
                    "description": nb["description"],
                    "confidence": nb["confidence"],
                    "depth": depth + 1,
                }
            )
            queue.append((nid, depth + 1))

    result = {
        "start_id": body.start_id,
        "start_title": title_cache.get(body.start_id, body.start_id),
        "nodes_visited": list(visited.values()),
        "edges_walked": edges_walked,
        "total_nodes": len(visited),
        "total_edges": len(edges_walked),
        "params": body.model_dump(),
        "stopped_reason": stopped_reason,
    }
    created = _utcnow()
    result["created_at"] = created.isoformat()
    row = TraversalORM(
        start_id=body.start_id,
        start_title=title_cache.get(body.start_id),
        result_json=json.dumps(result),
        created_at=created,
    )
    db.add(row)
    return result


@router.get("/papers/traverse/last")
def get_last_traversal(db: Session = Depends(get_db)) -> dict:
    """Return the most recent graph traversal result, or empty if none."""
    row = db.query(TraversalORM).order_by(TraversalORM.id.desc()).first()
    if row is None:
        return {"start_id": None, "nodes_visited": [], "edges_walked": [], "total_nodes": 0}
    result = json.loads(row.result_json)
    result["created_at"] = row.created_at.isoformat()
    return result


@router.delete("/papers/traverse/last")
def clear_last_traversal(db: Session = Depends(get_db)) -> dict:
    row = db.query(TraversalORM).order_by(TraversalORM.id.desc()).first()
    if row:
        db.delete(row)
    return {"status": "cleared"}


@router.get("/papers/traversals")
def list_traversals(db: Session = Depends(get_db)) -> list:
    """Return all stored traversals, newest-first."""
    rows = db.query(TraversalORM).order_by(TraversalORM.id.desc()).all()
    results = []
    for row in rows:
        r = json.loads(row.result_json)
        r["created_at"] = row.created_at.isoformat()
        r["_db_id"] = row.id
        results.append(r)
    return results


@router.delete("/papers/traversals")
def clear_traversals(db: Session = Depends(get_db)) -> dict:
    db.query(TraversalORM).delete()
    return {"status": "cleared"}


@router.delete("/papers/traversals/{idx}")
def delete_traversal(idx: int, db: Session = Depends(get_db)) -> dict:
    """Delete a traversal by its position in newest-first order (0 = most recent)."""
    rows = db.query(TraversalORM).order_by(TraversalORM.id.desc()).all()
    if 0 <= idx < len(rows):
        db.delete(rows[idx])
    return {"status": "ok"}


@router.get("/papers/{paper_id}/web-pages")
def list_web_pages(paper_id: str, db: Session = Depends(get_db)):
    """Return web pages ingested in association with paper_id, newest-first."""
    import urllib.parse

    from ...models.web_link import WebPaperLinkORM

    pid = urllib.parse.unquote(paper_id)
    rows = (
        db.query(WebPaperLinkORM)
        .filter(WebPaperLinkORM.paper_id == pid)
        .order_by(WebPaperLinkORM.created_at.desc())
        .all()
    )
    result = []
    for r in rows:
        wp = db.get(PaperORM, r.web_paper_id)
        result.append(
            {
                "web_paper_id": r.web_paper_id,
                "url": r.url,
                "title": wp.title if wp else r.url,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
        )
    return result


@router.get("/papers/graph/edges")
def list_edges(
    source: str | None = Query(default=None),
    edge_type: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Return all classified LLM edges, optionally filtered by source or type."""
    stmt = select(PaperEdgeORM)
    if source:
        stmt = stmt.where(PaperEdgeORM.source == source)
    if edge_type:
        stmt = stmt.where(PaperEdgeORM.edge_type == edge_type)
    stmt = stmt.order_by(PaperEdgeORM.confidence.desc()).limit(limit)
    rows = db.execute(stmt).scalars().all()
    out = []
    for r in rows:
        from_orm = db.get(PaperORM, r.from_id)
        to_orm = db.get(PaperORM, r.to_id)
        out.append(
            {
                "from_id": r.from_id,
                "to_id": r.to_id,
                "from_title": from_orm.title if from_orm else r.from_id,
                "to_title": to_orm.title if to_orm else r.to_id,
                "edge_type": r.edge_type,
                "description": r.description,
                "confidence": r.confidence,
                "source": r.source,
            }
        )
    return out


@router.delete("/papers/graph/edges")
def delete_edges(
    source: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    """Delete classified edges. Pass source=llm to clear only LLM edges."""
    from sqlalchemy import delete as _del

    stmt = _del(PaperEdgeORM)
    if source:
        stmt = stmt.where(PaperEdgeORM.source == source)
    result = db.execute(stmt)
    return {"deleted": result.rowcount}


@router.get("/papers/graph/edges/classify/status")
def classify_status() -> dict:
    return dict(_classify_state)


@router.post("/papers/graph/edges/classify/stop")
def classify_stop() -> dict:
    """Request cancellation of the running classification job."""
    if not _classify_state["running"]:
        return {"status": "not_running"}
    _classify_state["cancel"] = True
    return {"status": "cancel_requested"}


@router.post("/papers/graph/edges/classify", status_code=202)
async def classify_edges(
    background_tasks: BackgroundTasks,
    neighbors: int = Query(default=3, ge=1, le=10),
    all_sources: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> dict:
    """Queue LLM classification of semantic neighbor pairs."""
    if _classify_state["running"]:
        raise HTTPException(409, "Classification already running")
    settings = get_settings()
    background_tasks.add_task(_bg_classify_edges, settings, neighbors, all_sources)
    return {"status": "queued", "neighbors": neighbors, "all_sources": all_sources}


@router.post("/papers/{paper_id:path}/discover")
async def discover_connections(
    paper_id: str,
    body: dict,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Stream an LLM exploration of unexpected connections between anchor paper and N random papers."""
    import random as _random

    n = min(int(body.get("n", 10)), 30)
    anchor = db.get(PaperORM, paper_id)
    if anchor is None:
        raise HTTPException(404, f"Paper '{paper_id}' not found")

    all_ids = [r[0] for r in db.execute(select(PaperORM.id)).fetchall() if r[0] != paper_id]
    sample_ids = _random.sample(all_ids, min(n, len(all_ids)))
    candidates = [db.get(PaperORM, sid) for sid in sample_ids]
    candidates = [c for c in candidates if c is not None]

    candidate_block = "\n\n".join(
        f"[{i + 1}] {c.title}\n{(c.abstract or '')[:300]}" for i, c in enumerate(candidates)
    )

    prompt = render_prompt(
        "discover",
        anchor_title=anchor.title,
        anchor_abstract=(anchor.abstract or "")[:500],
        candidate_block=candidate_block,
    )

    settings = get_settings()

    async def event_gen():
        try:
            yield f"data: {json.dumps({'type': 'candidates', 'papers': [{'id': c.id, 'title': c.title} for c in candidates]})}\n\n"
            client = create_llm_client(settings)
            if hasattr(client, "stream_chat"):
                async for chunk in client.stream_chat(
                    [Message(role="user", content=prompt)],
                    system_prompt=(
                        "You are an expert at finding non-obvious intellectual connections "
                        "between ML research papers. Be specific, insightful, and concise."
                    ),
                    max_tokens=800,
                ):
                    yield f"data: {json.dumps(chunk)}\n\n"
            else:
                resp = await client.chat(
                    [Message(role="user", content=prompt)],
                    system_prompt=(
                        "You are an expert at finding non-obvious intellectual connections "
                        "between ML research papers. Be specific, insightful, and concise."
                    ),
                    max_tokens=800,
                )
                yield f"data: {json.dumps({'type': 'content', 'delta': resp.content})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'delta': str(exc)})}\n\n"
        finally:
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/papers/compare")
def compare_papers(
    a: str = Query(...),
    b: str = Query(...),
    db: Session = Depends(get_db),
) -> dict:
    a_orm = db.get(PaperORM, a)
    b_orm = db.get(PaperORM, b)
    if not a_orm:
        raise HTTPException(404, f"Paper '{a}' not found")
    if not b_orm:
        raise HTTPException(404, f"Paper '{b}' not found")
    return {
        "a": PaperDetail.from_paper(a_orm.to_pydantic()).model_dump(),
        "b": PaperDetail.from_paper(b_orm.to_pydantic()).model_dump(),
        "diffs": [],
    }


@router.get("/papers/{paper_id:path}/neighbors")
def get_neighbors(paper_id: str, k: int = 6, db: Session = Depends(get_db)) -> list[dict]:
    orm = db.get(PaperORM, paper_id)
    if orm is None:
        raise HTTPException(404)
    if not (orm.cache_flags or 0) & CACHE_EMBEDDINGS:
        return []
    try:
        svc = get_embedding_service()
        return svc.get_neighbors(paper_id, k=k)
    except Exception as exc:
        logger.warning("get_neighbors %s: %s", paper_id, exc)
        return []


@router.get("/papers/{paper_id:path}/mentions")
def get_mentions(paper_id: str, db: Session = Depends(get_db)) -> list[dict]:
    return []


@router.post("/papers/{paper_id:path}/mentions/refresh", status_code=202)
def refresh_mentions(paper_id: str) -> dict:
    return {"job_id": "mentions-placeholder"}


@router.get("/models/catalog")
def models_catalog() -> list[dict]:
    return [
        {
            "id": "local",
            "name": "Local · vLLM",
            "model": "llama-3.1-70B",
            "cost_per_paper_cents": 0,
            "tokens_per_sec": 14,
            "privacy": "full",
        },
        {
            "id": "claude",
            "name": "Claude Sonnet",
            "model": "claude-sonnet-4-6",
            "cost_per_paper_cents": 0.3,
            "tokens_per_sec": 68,
            "privacy": "api",
        },
        {
            "id": "gpt",
            "name": "GPT-4.1",
            "model": "gpt-4.1",
            "cost_per_paper_cents": 0.4,
            "tokens_per_sec": 72,
            "privacy": "api",
        },
        {
            "id": "gemini",
            "name": "Gemini 2.5 Pro",
            "model": "gemini-2.5-pro",
            "cost_per_paper_cents": 0.2,
            "tokens_per_sec": 85,
            "privacy": "api",
        },
    ]
