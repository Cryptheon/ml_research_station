"""PubMed fetcher using NCBI E-utilities.

API docs: https://www.ncbi.nlm.nih.gov/books/NBK25499/
Endpoints:
  esearch.fcgi  — keyword → PMID list
  efetch.fcgi   — PMIDs → XML metadata with abstracts

Rate limits: 3 req/s without API key, 10 req/s with one.
Set PUBMED_API_KEY in .env to unlock the higher rate and get better
error messages from NCBI.

Paper IDs use the `pubmed:` prefix, e.g. `pubmed:38123456`.
PMC open-access PDFs are linked when available (pmc_id present).
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx

from ..config.settings import RateLimitSettings
from ..models.paper import Author, Paper, PaperSource
from .base import BaseFetcher, FetchQuery, FetchResult

logger = logging.getLogger(__name__)

_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_BATCH = 100  # efetch supports up to 10k but XML parsing is slow above ~200


class PubMedFetcher(BaseFetcher):
    """Fetches papers from PubMed via NCBI E-utilities."""

    source_name = "pubmed"

    def __init__(
        self,
        rate_limits: RateLimitSettings,
        api_key: str | None = None,
    ) -> None:
        super().__init__(rate_limits)
        self._api_key = api_key
        self._client = httpx.Client(
            timeout=30.0,
            headers={
                "User-Agent": "MeridianResearchStation/0.1 (personal; mailto:research@localhost)"
            },
        )

    def fetch(self, query: FetchQuery) -> FetchResult:
        result = FetchResult(source=self.source_name)
        if not query.keywords and not query.categories:
            return result

        term = self._build_term(query)
        end = query.end_date or datetime.now(tz=timezone.utc)
        start = query.start_date or end

        date_filter = f"{start.strftime('%Y/%m/%d')}:{end.strftime('%Y/%m/%d')}[edat]"
        full_term = f"({term}) AND {date_filter}"

        logger.info("PubMed search: %r (max=%d)", full_term, query.max_results)

        pmids = self._esearch(full_term, query.max_results)
        if not pmids:
            logger.info("PubMed: no results for %r", full_term)
            return result

        logger.info("PubMed: found %d PMIDs, fetching metadata", len(pmids))

        for i in range(0, len(pmids), _BATCH):
            batch = pmids[i : i + _BATCH]
            self._throttle(self.rate_limits.pubmed_delay_seconds)
            try:
                papers = self._efetch(batch)
                result.papers.extend(papers)
            except Exception as exc:
                logger.error("PubMed efetch batch %d–%d failed: %s", i, i + _BATCH, exc)
                result.errors.append(str(exc))

        logger.info("PubMed: collected %d papers", result.count)
        return result

    # ── Private ───────────────────────────────────────────────────────────────

    def _build_term(self, query: FetchQuery) -> str:
        # Only use free-text keywords as title/abstract searches.
        # query.categories are arXiv codes (cs.LG etc.) — not valid MeSH terms.
        parts = [f"{kw}[tiab]" for kw in query.keywords if kw.strip()]
        return " OR ".join(parts) if parts else "all[sb]"

    def _esearch(self, term: str, max_results: int) -> list[str]:
        params: dict[str, str] = {
            "db": "pubmed",
            "term": term,
            "retmax": str(min(max_results, 10_000)),
            "retmode": "json",
            "sort": "date",
        }
        if self._api_key:
            params["api_key"] = self._api_key

        self._throttle(self.rate_limits.pubmed_delay_seconds)
        resp = self._client.get(f"{_EUTILS}/esearch.fcgi", params=params)
        resp.raise_for_status()
        data = resp.json()
        return data.get("esearchresult", {}).get("idlist", [])

    def _efetch(self, pmids: list[str]) -> list[Paper]:
        params: dict[str, str] = {
            "db": "pubmed",
            "id": ",".join(pmids),
            "rettype": "abstract",
            "retmode": "xml",
        }
        if self._api_key:
            params["api_key"] = self._api_key

        resp = self._client.get(f"{_EUTILS}/efetch.fcgi", params=params)
        resp.raise_for_status()
        return _parse_pubmed_xml(resp.text)

    def close(self) -> None:
        self._client.close()


# ── XML parsing ───────────────────────────────────────────────────────────────


def _parse_pubmed_xml(xml_text: str) -> list[Paper]:
    papers: list[Paper] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.error("PubMed XML parse error: %s", exc)
        return papers

    for article in root.findall(".//PubmedArticle"):
        try:
            paper = _article_to_paper(article)
            if paper:
                papers.append(paper)
        except Exception as exc:
            logger.warning("Skipping malformed PubMed article: %s", exc)

    return papers


def _article_to_paper(node: ET.Element) -> Paper | None:
    pmid_el = node.find(".//PMID")
    if pmid_el is None or not pmid_el.text:
        return None
    pmid = pmid_el.text.strip()

    med = node.find("MedlineCitation/Article")
    if med is None:
        return None

    title_el = med.find("ArticleTitle")
    title = "".join(title_el.itertext()).strip() if title_el is not None else ""
    if not title:
        return None

    # Abstract (may have multiple AbstractText sections)
    abstract_parts: list[str] = []
    for at in med.findall(".//AbstractText"):
        label = at.get("Label")
        text = "".join(at.itertext()).strip()
        if text:
            abstract_parts.append(f"{label}: {text}" if label else text)
    abstract = "\n".join(abstract_parts) or None

    # Authors
    authors: list[Author] = []
    for auth in med.findall(".//Author"):
        last = (auth.findtext("LastName") or "").strip()
        first = (auth.findtext("ForeName") or auth.findtext("Initials") or "").strip()
        name = f"{last}, {first}".strip(", ") if last else first
        if name:
            authors.append(Author(name=name))

    # Journal + date
    journal_el = med.find("Journal")
    venue = ""
    if journal_el is not None:
        venue = (
            journal_el.findtext("ISOAbbreviation") or journal_el.findtext("Title") or ""
        ).strip()

    pub_date = _extract_date(med)

    # DOI
    doi: str | None = None
    for id_el in node.findall(".//ArticleId"):
        if id_el.get("IdType") == "doi":
            doi = (id_el.text or "").strip() or None
            break

    # PMC ID → open-access PDF
    pmc_id: str | None = None
    pdf_url: str | None = None
    for id_el in node.findall(".//ArticleId"):
        if id_el.get("IdType") == "pmc":
            pmc_id = (id_el.text or "").strip() or None
            break
    if pmc_id:
        # NCBI XML gives a bare integer; normalise to PMC-prefixed accession
        pmc_acc = pmc_id if pmc_id.upper().startswith("PMC") else f"PMC{pmc_id}"
        # EuropePMC is more accessible than NCBI (no Cloudflare / session auth)
        pdf_url = f"https://europepmc.org/backend/ptpmcrender.fcgi?accid={pmc_acc}&blobtype=pdf"

    # Categories via MeSH headings
    categories = [
        mh.findtext("DescriptorName") or ""
        for mh in node.findall(".//MeshHeading")
        if mh.findtext("DescriptorName")
    ]

    paper_id = f"pubmed:{pmid}"

    return Paper(
        id=paper_id,
        title=title,
        abstract=abstract,
        authors=authors,
        categories=categories[:10],  # cap MeSH list
        source=PaperSource.PUBMED,
        venue=venue,
        published_date=pub_date,
        updated_date=pub_date,
        pdf_url=pdf_url,
        doi=doi,
        raw_metadata={
            "pmid": pmid,
            "pmc_id": pmc_id,
        },
    )


def _extract_date(med: ET.Element) -> datetime:
    """Best-effort date extraction: PubDate > ArticleDate > now."""
    for tag in ("PubDate", "ArticleDate"):
        el = med.find(f".//{tag}")
        if el is None:
            continue
        year = el.findtext("Year")
        month = el.findtext("Month") or "Jan"
        day = el.findtext("Day") or "1"
        if year:
            try:
                return datetime.strptime(f"{year} {month} {day}", "%Y %b %d").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                try:
                    return datetime.strptime(f"{year} {month}", "%Y %b").replace(
                        tzinfo=timezone.utc
                    )
                except ValueError:
                    pass
            try:
                return datetime(int(year), 1, 1, tzinfo=timezone.utc)
            except ValueError:
                pass
    return datetime.now(tz=timezone.utc)
