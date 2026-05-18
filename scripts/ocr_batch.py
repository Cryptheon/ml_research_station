#!/usr/bin/env python3
"""Batch PDF OCR using a vLLM-served vision model.

Renders every PDF in a directory page-by-page and sends the images to a
running vLLM OpenAI-compatible server (Nanonets-OCR, PaddleOCR-VL,
DeepSeek-OCR, or any other vision model).  One JSON file is written per
PDF in the output directory.

Output JSON schema
------------------
  {
    "pdf":             "paper.pdf",
    "model":           "nanonets/Nanonets-OCR2-1.5B-exp",
    "server":          "http://localhost:8000",
    "dpi":             150,
    "page_count":      10,
    "pages": [
      {"page": 1, "text": "Introduction ...", "error": null},
      {"page": 2, "text": "...",              "error": null},
      ...
    ],
    "status":          "success",   # "success" | "partial" | "error"
    "elapsed_seconds": 42.1
  }

Usage
-----
  # Minimal — auto-detects the model loaded in the vLLM server:
  python scripts/ocr_batch.py --input-dir data/pdfs

  # Full example:
  python scripts/ocr_batch.py \\
      --input-dir   data/pdfs \\
      --output-dir  data/ocr_json \\
      --server      http://localhost:8000 \\
      --model       nanonets/Nanonets-OCR2-1.5B-exp \\
      --max-pages   40 \\
      --dpi         150 \\
      --concurrency 4 \\
      --overwrite

Requirements
------------
  pip install pymupdf httpx rich
  (all already included in the project's [pdf] and [api] extras)
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import sys
import time
from functools import partial
from pathlib import Path

import httpx
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

logger = logging.getLogger(__name__)
console = Console(stderr=True)

# ── Defaults ──────────────────────────────────────────────────────────────────

DEFAULT_SERVER = "http://localhost:8000"
DEFAULT_DPI = 150
DEFAULT_MAX_PAGES = 40
DEFAULT_MAX_TOKENS = 4096
DEFAULT_CONCURRENCY = 4  # concurrent page requests per PDF
JPEG_QUALITY = 85
REQUEST_TIMEOUT = 300.0  # seconds — vision inference is slow

# ── OCR prompt ────────────────────────────────────────────────────────────────

OCR_PROMPT = (
    "Transcribe every piece of text visible on this document page exactly as it appears. "
    "Include body text, headings, footnotes, captions, and reference entries. "
    "Return tables in HTML format. "
    "Return equations in LaTeX. "
    "If a region contains an image and a caption is present, place the caption inside "
    "<img></img>; if no caption is present, write a brief description inside <img></img>. "
    "Wrap watermarks in <watermark></watermark>. "
    "Wrap page numbers in <page_number></page_number>. "
    "Use ☐ and ☑ for checkboxes. "
    "If the page is a pure figure with no readable text, write [figure]. "
    "If the page is blank, write [blank]. "
    "Output only the transcribed content — no commentary or preamble."
)


# ── PDF rendering (sync — runs in a thread executor) ──────────────────────────


def render_pdf_pages(
    pdf_path: Path,
    *,
    dpi: int = DEFAULT_DPI,
    max_pages: int = DEFAULT_MAX_PAGES,
    image_format: str = "png",
) -> list[bytes]:
    """Render up to *max_pages* pages of a PDF to raw image bytes.

    Returns a list of image bytes, one element per page (zero-indexed).
    Raises ``ImportError`` if pymupdf is not installed, ``RuntimeError`` on
    any PDF-level error.
    """
    try:
        import fitz  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ImportError("pymupdf is required for PDF rendering: uv pip install pymupdf") from exc

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        raise RuntimeError(f"Cannot open PDF {pdf_path.name}: {exc}") from exc

    n = min(doc.page_count, max_pages)
    scale = dpi / 72.0
    matrix = fitz.Matrix(scale, scale)
    pages: list[bytes] = []

    for i in range(n):
        page = doc.load_page(i)
        pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB)
        if image_format == "png":
            pages.append(pix.tobytes(output="png"))
        else:
            pages.append(pix.tobytes(output="jpeg", jpg_quality=JPEG_QUALITY))

    doc.close()
    return pages


# ── vLLM helpers ──────────────────────────────────────────────────────────────


async def resolve_model(base_url: str) -> str:
    """Query the vLLM /v1/models endpoint and return the first model ID.

    Raises ``RuntimeError`` if the server is unreachable or returns no models.
    """
    url = base_url.rstrip("/") + "/v1/models"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            raise RuntimeError(f"Cannot reach vLLM server at {base_url}: {exc}") from exc

    models = data.get("data", [])
    if not models:
        raise RuntimeError(f"vLLM server at {base_url} reports no loaded models.")
    return str(models[0]["id"])


async def ocr_page(
    image_bytes: bytes,
    page_num: int,
    *,
    client: httpx.AsyncClient,
    base_url: str,
    model: str,
    prompt: str,
    max_tokens: int,
    semaphore: asyncio.Semaphore,
    image_format: str,
) -> str:
    """Send one page image to the vLLM server and return extracted text.

    Raises ``RuntimeError`` on HTTP errors so the caller can record the failure
    per-page without aborting the rest of the PDF.
    """
    mime = "image/png" if image_format == "png" else "image/jpeg"
    data_uri = f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"

    payload: dict[str, object] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_uri}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "stream": False,
    }

    async with semaphore:
        resp = await client.post(f"{base_url.rstrip('/')}/v1/chat/completions", json=payload)

    if not resp.is_success:
        try:
            err_body = resp.json()
        except Exception:
            err_body = resp.text
        raise RuntimeError(f"HTTP {resp.status_code} on page {page_num + 1}: {err_body}")

    content = str(resp.json()["choices"][0]["message"]["content"] or "").strip()
    return content or "[no text]"


# ── Per-PDF orchestration ─────────────────────────────────────────────────────


async def ocr_pdf(
    pdf_path: Path,
    *,
    base_url: str,
    model: str,
    prompt: str,
    dpi: int,
    max_pages: int,
    max_tokens: int,
    concurrency: int,
    image_format: str,
    page_progress: Progress | None = None,
    page_task_id: object = None,
) -> dict[str, object]:
    """OCR one PDF and return its result dict.

    Never raises — errors are captured inside the result so the batch loop
    can continue with remaining PDFs.
    """
    t0 = time.monotonic()

    base_result: dict[str, object] = {
        "pdf": pdf_path.name,
        "model": model,
        "server": base_url,
        "dpi": dpi,
        "page_count": 0,
        "pages": [],
        "status": "error",
        "elapsed_seconds": 0.0,
    }

    # ── Render ────────────────────────────────────────────────────────────────
    loop = asyncio.get_event_loop()
    try:
        page_images: list[bytes] = await loop.run_in_executor(
            None,
            partial(
                render_pdf_pages,
                pdf_path,
                dpi=dpi,
                max_pages=max_pages,
                image_format=image_format,
            ),
        )
    except Exception as exc:
        logger.error("Render failed for %s: %s", pdf_path.name, exc)
        base_result["elapsed_seconds"] = round(time.monotonic() - t0, 2)
        return base_result

    if not page_images:
        logger.warning("No pages extracted from %s", pdf_path.name)
        base_result["elapsed_seconds"] = round(time.monotonic() - t0, 2)
        return base_result

    n_pages = len(page_images)
    base_result["page_count"] = n_pages

    if page_progress is not None and page_task_id is not None:
        page_progress.update(page_task_id, total=n_pages, completed=0)

    # ── OCR pages concurrently ────────────────────────────────────────────────
    sem = asyncio.Semaphore(concurrency)

    async def _tracked_ocr(img: bytes, i: int) -> str:
        result = await ocr_page(
            img,
            i,
            client=http_client,
            base_url=base_url,
            model=model,
            prompt=prompt,
            max_tokens=max_tokens,
            semaphore=sem,
            image_format=image_format,
        )
        if page_progress is not None and page_task_id is not None:
            page_progress.advance(page_task_id)
        return result

    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT,
        headers={"Authorization": "Bearer EMPTY"},
    ) as http_client:
        tasks = [_tracked_ocr(img, i) for i, img in enumerate(page_images)]
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)

    # ── Assemble pages ────────────────────────────────────────────────────────
    pages: list[dict[str, object]] = []
    error_count = 0

    for i, outcome in enumerate(outcomes):
        if isinstance(outcome, BaseException):
            logger.warning("Page %d/%d of %s failed: %s", i + 1, n_pages, pdf_path.name, outcome)
            pages.append({"page": i + 1, "text": None, "error": str(outcome)})
            error_count += 1
        else:
            pages.append({"page": i + 1, "text": outcome, "error": None})

    if error_count == 0:
        status = "success"
    elif error_count < n_pages:
        status = "partial"
    else:
        status = "error"

    return {
        **base_result,
        "pages": pages,
        "status": status,
        "elapsed_seconds": round(time.monotonic() - t0, 2),
    }


# ── Batch driver ──────────────────────────────────────────────────────────────


async def process_batch(
    pdf_paths: list[Path],
    output_dir: Path,
    *,
    base_url: str,
    model: str,
    prompt: str,
    dpi: int,
    max_pages: int,
    max_tokens: int,
    concurrency: int,
    image_format: str,
    overwrite: bool,
) -> tuple[int, int, int]:
    """Process all PDFs sequentially and return (success, partial, error) counts."""
    output_dir.mkdir(parents=True, exist_ok=True)

    success = partial_ok = errors = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    ) as progress:
        pdf_task = progress.add_task("PDFs", total=len(pdf_paths))
        page_task = progress.add_task("Pages", total=None)

        for pdf_path in pdf_paths:
            out_path = output_dir / f"{pdf_path.stem}.json"

            if out_path.exists() and not overwrite:
                logger.info("Skipping %s (output exists, use --overwrite)", pdf_path.name)
                progress.advance(pdf_task)
                success += 1
                continue

            progress.update(pdf_task, description=f"[cyan]{pdf_path.name}")
            progress.update(page_task, completed=0, total=None, description="Pages")

            result = await ocr_pdf(
                pdf_path,
                base_url=base_url,
                model=model,
                prompt=prompt,
                dpi=dpi,
                max_pages=max_pages,
                max_tokens=max_tokens,
                concurrency=concurrency,
                image_format=image_format,
                page_progress=progress,
                page_task_id=page_task,
            )

            out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

            status = result["status"]
            elapsed = result["elapsed_seconds"]
            n_pages = result["page_count"]

            if status == "success":
                success += 1
                logger.info(
                    "[green]✓[/] %s — %d pages in %.1fs",
                    pdf_path.name,
                    n_pages,
                    elapsed,
                    extra={"markup": True},
                )
            elif status == "partial":
                partial_ok += 1
                logger.warning(
                    "[yellow]~[/] %s — partial success (%d pages, %.1fs)",
                    pdf_path.name,
                    n_pages,
                    elapsed,
                    extra={"markup": True},
                )
            else:
                errors += 1
                logger.error(
                    "[red]✗[/] %s — failed (%.1fs)", pdf_path.name, elapsed, extra={"markup": True}
                )

            progress.advance(pdf_task)

    return success, partial_ok, errors


# ── CLI ───────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ocr_batch.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    io = p.add_argument_group("I/O")
    io.add_argument(
        "--input-dir",
        "-i",
        required=True,
        type=Path,
        metavar="DIR",
        help="Directory containing PDF files to process.",
    )
    io.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        metavar="DIR",
        default=None,
        help="Directory for output JSON files (default: <input-dir>/ocr/).",
    )
    io.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-OCR PDFs whose output JSON already exists (default: skip).",
    )

    srv = p.add_argument_group("vLLM server")
    srv.add_argument(
        "--server",
        "-s",
        default=DEFAULT_SERVER,
        metavar="URL",
        help=f"vLLM server base URL (default: {DEFAULT_SERVER}).",
    )
    srv.add_argument(
        "--model",
        "-m",
        default=None,
        metavar="MODEL_ID",
        help=(
            "Model ID as registered in the vLLM server. If omitted, auto-detected from /v1/models."
        ),
    )

    ocr = p.add_argument_group("OCR")
    ocr.add_argument(
        "--max-pages",
        type=int,
        default=DEFAULT_MAX_PAGES,
        metavar="N",
        help=f"Maximum pages to OCR per PDF (default: {DEFAULT_MAX_PAGES}).",
    )
    ocr.add_argument(
        "--dpi",
        type=int,
        default=DEFAULT_DPI,
        metavar="DPI",
        help=f"Rendering resolution in DPI (default: {DEFAULT_DPI}).",
    )
    ocr.add_argument(
        "--image-format",
        choices=["png", "jpeg"],
        default="png",
        help="Image format sent to the model (default: png; lossless, preferred for accuracy).",
    )
    ocr.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_MAX_TOKENS,
        metavar="N",
        help=f"Max output tokens per page (default: {DEFAULT_MAX_TOKENS}).",
    )
    ocr.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        metavar="N",
        help=(
            f"Max concurrent page requests per PDF (default: {DEFAULT_CONCURRENCY}). "
            "vLLM batches these server-side. Reduce to 1 if the server OOMs."
        ),
    )

    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO).",
    )

    return p


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )

    input_dir: Path = args.input_dir.resolve()
    if not input_dir.is_dir():
        console.print(f"[red]Error:[/] --input-dir {input_dir} is not a directory.", style="bold")
        sys.exit(1)

    pdf_paths = sorted(input_dir.glob("*.pdf"))
    if not pdf_paths:
        console.print(f"[yellow]No PDF files found in {input_dir}[/]")
        sys.exit(0)

    output_dir: Path = args.output_dir.resolve() if args.output_dir else input_dir / "ocr"
    base_url: str = args.server.rstrip("/")

    async def run() -> None:
        # Resolve model if not specified
        model = args.model
        if model is None:
            console.print(f"Auto-detecting model from [cyan]{base_url}[/]…")
            try:
                model = await resolve_model(base_url)
                console.print(f"  → [green]{model}[/]")
            except RuntimeError as exc:
                console.print(f"[red]Error:[/] {exc}")
                sys.exit(1)

        console.rule("[bold]Batch PDF OCR")
        console.print(f"  Input  : [cyan]{input_dir}[/] ({len(pdf_paths)} PDFs)")
        console.print(f"  Output : [cyan]{output_dir}[/]")
        console.print(f"  Server : [cyan]{base_url}[/]")
        console.print(f"  Model  : [cyan]{model}[/]")
        console.print(
            f"  Config : {args.dpi} DPI · max {args.max_pages} pages · "
            f"{args.concurrency}x concurrency · {args.image_format}"
        )
        console.rule()

        t_start = time.monotonic()
        ok, partial_ok, err = await process_batch(
            pdf_paths,
            output_dir,
            base_url=base_url,
            model=model,
            prompt=OCR_PROMPT,
            dpi=args.dpi,
            max_pages=args.max_pages,
            max_tokens=args.max_tokens,
            concurrency=args.concurrency,
            image_format=args.image_format,
            overwrite=args.overwrite,
        )
        elapsed = time.monotonic() - t_start

        console.rule()
        console.print(
            f"Done in [bold]{elapsed:.1f}s[/] — "
            f"[green]{ok} succeeded[/], "
            f"[yellow]{partial_ok} partial[/], "
            f"[red]{err} failed[/]"
        )

        if err > 0:
            sys.exit(1)

    asyncio.run(run())


if __name__ == "__main__":
    main()
