"""Web page ingestion: DOM-first extraction with scroll-based screenshot OCR fallback.

Strategy
--------
1. Playwright fetches the URL and extracts readable text from the DOM
   (innerText of <body>, stripped of nav/footer/script noise).
2. If DOM text is too short (<300 chars) or force_ocr=True, scroll the page
   one viewport at a time, taking a screenshot at each position, until the
   page can no longer be scrolled.  Each viewport screenshot is saved to
   data/web_screenshots/<safe_id>/ and OCR'd individually.
3. The full text is saved to data/ocr/<safe_id>.txt and a PaperORM row is
   upserted with source="web" and CACHE_FULLTEXT set.
4. If a requesting paper_id is supplied, a WebPaperLinkORM association row
   is written.

Paper IDs use the form:  web:<sha256[:12]_of_url>
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

_UNWANTED_TAGS = re.compile(
    r"<(script|style|nav|footer|header|aside)[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_STRIP = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\n{3,}")

# Viewport dimensions used for scroll-based capture
_VIEWPORT_W = 1280
_VIEWPORT_H = 900
# Minimum DOM text length before falling back to OCR
_MIN_DOM_CHARS = 300
# Maximum viewports to capture (safety cap for very long pages)
_MAX_VIEWPORTS = 60
# Milliseconds to wait after each scroll for lazy-loaded content
_SCROLL_SETTLE_MS = 600


def _web_id(url: str) -> str:
    h = hashlib.sha256(url.encode()).hexdigest()[:12]
    return f"web:{h}"


def _safe_filename(paper_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", paper_id)


def _dom_to_text(html: str) -> str:
    """Best-effort HTML → plain text without external deps."""
    text = _UNWANTED_TAGS.sub(" ", html)
    text = _TAG_STRIP.sub(" ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = _WHITESPACE.sub("\n\n", text)
    return text.strip()


class WebPageIngestor:
    """Ingest a web page into the corpus.

    Parameters
    ----------
    ocr_dir:
        Directory where extracted .txt files are stored.
    screenshots_dir:
        Directory where per-page viewport screenshots are stored.
    ocr_backend:
        An optional BaseOCRBackend instance for screenshot-based OCR.
        If None, falls back to DOM extraction only.
    """

    def __init__(self, ocr_dir: Path, screenshots_dir: Path, ocr_backend=None):
        self.ocr_dir = Path(ocr_dir)
        self.ocr_dir.mkdir(parents=True, exist_ok=True)
        self.screenshots_dir = Path(screenshots_dir)
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.ocr_backend = ocr_backend

    def ingest(self, url: str, force_ocr: bool = False) -> dict:
        """Fetch and cache the page text.

        Returns a dict with keys:
          paper_id, title, char_count, method, text_path, screenshots (list of paths).
        """
        paper_id = _web_id(url)
        text_path = self.ocr_dir / f"{_safe_filename(paper_id)}.txt"
        shot_dir = self.screenshots_dir / _safe_filename(paper_id)

        # ── Step 1: DOM extraction (skipped when force_ocr=True) ─────────────
        html, page_title = self._fetch_html(url)
        dom_text = _dom_to_text(html) if html else ""

        if not force_ocr and len(dom_text) >= _MIN_DOM_CHARS:
            text_path.write_text(dom_text, encoding="utf-8")
            return {
                "paper_id": paper_id,
                "title": page_title or url,
                "char_count": len(dom_text),
                "method": "dom",
                "text_path": str(text_path),
                "screenshots": [],
            }

        # ── Step 2: scroll-based screenshot OCR ──────────────────────────────
        if self.ocr_backend is None:
            final = dom_text or f"[Web page: {url}]\n(No readable text extracted)"
            text_path.write_text(final, encoding="utf-8")
            return {
                "paper_id": paper_id,
                "title": page_title or url,
                "char_count": len(final),
                "method": "dom_minimal",
                "text_path": str(text_path),
                "screenshots": [],
            }

        ocr_text, screenshot_paths = self._scroll_and_ocr(url, shot_dir)

        if force_ocr:
            final = ocr_text.strip() or dom_text or f"[Web page: {url}]\n(No text extracted)"
        else:
            combined = dom_text + ("\n\n" + ocr_text if ocr_text else "")
            final = combined.strip() or f"[Web page: {url}]\n(No text extracted)"

        text_path.write_text(final, encoding="utf-8")
        return {
            "paper_id": paper_id,
            "title": page_title or url,
            "char_count": len(final),
            "method": "ocr",
            "text_path": str(text_path),
            "screenshots": [str(p) for p in screenshot_paths],
        }

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _fetch_html(self, url: str) -> tuple[str, str]:
        """Return (html_body, page_title) via Playwright."""
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": _VIEWPORT_W, "height": _VIEWPORT_H})
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                try:
                    page.wait_for_load_state("networkidle", timeout=8_000)
                except Exception:
                    pass
                title = page.title() or ""
                html = page.content()
                browser.close()
            return html, title
        except Exception as exc:
            log.warning("web_ingest: Playwright fetch failed: %s", exc)
            return "", ""

    def _scroll_and_ocr(self, url: str, shot_dir: Path) -> tuple[str, list[Path]]:
        """Scroll through the page viewport by viewport, screenshot + OCR each.

        Returns (combined_ocr_text, list_of_saved_screenshot_paths).
        """
        shot_dir.mkdir(parents=True, exist_ok=True)
        # Remove stale screenshots from a previous run
        for old in shot_dir.glob("viewport_*.jpg"):
            old.unlink(missing_ok=True)

        screenshot_paths: list[Path] = []
        pages_text: list[str] = []

        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": _VIEWPORT_W, "height": _VIEWPORT_H})
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                try:
                    page.wait_for_load_state("networkidle", timeout=8_000)
                except Exception:
                    pass

                # Scroll to top and let the page settle
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(300)

                viewport_idx = 0
                last_scroll_y = -1

                while viewport_idx < _MAX_VIEWPORTS:
                    scroll_y = page.evaluate("window.scrollY")

                    # Stop if scroll position hasn't changed (reached bottom)
                    if scroll_y == last_scroll_y:
                        break
                    last_scroll_y = scroll_y

                    # Take viewport screenshot
                    shot_bytes = page.screenshot(full_page=False)
                    shot_path = shot_dir / f"viewport_{viewport_idx:03d}.jpg"
                    self._save_jpeg(shot_bytes, shot_path)
                    screenshot_paths.append(shot_path)

                    # OCR this viewport — use a fresh event loop so this is safe
                    # whether called from a sync thread or an async executor thread.
                    try:
                        _loop = asyncio.new_event_loop()
                        try:
                            chunk_text = _loop.run_until_complete(
                                self.ocr_backend.extract_page(shot_bytes, viewport_idx)
                            )
                        finally:
                            _loop.close()
                        if chunk_text.strip():
                            pages_text.append(chunk_text.strip())
                    except Exception as exc:
                        log.warning("web_ingest: OCR viewport %d failed: %s", viewport_idx, exc)

                    # Scroll down one viewport and wait for lazy content
                    page.evaluate(f"window.scrollBy(0, {_VIEWPORT_H})")
                    page.wait_for_timeout(_SCROLL_SETTLE_MS)
                    viewport_idx += 1

                browser.close()

        except Exception as exc:
            log.warning("web_ingest: scroll OCR failed: %s", exc)

        return "\n\n".join(pages_text), screenshot_paths

    @staticmethod
    def _save_jpeg(png_bytes: bytes, path: Path) -> None:
        """Convert Playwright PNG screenshot bytes to JPEG and save."""
        try:
            import io

            from PIL import Image

            img = Image.open(io.BytesIO(png_bytes))
            img.save(path, format="JPEG", quality=85, optimize=True)
        except Exception:
            # Fallback: save raw bytes (Playwright can also return JPEG directly)
            path.write_bytes(png_bytes)
