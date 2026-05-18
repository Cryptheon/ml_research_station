"""LLM-based structured entity and relationship extractor for research papers."""

from __future__ import annotations

import json
import logging
from datetime import datetime

from ..models.entity import (
    ENTITY_TYPES,
    RELATIONSHIP_TYPES,
    EntityExtractionResult,
    ExtractedEntity,
    ExtractedRelationship,
)
from .llm.base import BaseLLMClient, Message
from .prompts import load as load_prompt

logger = logging.getLogger(__name__)

_MAX_CONTENT_CHARS = 6_000


class EntityExtractor:
    """Extracts structured entities and typed relationships from paper text via an LLM."""

    def __init__(self, llm_client: BaseLLMClient) -> None:
        self._client = llm_client

    async def extract(
        self,
        paper_id: str,
        title: str,
        content: str,
    ) -> EntityExtractionResult:
        truncated = content[:_MAX_CONTENT_CHARS]
        prompt = load_prompt("entity_extract").substitute(
            title=title,
            content=truncated,
        )
        resp = await self._client.chat(
            [Message(role="user", content=prompt)],
            system_prompt=(
                "You extract structured entities and typed relationships from research text. "
                "Output only valid JSON objects as specified — no preamble, no markdown."
            ),
            temperature=0.1,
            max_tokens=2000,
        )
        entities, relationships = self._parse(resp.content or "")
        model_label = getattr(self._client, "_model", self._client.provider_name)
        return EntityExtractionResult(
            paper_id=paper_id,
            entities=entities,
            relationships=relationships,
            model_used=model_label,
            generated_at=datetime.utcnow(),
        )

    # ── Parsing ───────────────────────────────────────────────────────────────

    def _parse(self, text: str) -> tuple[list[ExtractedEntity], list[ExtractedRelationship]]:
        entities: list[ExtractedEntity] = []
        relationships: list[ExtractedRelationship] = []

        # Split on the two section headers
        ent_block = ""
        rel_block = ""
        if "RELATIONSHIPS:" in text:
            parts = text.split("RELATIONSHIPS:", 1)
            ent_part = parts[0]
            rel_block = parts[1]
        else:
            ent_part = text
        if "ENTITIES:" in ent_part:
            ent_block = ent_part.split("ENTITIES:", 1)[1]
        else:
            ent_block = ent_part

        for line in ent_block.splitlines():
            obj = _try_json(line)
            if obj is None:
                continue
            name = str(obj.get("name", "")).strip()
            etype = str(obj.get("type", "")).strip().lower()
            if not name or etype not in ENTITY_TYPES:
                continue
            attrs = obj.get("attributes", {})
            if not isinstance(attrs, dict):
                attrs = {}
            entities.append(ExtractedEntity(name=name, entity_type=etype, attributes=attrs))

        # Build a name → canonical name map for relationship resolution
        entity_names = {e.name.lower(): e.name for e in entities}

        for line in rel_block.splitlines():
            obj = _try_json(line)
            if obj is None:
                continue
            from_raw = str(obj.get("from", "")).strip()
            to_raw = str(obj.get("to", "")).strip()
            rtype = str(obj.get("type", "")).strip().lower()
            if not from_raw or not to_raw or rtype not in RELATIONSHIP_TYPES:
                continue
            from_name = entity_names.get(from_raw.lower(), from_raw)
            to_name = entity_names.get(to_raw.lower(), to_raw)
            try:
                confidence = max(0.0, min(1.0, float(obj.get("confidence", 0.8))))
            except (TypeError, ValueError):
                confidence = 0.8
            relationships.append(
                ExtractedRelationship(
                    from_entity=from_name,
                    to_entity=to_name,
                    relationship_type=rtype,
                    description=str(obj.get("description", "")).strip(),
                    confidence=confidence,
                )
            )

        logger.info(
            "Entity extraction: %d entities, %d relationships",
            len(entities),
            len(relationships),
        )
        return entities, relationships


def _try_json(line: str) -> dict | None:
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        obj = json.loads(line)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None
