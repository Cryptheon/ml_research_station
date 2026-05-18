"""ChromaDB-backed semantic similarity service.

Text fed to ChromaDB: title + abstract (capped at 8 000 chars).
Supports three embedding providers:
  - ollama              calls /api/embed on a local Ollama server
  - sentence_transformers  uses the sentence-transformers library
  - default             uses ChromaDB's built-in ONNX model (no extra deps)
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_service: EmbeddingService | None = None
_MAX_TEXT = 8_000
_CHUNK_SIZE = 1_200  # characters per chunk
_CHUNK_OVERLAP = 200  # overlap between consecutive chunks


def _split_chunks(text: str, size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks, preferring paragraph/sentence breaks."""
    if not text.strip():
        return []
    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + size, n)
        at_end = end == n
        if not at_end:
            halfway = start + size // 2
            for sep in ("\n\n", "\n", ". ", " "):
                pos = text.rfind(sep, halfway, end)
                if pos > halfway:
                    end = pos + len(sep)
                    break
        chunk = text[start:end].strip()
        if len(chunk) > 50:
            chunks.append(chunk)
        if at_end:
            break
        start = max(end - overlap, start + 1)
    return chunks


# ── Embedding functions ───────────────────────────────────────────────────────


class _VllmEF:
    """ChromaDB-compatible embedding function backed by a vLLM /v1/embeddings endpoint."""

    @classmethod
    def name(cls) -> str:
        return "vllm"

    def __init__(self, base_url: str, model: str) -> None:
        # base_url e.g. http://localhost:8888/v1
        self._url = base_url.rstrip("/") + "/embeddings"
        self._model = model

    def __call__(self, input: list[str]) -> list[list[float]]:
        resp = httpx.post(
            self._url,
            json={"model": self._model, "input": input},
            timeout=300,
        )
        resp.raise_for_status()
        data = resp.json()
        # OpenAI-compat: {"data": [{"embedding": [...], "index": N}, ...]}
        items = sorted(data["data"], key=lambda x: x["index"])
        return [item["embedding"] for item in items]


class _OllamaEF:
    """ChromaDB-compatible embedding function backed by Ollama /api/embed."""

    @classmethod
    def name(cls) -> str:
        return "ollama"

    def __init__(self, base_url: str, model: str) -> None:
        self._url = base_url.rstrip("/") + "/api/embed"
        self._model = model

    def __call__(self, input: list[str]) -> list[list[float]]:
        resp = httpx.post(
            self._url,
            json={"model": self._model, "input": input},
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["embeddings"]


def _make_ef(provider: str, model: str, ollama_base_url: str, vllm_base_url: str):
    if provider == "vllm":
        return _VllmEF(vllm_base_url, model)
    if provider == "ollama":
        return _OllamaEF(ollama_base_url, model)
    if provider == "sentence_transformers":
        try:
            from chromadb.utils.embedding_functions import (
                SentenceTransformerEmbeddingFunction,
            )

            return SentenceTransformerEmbeddingFunction(model_name=model)
        except (ImportError, Exception) as exc:
            logger.warning("SentenceTransformer EF failed (%s); falling back to default", exc)
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

    return DefaultEmbeddingFunction()


# ── Service ───────────────────────────────────────────────────────────────────


class EmbeddingService:
    def __init__(
        self,
        provider: str,
        model: str,
        ollama_base_url: str,
        vllm_base_url: str,
        chroma_path: Path,
    ) -> None:
        import chromadb

        ef = _make_ef(provider, model, ollama_base_url, vllm_base_url)
        self._ef = ef
        self._client = chromadb.PersistentClient(path=str(chroma_path))
        self._col = self._client.get_or_create_collection(
            name="papers",
            embedding_function=ef,
            metadata={"hnsw:space": "cosine"},
        )
        self._chunks_col = self._client.get_or_create_collection(
            name="paper_chunks",
            embedding_function=ef,
            metadata={"hnsw:space": "cosine"},
        )

    # ── Write ──────────────────────────────────────────────────────────────

    def embed_paper(self, paper_id: str, text: str) -> None:
        self._col.upsert(ids=[paper_id], documents=[text[:_MAX_TEXT]])

    def embed_chunks(self, paper_id: str, text: str) -> int:
        """Chunk *text* and upsert into the paper_chunks collection. Returns chunk count."""
        chunks = _split_chunks(text)
        if not chunks:
            return 0
        self.delete_chunks(paper_id)
        ids = [f"{paper_id}||{i}" for i in range(len(chunks))]
        metadatas = [{"paper_id": paper_id} for _ in chunks]
        self._chunks_col.upsert(ids=ids, documents=chunks, metadatas=metadatas)
        return len(chunks)

    def delete_chunks(self, paper_id: str) -> None:
        try:
            existing = self._chunks_col.get(where={"paper_id": paper_id})
            if existing["ids"]:
                self._chunks_col.delete(ids=existing["ids"])
        except Exception:
            pass

    def query_chunks(self, question: str, n_results: int = 10) -> list[dict]:
        """Query paper_chunks by semantic similarity. Returns list of chunk dicts."""
        try:
            total = self._chunks_col.count()
            if total == 0:
                return []
            results = self._chunks_col.query(
                query_texts=[question],
                n_results=min(n_results, total),
                include=["documents", "metadatas", "distances"],
            )
            out = []
            for cid, doc, meta, dist in zip(
                results["ids"][0],
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            ):
                out.append(
                    {
                        "chunk_id": cid,
                        "paper_id": meta.get("paper_id", cid.split("||")[0]),
                        "text": doc,
                        "distance": float(dist),
                    }
                )
            return out
        except Exception as exc:
            logger.warning("query_chunks failed: %s", exc)
            return []

    def chunks_count(self, paper_id: str | None = None) -> int:
        try:
            if paper_id:
                return len(self._chunks_col.get(where={"paper_id": paper_id})["ids"])
            return self._chunks_col.count()
        except Exception:
            return 0

    def delete(self, paper_id: str) -> None:
        try:
            self._col.delete(ids=[paper_id])
        except Exception:
            pass
        self.delete_chunks(paper_id)

    # ── Read ───────────────────────────────────────────────────────────────

    def is_embedded(self, paper_id: str) -> bool:
        return bool(self._col.get(ids=[paper_id])["ids"])

    def count(self) -> int:
        return self._col.count()

    def get_neighbors(self, paper_id: str, k: int = 6) -> list[dict]:
        """Return top-k nearest neighbours using the stored embedding vector (no re-embed)."""
        existing = self._col.get(ids=[paper_id], include=["embeddings"])
        if not existing["ids"]:
            return []
        embs = existing.get("embeddings")
        if embs is None or len(embs) == 0:
            return []
        vector = embs[0]
        vec_list = vector.tolist() if hasattr(vector, "tolist") else list(vector)
        n = min(k + 1, self._col.count())
        if n < 2:
            return []
        results = self._col.query(query_embeddings=[vec_list], n_results=n, include=["distances"])
        out = []
        for rid, dist in zip(results["ids"][0], results["distances"][0]):
            if rid == paper_id:
                continue
            out.append(
                {
                    "id": rid,
                    "distance": round(float(dist), 4),
                    "similarity": round(1.0 - float(dist), 4),
                }
            )
        return out[:k]

    def get_pca_layout(self, paper_ids: list[str]) -> dict[str, tuple[float, float]]:
        """Return {paper_id: (x, y)} positions using 2-component PCA on stored embeddings.

        Coordinates are normalised to [-1, 1] on each axis.
        Papers without embeddings are omitted from the result.
        """
        if not paper_ids:
            return {}
        try:
            import numpy as np

            result = self._col.get(ids=paper_ids, include=["embeddings"])
            ids: list[str] = result["ids"]
            embs = result.get("embeddings")
            if len(ids) < 2 or embs is None or len(embs) == 0:
                return {}

            E = np.array(embs, dtype=np.float32)
            E -= E.mean(axis=0)

            # SVD — top 2 principal components
            _, _, Vt = np.linalg.svd(E, full_matrices=False)
            coords = E @ Vt[:2].T  # (n, 2)

            # Normalise each axis to [-1, 1]
            for i in range(2):
                col = coords[:, i]
                rng = float(col.max() - col.min())
                if rng > 1e-9:
                    coords[:, i] = (col - col.min()) / rng * 2.0 - 1.0

            return {rid: (float(coords[j, 0]), float(coords[j, 1])) for j, rid in enumerate(ids)}
        except Exception as exc:
            logger.warning("get_pca_layout failed: %s", exc)
            return {}

    def get_umap_layout(
        self, paper_ids: list[str], n_neighbors: int = 15
    ) -> dict[str, tuple[float, float]]:
        """Return {paper_id: (x, y)} positions using UMAP on stored embeddings.

        Requires umap-learn: pip install umap-learn
        Falls back to an empty dict if umap-learn is not installed.
        """
        if not paper_ids:
            return {}
        try:
            import numpy as np
            import umap as umap_lib

            result = self._col.get(ids=paper_ids, include=["embeddings"])
            ids: list[str] = result["ids"]
            embs = result.get("embeddings")
            if len(ids) < 4 or embs is None or len(embs) == 0:
                return {}

            E = np.array(embs, dtype=np.float32)
            reducer = umap_lib.UMAP(
                n_components=2,
                metric="cosine",
                random_state=42,
                n_neighbors=min(n_neighbors, len(ids) - 1),
                min_dist=0.1,
                low_memory=True,
            )
            coords = reducer.fit_transform(E)

            for i in range(2):
                col = coords[:, i]
                rng = float(col.max() - col.min())
                if rng > 1e-9:
                    coords[:, i] = (col - col.min()) / rng * 2.0 - 1.0

            return {rid: (float(coords[j, 0]), float(coords[j, 1])) for j, rid in enumerate(ids)}
        except ImportError:
            logger.warning("umap-learn not installed — install with: pip install umap-learn")
            return {}
        except Exception as exc:
            logger.warning("get_umap_layout failed: %s", exc)
            return {}

    def get_neighbors_for_unembedded(
        self,
        texts_by_id: dict[str, str],
        k: int = 5,
        exclude_ids: set[str] | None = None,
    ) -> list[dict]:
        """Find top-k nearest neighbours in the stored pool for papers that are not embedded.

        Uses ChromaDB's query_texts path — the embedding is computed on-the-fly and
        is NOT stored, so the paper's CACHE_EMBEDDINGS flag is not affected.

        Args:
            texts_by_id: {paper_id: text} for unembedded papers.
            k:           Number of neighbours to return per paper.
            exclude_ids: Paper IDs to exclude from results (e.g. papers already processed).

        Returns list of undirected edge dicts with keys: from, to, type, similarity.
        """
        pool_count = self._col.count()
        if pool_count < 1 or not texts_by_id:
            return []

        exclude = exclude_ids or set()
        seen: set[tuple[str, str]] = set()
        edges: list[dict] = []

        for paper_id, text in texts_by_id.items():
            if not text.strip():
                continue
            try:
                n = min(k + 1, pool_count)
                results = self._col.query(
                    query_texts=[text[:_MAX_TEXT]],
                    n_results=n,
                    include=["distances"],
                )
                for rid, dist in zip(results["ids"][0], results["distances"][0]):
                    if rid == paper_id or rid in exclude:
                        continue
                    s = round(1.0 - float(dist), 3)
                    a, b = (paper_id, rid) if paper_id < rid else (rid, paper_id)
                    if (a, b) not in seen:
                        seen.add((a, b))
                        edges.append({"from": a, "to": b, "type": "semantic", "similarity": s})
            except Exception as exc:
                logger.warning("get_neighbors_for_unembedded(%s): %s", paper_id, exc)

        return edges

    def get_semantic_edges(
        self,
        paper_ids: list[str],
        threshold: float = 0.75,
        k: int = 5,
    ) -> list[dict]:
        """Batch-compute semantic edges for a corpus via stored embeddings.

        Returns undirected edges (from < to alphabetically) for the top *k*
        neighbours per paper whose similarity is at or above *threshold*.
        Pass threshold=-1.0 to include all top-k regardless of score.
        """
        if len(paper_ids) < 2:
            return []
        try:
            result = self._col.get(ids=paper_ids, include=["embeddings"])
        except Exception as exc:
            logger.warning("get_semantic_edges: %s", exc)
            return []

        ids: list[str] = result["ids"]
        embs = result.get("embeddings")
        if len(ids) < 2 or embs is None or len(embs) == 0:
            return []

        try:
            import numpy as np

            E = np.array(embs, dtype=np.float32)
            norms = np.linalg.norm(E, axis=1, keepdims=True)
            norms[norms < 1e-9] = 1.0
            E /= norms
            sim = (E @ E.T).astype(float)

            edges: list[dict] = []
            n = len(ids)
            for i in range(n):
                row = sim[i].copy()
                row[i] = -1.0
                top = int(np.argsort(-row)[0])  # fastest: single sort per row
                top_j = list(np.argsort(-row)[:k])
                for j in top_j:
                    s = float(row[j])
                    if s < threshold:
                        break
                    if i < j:
                        edges.append(
                            {
                                "from": ids[i],
                                "to": ids[j],
                                "type": "semantic",
                                "similarity": round(s, 3),
                            }
                        )
            return edges

        except ImportError:
            # Pure-Python fallback (slow for large corpora)
            edges = []
            n = len(ids)
            for i in range(n):
                sims_row: list[tuple[float, int]] = []
                ei = embs[i]
                for j in range(n):
                    if i == j:
                        continue
                    dot = sum(a * b for a, b in zip(ei, embs[j]))
                    sims_row.append((dot, j))
                sims_row.sort(reverse=True)
                for s, j in sims_row[:k]:
                    if s < threshold:
                        break
                    if i < j:
                        edges.append(
                            {
                                "from": ids[i],
                                "to": ids[j],
                                "type": "semantic",
                                "similarity": round(s, 3),
                            }
                        )
            return edges


# ── Singleton ─────────────────────────────────────────────────────────────────


def get_embedding_service() -> EmbeddingService:
    global _service
    if _service is None:
        from ..config.settings import get_settings

        s = get_settings()
        _service = EmbeddingService(
            provider=s.embedding.provider,
            model=s.embedding.model,
            ollama_base_url=s.embedding.ollama_base_url,
            vllm_base_url=s.embedding.vllm_base_url,
            chroma_path=s.database.chroma_path,
        )
    return _service


def reset_embedding_service() -> None:
    """Drop cached instance — called when embedding settings change."""
    global _service
    _service = None
