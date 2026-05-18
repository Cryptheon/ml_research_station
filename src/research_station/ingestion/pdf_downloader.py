"""PDF download utility with deduplication and partial-download cleanup."""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_CURL_HOSTS = {
    "www.biorxiv.org",
    "www.medrxiv.org",
    "biorxiv.org",
    "medrxiv.org",
    "www.ncbi.nlm.nih.gov",
    "ncbi.nlm.nih.gov",
    "europepmc.org",
    "www.europepmc.org",
}

_CURL = shutil.which("curl")


def _referer_for(pdf_url: str) -> str | None:
    parsed = urlparse(pdf_url)
    if parsed.netloc not in _CURL_HOSTS:
        return None
    abstract_path = parsed.path.replace(".full.pdf", "").replace(".full", "")
    return f"{parsed.scheme}://{parsed.netloc}{abstract_path}"


def _needs_curl(pdf_url: str) -> bool:
    return urlparse(pdf_url).netloc in _CURL_HOSTS


class PDFDownloader:
    """Downloads paper PDFs to a local directory.

    Skips files that already exist so repeated runs are idempotent.
    Cleans up partial files if the download fails mid-stream.
    Uses curl (libcurl TLS) for bioRxiv/medRxiv to bypass Cloudflare fingerprinting.
    """

    def __init__(self, papers_dir: Path) -> None:
        self._papers_dir = papers_dir
        self._papers_dir.mkdir(parents=True, exist_ok=True)
        self._client = httpx.Client(
            timeout=120.0,
            follow_redirects=True,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "application/pdf,*/*;q=0.9",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )

    def download(self, pdf_url: str, paper_id: str, timeout: float = 60.0) -> Path | None:
        """Download *pdf_url* and return the local ``Path``, or ``None`` on failure."""
        dest = self._papers_dir / _safe_filename(paper_id)

        if dest.exists():
            logger.debug("PDF already cached: %s", dest.name)
            return dest

        logger.info("Downloading %s → %s", pdf_url, dest.name)
        tmp = dest.with_suffix(".part")

        try:
            if _needs_curl(pdf_url) and _CURL:
                return self._download_curl(pdf_url, paper_id, dest, tmp, timeout)
            return self._download_httpx(pdf_url, paper_id, dest, tmp, timeout)
        except Exception as exc:
            logger.warning("Download failed for %s: %s", paper_id, exc)
            tmp.unlink(missing_ok=True)
            return None

    def _download_curl(
        self, pdf_url: str, paper_id: str, dest: Path, tmp: Path, timeout: float
    ) -> Path | None:
        referer = _referer_for(pdf_url) or pdf_url
        base_cmd = [
            _CURL,
            "--silent",
            "--show-error",
            "--location",
            "--max-time",
            str(int(timeout)),
            "--user-agent",
            _USER_AGENT,
            "--referer",
            referer,
            "--header",
            "Accept: application/pdf,*/*;q=0.9",
            "--header",
            "Accept-Language: en-US,en;q=0.9",
            "--output",
            str(tmp),
            "--write-out",
            "%{http_code}",
        ]

        for attempt, extra in enumerate([[], ["--http1.1"]]):
            tmp.unlink(missing_ok=True)
            cmd = base_cmd + extra + [pdf_url]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
            http_code = result.stdout.strip()

            # exit 92 = HTTP/2 stream closed unexpectedly; retry with HTTP/1.1
            if result.returncode == 92 and attempt == 0:
                logger.debug("HTTP/2 stream error for %s — retrying with HTTP/1.1", paper_id)
                continue

            if result.returncode != 0 or http_code not in ("200", "206"):
                tmp.unlink(missing_ok=True)
                logger.warning(
                    "curl download failed for %s: exit=%d http=%s stderr=%s",
                    paper_id,
                    result.returncode,
                    http_code,
                    result.stderr[:200],
                )
                return None
            break  # success

        # Verify it's not an HTML paywall response
        if tmp.exists() and tmp.stat().st_size > 100:
            with tmp.open("rb") as fh:
                magic = fh.read(5)
            if magic != b"%PDF-":
                tmp.unlink(missing_ok=True)
                logger.warning("Skipping %s — curl got non-PDF content", paper_id)
                return None

        tmp.rename(dest)
        logger.info("Saved %s (%.1f KB) via curl", dest.name, dest.stat().st_size / 1024)
        return dest

    def _download_httpx(
        self, pdf_url: str, paper_id: str, dest: Path, tmp: Path, timeout: float
    ) -> Path | None:
        extra_headers: dict[str, str] = {}
        referer = _referer_for(pdf_url)
        if referer:
            extra_headers["Referer"] = referer

        for attempt in range(3):
            try:
                with self._client.stream(
                    "GET", pdf_url, timeout=timeout, headers=extra_headers
                ) as response:
                    if response.status_code == 429:
                        retry_after = int(response.headers.get("Retry-After", 5 * (attempt + 1)))
                        logger.warning(
                            "429 for %s — waiting %ds before retry", paper_id, retry_after
                        )
                        time.sleep(retry_after)
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "")
                    if "html" in content_type:
                        logger.warning("Skipping %s — got HTML instead of PDF", paper_id)
                        return None
                    with tmp.open("wb") as fh:
                        for chunk in response.iter_bytes(chunk_size=65536):
                            fh.write(chunk)
                tmp.rename(dest)
                logger.info("Saved %s (%.1f KB)", dest.name, dest.stat().st_size / 1024)
                return dest
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429 and attempt < 2:
                    wait = 5 * (attempt + 1)
                    logger.warning("429 for %s — waiting %ds", paper_id, wait)
                    time.sleep(wait)
                    continue
                raise
        return None

    def close(self) -> None:
        self._client.close()


def _safe_filename(paper_id: str) -> str:
    return paper_id.replace(":", "_").replace("/", "_").replace(" ", "_") + ".pdf"
