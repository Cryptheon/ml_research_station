"""Chat endpoints: simple stream, agentic stream, and non-streaming fallback."""

from __future__ import annotations

import json
import logging
import uuid as _uuid
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ...config.settings import get_settings
from ...database.engine import build_engine, build_session_factory, get_session
from ...database.repository import SummaryRepository
from ...models.paper import PaperORM
from ...models.user import ChatMessageORM, ChatORM
from ...processing.llm.base import Message
from ...processing.llm.factory import create_llm_client
from ..deps import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

_OCR_CHAT_LIMIT = 12_000


def _build_paper_context(paper_id: str, db: Session) -> str:
    """Build a rich context block for a paper to inject into the chat system prompt."""
    from ...processing.pdf_ocr import PDFOCRPipeline

    orm = db.get(PaperORM, paper_id)
    if orm is None:
        return ""

    paper = orm.to_pydantic()
    settings = get_settings()
    has_pdf = bool(orm.local_pdf_path and Path(orm.local_pdf_path).exists())
    has_ocr = has_pdf and bool(
        PDFOCRPipeline.load_text(Path(orm.local_pdf_path), paper_id, ocr_dir=settings.ocr_dir)
    )

    parts: list[str] = [
        f"Paper ID: {paper_id}",
        f"Title: {paper.title}",
        f"Authors: {', '.join(a.name for a in paper.authors[:5])}",
        f"Venue: {paper.venue or paper.source.value}  Year: {paper.published_date.year}",
        f"PDF downloaded: {'yes' if has_pdf else 'no'}  "
        f"Text extractable: {'yes (call extract_pdf_text or rag_query with this paper_id)' if has_pdf else 'no'}  "
        f"OCR/extracted text cached: {'yes' if has_ocr else 'no — extract_pdf_text will generate and cache it on first call'}",
    ]
    if paper.abstract:
        parts.append(f"Abstract:\n{paper.abstract}")

    summary = SummaryRepository(db).get_latest(paper_id)
    if summary:
        if summary.tldr:
            parts.append(f"TLDR: {summary.tldr}")
        if summary.methodology:
            parts.append(f"Methodology:\n{summary.methodology}")
        if summary.contributions:
            parts.append("Contributions:\n" + "\n".join(f"- {c}" for c in summary.contributions))
        if summary.key_results:
            parts.append("Key results:\n" + "\n".join(f"- {r}" for r in summary.key_results))
        if summary.limitations:
            parts.append("Limitations:\n" + "\n".join(f"- {l}" for l in summary.limitations))
        if summary.related_work_context:
            parts.append(f"Related work context:\n{summary.related_work_context}")

    if has_ocr:
        ocr_text = PDFOCRPipeline.load_text(
            Path(orm.local_pdf_path), paper_id, ocr_dir=settings.ocr_dir
        )
        if ocr_text:
            truncated = ocr_text[:_OCR_CHAT_LIMIT]
            suffix = (
                f"\n[…truncated — {len(ocr_text):,} chars total]"
                if len(ocr_text) > _OCR_CHAT_LIMIT
                else ""
            )
            parts.append(
                f"Full extracted text (first {_OCR_CHAT_LIMIT:,} chars):\n{truncated}{suffix}"
            )

    return "\n\n".join(parts)


def _ensure_chat(chat_id: str, paper_id: str | None, db: Session) -> ChatORM:
    """Return existing chat or create a new one."""
    chat = db.get(ChatORM, chat_id)
    if chat is None:
        chat = ChatORM(id=chat_id, user_id="default", paper_id=paper_id)
        db.add(chat)
        db.flush()
    return chat


def _auto_title(text: str) -> str:
    words = text.split()
    title = " ".join(words[:8])
    return (title + "…") if len(words) > 8 else title


@router.post("/chats", status_code=201)
def new_chat(body: dict | None = None, db: Session = Depends(get_db)) -> dict:
    paper_id: str | None = (body or {}).get("paper_id")
    chat_id = str(_uuid.uuid4())
    chat = ChatORM(id=chat_id, user_id="default", paper_id=paper_id)
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat.to_dict()


@router.get("/chats/{chat_id}/messages")
def get_chat_messages(chat_id: str, db: Session = Depends(get_db)) -> list:
    chat = db.get(ChatORM, chat_id)
    if chat is None:
        return []
    result = []
    pending_tools: list[dict] | None = None
    for m in chat.messages:
        if m.role == "tool_block":
            try:
                pending_tools = json.loads(m.content or "[]")
            except Exception:
                pending_tools = []
        elif m.role == "agent":
            d = m.to_dict()
            if pending_tools:
                d["tools"] = [
                    {
                        "id": t.get("call_id", ""),
                        "tool": t.get("tool", ""),
                        "input": json.loads(t["args_str"]) if t.get("args_str") else {},
                        "result": t.get("result") or "",
                        "streaming": False,
                    }
                    for t in pending_tools
                ]
                pending_tools = None
            result.append(d)
        else:
            result.append(m.to_dict())
    return result


@router.post("/chats/{chat_id}/messages/stream")
async def stream_chat_message(
    chat_id: str,
    body: dict,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """SSE streaming chat (non-agentic, direct LLM call)."""
    text = (body.get("text") or "").strip()
    if not text:

        async def _empty():
            yield f"data: {json.dumps({'type': 'content', 'delta': 'Please ask a question.'})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        return StreamingResponse(_empty(), media_type="text/event-stream")

    paper_id: str | None = body.get("paper_id")
    mode: str = body.get("mode", "paper")
    images: list[str] = body.get("images") or []

    chat = _ensure_chat(chat_id, paper_id, db)
    if not chat.messages:
        chat.title = _auto_title(text)

    MAX_HISTORY = 20
    history_messages: list[Message] = []
    for m in chat.messages[-(MAX_HISTORY * 2) :]:
        if m.role in ("user", "agent"):
            history_messages.append(
                Message(
                    role="user" if m.role == "user" else "assistant",
                    content=m.content or "",
                )
            )

    user_msg = ChatMessageORM(
        chat_id=chat_id,
        role="user",
        content=text,
        images_json=json.dumps(images) if images else None,
    )
    db.add(user_msg)
    db.commit()

    all_messages = history_messages + [Message(role="user", content=text, images=images)]

    from ...processing.prompts import load as load_prompt

    if mode == "general":
        system = load_prompt("general_system").template
    else:
        system = load_prompt("chat_system").template
        if paper_id:
            ctx = _build_paper_context(paper_id, db)
            if ctx:
                system += "\n\n=== ACTIVE PAPER ===\n" + ctx

    settings = get_settings()

    async def event_gen():
        thinking_acc: list[str] = []
        content_acc: list[str] = []
        try:
            client = create_llm_client(settings)
            if hasattr(client, "stream_chat"):
                async for chunk in client.stream_chat(
                    all_messages,
                    system_prompt=system,
                    max_tokens=1024,
                ):
                    if chunk.get("type") == "thinking":
                        thinking_acc.append(chunk["delta"])
                    elif chunk.get("type") == "content":
                        content_acc.append(chunk["delta"])
                    yield f"data: {json.dumps(chunk)}\n\n"
            else:
                response = await client.chat(
                    all_messages,
                    system_prompt=system,
                    max_tokens=1024,
                )
                if response.thinking:
                    thinking_acc.append(response.thinking)
                    yield f"data: {json.dumps({'type': 'thinking', 'delta': response.thinking})}\n\n"
                content_acc.append(response.content)
                yield f"data: {json.dumps({'type': 'content', 'delta': response.content})}\n\n"
        except Exception as exc:
            logger.error("Stream chat error: %s", exc)
            yield f"data: {json.dumps({'type': 'error', 'delta': str(exc)})}\n\n"
        finally:
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            full_content = "".join(content_acc)
            full_thinking = "".join(thinking_acc) or None
            if full_content:
                try:
                    _settings = get_settings()
                    _engine = build_engine(_settings.database.sqlite_path)
                    _factory = build_session_factory(_engine)
                    with get_session(_factory) as _sess:
                        _sess.add(
                            ChatMessageORM(
                                chat_id=chat_id,
                                role="agent",
                                content=full_content,
                                thinking=full_thinking,
                            )
                        )
                except Exception as save_exc:
                    logger.warning("Failed to save agent message: %s", save_exc)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chats/{chat_id}/messages/agent-stream")
async def agent_stream_chat(
    chat_id: str,
    body: dict,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Agentic SSE endpoint: OpenAI Agents SDK → local vLLM / Ollama."""
    text = (body.get("text") or "").strip()
    if not text:

        async def _empty():
            yield f"data: {json.dumps({'type': 'content', 'delta': 'Please ask a question.'})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        return StreamingResponse(_empty(), media_type="text/event-stream")

    paper_id: str | None = body.get("paper_id")
    enable_thinking: bool = bool(body.get("thinking", True))
    images: list[str] = body.get("images") or []
    settings = get_settings()

    chat = _ensure_chat(chat_id, paper_id, db)
    if not chat.messages:
        chat.title = _auto_title(text)
    db.add(
        ChatMessageORM(
            chat_id=chat_id,
            role="user",
            content=text,
            images_json=json.dumps(images) if images else None,
        )
    )
    db.commit()

    history: list[dict] = []
    for m in chat.messages[-40:]:
        if m.role == "tool_block":
            try:
                tool_data = json.loads(m.content or "[]")
                for d in tool_data:
                    cid = (
                        d["call_id"] if d["call_id"].startswith("call_") else f"call_{d['call_id']}"
                    )
                    history.append(
                        {
                            "type": "function_call",
                            "call_id": cid,
                            "name": d["tool"],
                            "arguments": d["args_str"],
                        }
                    )
                    history.append(
                        {
                            "type": "function_call_output",
                            "call_id": cid,
                            "output": d["result"] or "",
                        }
                    )
            except Exception:
                pass
        elif m.role in ("user", "agent"):
            role = "assistant" if m.role == "agent" else "user"
            history.append({"role": role, "content": m.content or ""})

    paper_ctx: str | None = None
    if paper_id:
        paper_ctx = _build_paper_context(paper_id, db)

    from ..agent_loop import run_agent_stream
    from ..agent_tools import set_active_paper

    set_active_paper(paper_id)

    async def event_gen():
        content_acc: list[str] = []
        thinking_acc: list[str] = []
        tool_block: list[dict] = []
        pending_calls: dict[str, dict] = {}
        error_message: str | None = None
        try:
            async for event in run_agent_stream(
                user_text=text,
                history=history,
                paper_context=paper_ctx,
                settings=settings,
                enable_thinking=enable_thinking,
                images=images or None,
            ):
                etype = event.get("type")
                if etype == "content":
                    content_acc.append(event.get("delta", ""))
                elif etype == "thinking":
                    thinking_acc.append(event.get("delta", ""))
                elif etype == "tool_call":
                    cid = event.get("id", "")
                    pending_calls[cid] = {
                        "call_id": cid,
                        "tool": event.get("tool", ""),
                        "args_str": json.dumps(event.get("input") or {}),
                        "result": None,
                    }
                elif etype == "tool_result":
                    cid = event.get("id", "")
                    if cid in pending_calls:
                        pending_calls[cid]["result"] = event.get("content") or ""
                        tool_block.append(pending_calls.pop(cid))
                elif etype == "error":
                    error_message = event.get("message") or "Unknown agent error"
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:
            logger.error("agent_stream error: %s", exc)
            error_message = str(exc)
            yield f"data: {json.dumps({'type': 'error', 'message': error_message})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        finally:
            full_content = "".join(content_acc)
            full_thinking = "".join(thinking_acc) or None
            save_content = full_content or (
                f"[Error: {error_message}]" if error_message and not tool_block else None
            )
            if save_content or tool_block:
                try:
                    _engine = build_engine(settings.database.sqlite_path)
                    _factory = build_session_factory(_engine)
                    with get_session(_factory) as _sess:
                        if tool_block:
                            _sess.add(
                                ChatMessageORM(
                                    chat_id=chat_id,
                                    role="tool_block",
                                    content=json.dumps(tool_block),
                                )
                            )
                        if save_content:
                            _sess.add(
                                ChatMessageORM(
                                    chat_id=chat_id,
                                    role="agent",
                                    content=save_content,
                                    thinking=full_thinking,
                                )
                            )
                except Exception as save_exc:
                    logger.warning("Failed to save agent message: %s", save_exc)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chats/{chat_id}/messages")
async def send_chat_message(chat_id: str, body: dict, db: Session = Depends(get_db)) -> dict:
    text = (body.get("text") or "").strip()
    if not text:
        return {"role": "agent", "text": "Please ask a question.", "tools": [], "papers": []}

    paper_id: str | None = body.get("paper_id")

    chat = _ensure_chat(chat_id, paper_id, db)
    if not chat.messages:
        chat.title = _auto_title(text)
    user_msg = ChatMessageORM(chat_id=chat_id, role="user", content=text)
    db.add(user_msg)
    db.commit()

    from ...processing.prompts import load as load_prompt

    system = load_prompt("chat_system").template
    if paper_id:
        ctx = _build_paper_context(paper_id, db)
        if ctx:
            system += "\n\n=== ACTIVE PAPER ===\n" + ctx

    settings = get_settings()
    try:
        client = create_llm_client(settings)
        response = await client.chat(
            [Message(role="user", content=text)],
            system_prompt=system,
            max_tokens=1024,
        )
        agent_msg = ChatMessageORM(
            chat_id=chat_id,
            role="agent",
            content=response.content,
            thinking=response.thinking or None,
        )
        db.add(agent_msg)
        db.commit()
        return {
            "role": "agent",
            "text": response.content,
            "thinking": response.thinking or None,
            "tools": [],
            "papers": [],
        }
    except Exception as exc:
        logger.error("Chat LLM error: %s", exc)
        return {
            "role": "agent",
            "text": f"LLM error — check that your provider is reachable. ({exc})",
            "tools": [],
            "papers": [],
        }
