"""PDF → text extraction via a swappable OCR backend.

Architecture
------------
``BaseOCRBackend``    — one-method ABC: extract_page(image_bytes, page_num) → str.
                         Implement this to add Nougat, Tesseract, GROBID, etc.

``VisionLLMOCR``      — backend using an Ollama multimodal model (e.g. gemma4:26b).

``DeepSeekOCRBackend`` — backend using DeepSeek-OCR-2 via a vLLM OpenAI-compat
                         server.  Sends pages concurrently; vLLM batches server-side.
                         Requires the server to be started with the NGram logits
                         processor (see docs/deepseek_ocr_setup.md).

``PDFPageRenderer``   — renders PDF pages to JPEG bytes using pymupdf (fitz).
                         Kept separate so backends can override it (e.g. a
                         dedicated OCR model may want PNG at 300 DPI).

``PDFOCRPipeline``    — orchestrates render → OCR each page → assemble → save.
                         Call ``pipeline.run(pdf_path, paper)`` to produce a
                         ``.txt`` file alongside the PDF and get back the full text.

Text storage
------------
Extracted text is saved as ``<paper_id>.txt`` in the same directory as the PDF.
The ``PaperSummarizer`` checks for this file and uses full text instead of just
the abstract when available.  ``CACHE_FULLTEXT`` (bit 32) is set in cache_flags.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

from ..models.paper import Paper

logger = logging.getLogger(__name__)

# Maximum pages to OCR by default — vision LLMs are slow; keep it practical.
DEFAULT_MAX_PAGES = 40
DEFAULT_DPI = 150  # ~1200×900 for a typical A4 page — good enough for text
JPEG_QUALITY = 85


# ── Abstract interface ────────────────────────────────────────────────────────


class BaseOCRBackend(ABC):
    """Contract every OCR backend must implement.

    A backend receives a single page as raw JPEG bytes and returns the
    extracted plain text for that page.  Page ordering and assembly are
    handled by ``PDFOCRPipeline``.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier used in logs and stored metadata."""
        ...

    @abstractmethod
    async def extract_page(
        self,
        image_bytes: bytes,
        page_num: int,
        *,
        context: str = "",
    ) -> str:
        """Extract text from a single page image.

        Args:
            image_bytes: JPEG-encoded page image.
            page_num:    Zero-based page index (for prompt context).
            context:     Optional paper title/abstract hint to help the model.

        Returns:
            Extracted plain text.  Return ``"[no text]"`` rather than raising
            when extraction yields nothing.
        """
        ...


# ── Concrete: vision LLM via Ollama ──────────────────────────────────────────


class VisionLLMOCR(BaseOCRBackend):
    """OCR backend that uses an Ollama multimodal model (e.g. gemma4:26b).

    Requires the model to be multimodal (accepts images).
    Uses ``Message.images`` to pass the page image as a base64 data URI.
    """

    def __init__(self, ollama_client: object, *, semaphore_limit: int = 1) -> None:
        """
        Args:
            ollama_client:   An ``OllamaClient`` instance (typed as object to
                             avoid a hard import loop; duck-typed at call time).
            semaphore_limit: Max concurrent page requests.  Keep at 1 for a
                             local GPU to avoid OOM; raise for multi-GPU setups.
        """
        self._client = ollama_client
        self._sem = asyncio.Semaphore(semaphore_limit)

    @property
    def name(self) -> str:
        model = getattr(self._client, "_model", "unknown")
        return f"vision-llm/{model}"

    async def extract_page(
        self,
        image_bytes: bytes,
        page_num: int,
        *,
        context: str = "",
    ) -> str:
        from .prompts import render as render_prompt

        context_line = f"Paper context: {context}" if context else ""
        prompt = render_prompt(
            "ocr_page",
            page=page_num + 1,
            context_line=context_line,
        )
        image_b64 = base64.b64encode(image_bytes).decode()
        data_uri = f"data:image/jpeg;base64,{image_b64}"

        from .llm.base import Message  # avoid circular import

        async with self._sem:
            response = await self._client.chat(
                [Message(role="user", content=prompt, images=[data_uri])],
                max_tokens=2048,
            )
        return response.content.strip() or "[no text]"


# ── Concrete: DeepSeek-OCR-2 via vLLM ────────────────────────────────────────


class DeepSeekOCRBackend(BaseOCRBackend):
    """OCR backend using DeepSeek-OCR-2 served via a vLLM OpenAI-compatible server.

    vLLM batches concurrent requests server-side, so we send all pages in
    parallel (controlled by ``semaphore_limit``) and let the server schedule
    them efficiently.

    ``use_ngram_processor`` must match how the server was started:
    - True  → server was launched with --logits_processors NGramPerReqLogitsProcessor
              Adds ``skip_special_tokens=False`` and ``vllm_xargs`` to each request.
              Required for correct table rendering (keeps <td>/<\td> tokens).
    - False → plain vLLM serve with no extra flags (default, simpler setup).

    Args:
        base_url:             vLLM server base URL (e.g. ``http://localhost:8000/v1``).
        model:                Model ID as registered in the vLLM server.
        semaphore_limit:      Max in-flight page requests.
        max_tokens:           Max tokens per page.
        use_ngram_processor:  Set True only when server has NGramPerReqLogitsProcessor.
    """

    _PROMPT = "<image>\n<|grounding|>Convert the document to markdown. "

    _NGRAM_XARGS: dict[str, object] = {
        "ngram_size": 30,
        "window_size": 90,
        "whitelist_token_ids": [128821, 128822],  # <td>, </td>
    }

    def __init__(
        self,
        base_url: str,
        model: str = "deepseek-ai/DeepSeek-OCR-2",
        *,
        semaphore_limit: int = 8,
        max_tokens: int = 2048,
        use_ngram_processor: bool = False,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._sem = asyncio.Semaphore(semaphore_limit)
        self._max_tokens = max_tokens
        self._use_ngram = use_ngram_processor

    @property
    def name(self) -> str:
        suffix = "+ngram" if self._use_ngram else ""
        return f"vllm/{self._model}{suffix}"

    async def extract_page(
        self,
        image_bytes: bytes,
        page_num: int,
        *,
        context: str = "",
    ) -> str:
        import httpx

        image_b64 = base64.b64encode(image_bytes).decode()
        data_uri = f"data:image/jpeg;base64,{image_b64}"

        payload: dict[str, object] = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_uri}},
                        {"type": "text", "text": self._PROMPT},
                    ],
                }
            ],
            "max_tokens": self._max_tokens,
            "temperature": 0.0,
            "stream": False,
        }

        if self._use_ngram:
            # Only include these when the server was launched with NGramPerReqLogitsProcessor.
            # vLLM rejects the request with 400 if the processor is not registered.
            payload["skip_special_tokens"] = False
            payload["vllm_xargs"] = self._NGRAM_XARGS

        async with self._sem:
            async with httpx.AsyncClient(
                timeout=300.0,
                headers={"Authorization": "Bearer EMPTY"},
            ) as client:
                resp = await client.post(f"{self._base_url}/chat/completions", json=payload)
                if not resp.is_success:
                    # Log the response body so we can see what vLLM rejected
                    try:
                        err_body = resp.json()
                    except Exception:
                        err_body = resp.text
                    logger.error(
                        "vLLM returned %s for page %d: %s",
                        resp.status_code,
                        page_num + 1,
                        err_body,
                    )
                    resp.raise_for_status()
                data = resp.json()

        content = str(data["choices"][0]["message"]["content"] or "").strip()
        return content or "[no text]"


# ── Concrete: Nanonets-OCR via vLLM ──────────────────────────────────────────


class NanonetsOCRBackend(BaseOCRBackend):
    """OCR backend using Nanonets-OCR-2 (1.5B/3B) served via vLLM.

    Uses the standard OpenAI-compatible API with no special logits processors.
    Start the server with::

        vllm serve nanonets/Nanonets-OCR2-3B

    Args:
        base_url:           vLLM server base URL.
        model:              Model ID registered in the vLLM server.
        semaphore_limit:    Max concurrent page requests.
        max_tokens:         Max output tokens per page (model supports up to ~15000).
        repetition_penalty: Set to 1.0 for complex tables / financial docs (upstream tip).
    """

    _PROMPT = (
        "Extract the text from the above document as if you were reading it naturally. "
        "Return the tables in html format. "
        "Return the equations in LaTeX representation. "
        "If there is an image in the document and image caption is not present, "
        "add a small description of the image inside the <img></img> tag; "
        "otherwise, add the image caption inside <img></img>. "
        "Watermarks should be wrapped in brackets. Ex: <watermark>OFFICIAL COPY</watermark>. "
        "Page numbers should be wrapped in brackets. "
        "Ex: <page_number>14</page_number> or <page_number>9/22</page_number>. "
        "Prefer using ☐ and ☑ for check boxes."
    )

    def __init__(
        self,
        base_url: str,
        model: str = "nanonets/Nanonets-OCR2-3B",
        *,
        semaphore_limit: int = 1,
        max_tokens: int = 4096,
        repetition_penalty: float | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._sem = asyncio.Semaphore(semaphore_limit)
        self._max_tokens = max_tokens
        self._repetition_penalty = repetition_penalty

    @property
    def name(self) -> str:
        return f"vllm/{self._model}"

    async def extract_page(
        self,
        image_bytes: bytes,
        page_num: int,
        *,
        context: str = "",
    ) -> str:
        import httpx

        # Nanonets recommends PNG for best accuracy
        image_b64 = base64.b64encode(image_bytes).decode()
        data_uri = f"data:image/png;base64,{image_b64}"

        payload: dict[str, object] = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_uri}},
                        {"type": "text", "text": self._PROMPT},
                    ],
                }
            ],
            "max_tokens": self._max_tokens,
            "temperature": 0.0,
            "stream": False,
        }
        if self._repetition_penalty is not None:
            payload["repetition_penalty"] = self._repetition_penalty

        async with self._sem:
            async with httpx.AsyncClient(
                timeout=300.0,
                headers={"Authorization": "Bearer EMPTY"},
            ) as client:
                resp = await client.post(f"{self._base_url}/chat/completions", json=payload)
                if not resp.is_success:
                    try:
                        err_body = resp.json()
                    except Exception:
                        err_body = resp.text
                    logger.error(
                        "vLLM returned %s for page %d: %s",
                        resp.status_code,
                        page_num + 1,
                        err_body,
                    )
                    resp.raise_for_status()
                data = resp.json()

        content = str(data["choices"][0]["message"]["content"] or "").strip()
        return content or "[no text]"


# ── Concrete: Qwen3.5-VL / Qwen2.5-VL via Ollama OpenAI-compat API ───────────


class QwenVLOCRBackend(BaseOCRBackend):
    """OCR backend using Qwen3.5-VL (or Qwen2.5-VL) served via Ollama.

    Uses Ollama's OpenAI-compatible endpoint (``/v1/chat/completions``) with
    the sampling parameters recommended in Qwen's documentation:
    temperature=1.0, top_p=0.95, presence_penalty=1.5.

    Pull the model first::

        ollama pull qwen2.5vl:7b   # or qwen3.5vl:4b once available

    Args:
        base_url:         Ollama base URL (e.g. ``http://localhost:11434``).
                          The ``/v1`` suffix is appended automatically.
        model:            Ollama model name (e.g. ``qwen2.5vl:7b``).
        semaphore_limit:  Max concurrent page requests (keep 1 on a single GPU).
        max_tokens:       Max output tokens per page.
    """

    def __init__(
        self,
        base_url: str,
        model: str = "qwen2.5vl:7b",
        *,
        semaphore_limit: int = 1,
        max_tokens: int = 2048,
    ) -> None:
        # Ollama's OpenAI-compat endpoint lives at /v1
        self._base_url = base_url.rstrip("/").removesuffix("/v1")
        self._model = model
        self._sem = asyncio.Semaphore(semaphore_limit)
        self._max_tokens = max_tokens

    @property
    def name(self) -> str:
        return f"ollama/{self._model}"

    async def extract_page(
        self,
        image_bytes: bytes,
        page_num: int,
        *,
        context: str = "",
    ) -> str:
        import httpx

        from .prompts import render as render_prompt

        context_line = f"Paper context: {context}" if context else ""
        prompt = render_prompt("ocr_page", page=page_num + 1, context_line=context_line)

        image_b64 = base64.b64encode(image_bytes).decode()
        data_uri = f"data:image/jpeg;base64,{image_b64}"

        payload: dict[str, object] = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_uri}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "max_tokens": self._max_tokens,
            "temperature": 1.0,
            "top_p": 0.95,
            "presence_penalty": 1.5,
            "stream": False,
        }

        async with self._sem:
            async with httpx.AsyncClient(
                timeout=300.0,
                headers={"Authorization": "Bearer ollama"},
            ) as client:
                resp = await client.post(f"{self._base_url}/v1/chat/completions", json=payload)
                if not resp.is_success:
                    try:
                        err_body = resp.json()
                    except Exception:
                        err_body = resp.text
                    logger.error(
                        "Ollama returned %s for page %d: %s",
                        resp.status_code,
                        page_num + 1,
                        err_body,
                    )
                    resp.raise_for_status()
                data = resp.json()

        content = str(data["choices"][0]["message"]["content"] or "").strip()
        return content or "[no text]"


# ── PDF rendering ─────────────────────────────────────────────────────────────


class PDFPageRenderer:
    """Renders PDF pages to image bytes using pymupdf (fitz).

    ``image_format`` can be ``"jpeg"`` (smaller, lossy) or ``"png"`` (lossless,
    preferred by Nanonets-OCR for best accuracy).
    """

    def __init__(
        self,
        dpi: int = DEFAULT_DPI,
        max_pages: int = DEFAULT_MAX_PAGES,
        image_format: str = "jpeg",
    ) -> None:
        self.dpi = dpi
        self.max_pages = max_pages
        self.image_format = image_format.lower()

    @property
    def mime_type(self) -> str:
        return "image/png" if self.image_format == "png" else "image/jpeg"

    def render(self, pdf_path: Path) -> list[bytes]:
        """Return image bytes for each page (up to max_pages)."""
        try:
            import fitz  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "pymupdf is required for PDF rendering. Install it with: uv pip install -e '.[pdf]'"
            ) from exc

        doc = fitz.open(str(pdf_path))
        pages: list[bytes] = []
        n = min(doc.page_count, self.max_pages)
        scale = self.dpi / 72.0
        matrix = fitz.Matrix(scale, scale)

        for i in range(n):
            page = doc.load_page(i)
            pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB)
            if self.image_format == "png":
                img_bytes = pix.tobytes(output="png")
            else:
                img_bytes = pix.tobytes(output="jpeg", jpg_quality=JPEG_QUALITY)
            pages.append(img_bytes)

        doc.close()
        return pages


# ── Pipeline ──────────────────────────────────────────────────────────────────


class PDFOCRPipeline:
    """Orchestrates: render PDF → OCR each page → assemble → save.

    Usage::

        backend = VisionLLMOCR(ollama_client)
        pipeline = PDFOCRPipeline(backend, ocr_dir=Path("data/ocr"))
        text = await pipeline.run(pdf_path, paper)
        # → saves data/ocr/<paper_id_safe>.txt, returns full text
    """

    def __init__(
        self,
        backend: BaseOCRBackend,
        renderer: PDFPageRenderer | None = None,
        ocr_dir: Path | None = None,
    ) -> None:
        self._backend = backend
        self._renderer = renderer or PDFPageRenderer()
        self._ocr_dir = ocr_dir

    async def run(
        self,
        pdf_path: Path,
        paper: Paper,
        *,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> str:
        """Full pipeline: render → OCR → assemble → save.

        Args:
            pdf_path:     Path to the local PDF file.
            paper:        Paper metadata used as context for the OCR prompt.
            on_progress:  Optional sync callable ``(pages_done, pages_total)``
                          called after each page completes.

        Returns:
            Full extracted text (empty string on total failure).
        """
        logger.info("Starting OCR for %s via %s", paper.id, self._backend.name)

        # Render in a thread pool — fitz is synchronous
        loop = asyncio.get_event_loop()
        try:
            page_images = await loop.run_in_executor(None, self._renderer.render, pdf_path)
        except Exception:
            logger.exception("PDF rendering failed for %s", paper.id)
            return ""

        if not page_images:
            logger.warning("No pages rendered for %s", paper.id)
            return ""

        logger.info("Rendered %d pages for %s", len(page_images), paper.id)

        # Short context hint fed into the OCR prompt
        context = f"{paper.title} — {', '.join(a.name for a in paper.authors[:3])}"

        # Wrap each page task to fire progress callback after completion
        total = len(page_images)
        done_count = 0

        async def _tracked(img: bytes, i: int) -> str:
            nonlocal done_count
            result = await self._backend.extract_page(img, i, context=context)
            done_count += 1
            if on_progress is not None:
                try:
                    on_progress(done_count, total)
                except Exception:
                    pass
            return result

        # OCR pages concurrently (semaphore inside backend controls actual concurrency)
        tasks = [_tracked(img, i) for i, img in enumerate(page_images)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        page_texts: list[str] = []
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                logger.warning("OCR page %d failed: %s", i + 1, result)
                page_texts.append(f"--- Page {i + 1} ---\n[OCR failed: {result}]")
            else:
                page_texts.append(f"--- Page {i + 1} ---\n{result}")

        full_text = "\n\n".join(page_texts)

        txt_path = self._text_path(pdf_path, paper.id)
        try:
            txt_path.parent.mkdir(parents=True, exist_ok=True)
            txt_path.write_text(full_text, encoding="utf-8")
            logger.info("Saved OCR text to %s (%.1f KB)", txt_path, len(full_text) / 1024)
        except Exception:
            logger.exception("Failed to save OCR text for %s", paper.id)

        return full_text

    @staticmethod
    def safe_name(paper_id: str) -> str:
        return paper_id.replace(":", "_").replace("/", "_").replace(" ", "_")

    @staticmethod
    def text_path_for(pdf_path: Path, paper_id: str, ocr_dir: Path | None = None) -> Path:
        """Return the `.txt` path: in ``ocr_dir`` if provided, else alongside the PDF."""
        fname = f"{PDFOCRPipeline.safe_name(paper_id)}.txt"
        base = ocr_dir if ocr_dir is not None else pdf_path.parent
        return base / fname

    @staticmethod
    def load_text(pdf_path: Path, paper_id: str, ocr_dir: Path | None = None) -> str | None:
        """Load previously extracted text, searching ocr_dir first then alongside PDF."""
        fname = f"{PDFOCRPipeline.safe_name(paper_id)}.txt"
        candidates: list[Path] = []
        if ocr_dir is not None:
            candidates.append(ocr_dir / fname)
        candidates.append(pdf_path.parent / fname)
        for p in candidates:
            if p.exists():
                return p.read_text(encoding="utf-8")
        return None

    def _text_path(self, pdf_path: Path, paper_id: str) -> Path:
        return self.text_path_for(pdf_path, paper_id, self._ocr_dir)
