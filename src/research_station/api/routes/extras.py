"""Stub: all routes have been split into dedicated modules.

- reader.py    — /papers/{id}/reader, fulltext, entities, cache, ocr, pdf proxy
- processing.py — /papers/embed/batch, /batch/*, /processing/*
- graph.py     — /papers/graph, traverse, lineage, edges, discover, compare, neighbors
- export.py    — /papers/{id}/export.*
- chat.py      — /chats/*
- watches.py   — /watches/*, /events/*
- web.py       — /web/ingest, /papers/{id}/screenshots
"""

from fastapi import APIRouter

router = APIRouter()
