from __future__ import annotations

import json
import logging
from app.services.sqlite_compat import sqlite3
import uuid
from pathlib import Path
from typing import Any, Optional

from app.config.settings import settings
from app.services.db import postgres_connection, uses_postgres
from app.services.embedding_service import embed_text

logger = logging.getLogger(__name__)


def _current_tenant_id() -> Optional[str]:
    if not settings.MULTI_TENANT_ENABLED:
        return None
    from app.services.tenant_context import get_tenant_id

    return get_tenant_id()


def _tenant_matches(metadata: dict[str, Any]) -> bool:
    tid = _current_tenant_id()
    if not tid:
        return True
    doc_tid = metadata.get("tenant_id")
    if doc_tid is None:
        return True
    return str(doc_tid) == tid


def _stamp_tenant_metadata(metadata: Optional[dict[str, Any]]) -> dict[str, Any]:
    meta = dict(metadata or {})
    tid = _current_tenant_id()
    if tid:
        meta.setdefault("tenant_id", tid)
    return meta


class _ChromaVectorIndex:
    """ChromaDB-backed vector index with configurable embeddings."""

    def __init__(self, persist_path: str, collection_name: str) -> None:
        self._available = False
        self._collection = None
        try:
            import chromadb

            from app.services.embedding_service import EmbeddingFunction

            Path(persist_path).mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(path=persist_path)
            embedding_fn = EmbeddingFunction()
            self._collection = client.get_or_create_collection(
                name=collection_name,
                embedding_function=embedding_fn,
            )
            self._available = True
        except Exception as exc:
            logger.warning("ChromaDB unavailable, falling back to keyword search: %s", exc)

    @property
    def available(self) -> bool:
        return self._available and self._collection is not None

    def upsert(self, doc_id: str, title: str, content: str, metadata: dict[str, Any]) -> None:
        if not self.available:
            return
        document = f"{title}\n{content}"
        meta = {**metadata, "title": title}
        self._collection.upsert(
            ids=[doc_id],
            documents=[document],
            metadatas=[meta],
        )

    def delete(self, doc_id: str) -> None:
        if not self.available:
            return
        self._collection.delete(ids=[doc_id])

    def search(self, query: str, top_k: int) -> list[dict[str, Any]]:
        if not self.available:
            return []
        # Chroma 1.5.x rust backend mishandles query_texts + custom EmbeddingFunction;
        # pass explicit embeddings (verified in container).
        result = self._collection.query(query_embeddings=[embed_text(query)], n_results=top_k)
        ids = (result.get("ids") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]

        hits: list[dict[str, Any]] = []
        for idx, doc_id in enumerate(ids):
            distance = distances[idx] if idx < len(distances) else 1.0
            score = 1.0 / (1.0 + float(distance))
            if score < settings.SIMILARITY_THRESHOLD:
                continue
            meta = metadatas[idx] if idx < len(metadatas) else {}
            title = str(meta.get("title", ""))
            content = documents[idx] if idx < len(documents) else ""
            hits.append(
                {
                    "doc_id": doc_id,
                    "title": title,
                    "content": content,
                    "score": score,
                    "metadata": meta,
                    "source": "chroma",
                }
            )
        return hits


class _QdrantVectorIndex:
    """Qdrant vector index for production deployments (§23.1)."""

    def __init__(self, collection_name: str) -> None:
        self._available = False
        self._client = None
        self._collection = collection_name
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, VectorParams

            if settings.QDRANT_URL:
                self._client = QdrantClient(url=settings.QDRANT_URL)
            else:
                path = settings.QDRANT_PATH or str(
                    Path(settings.VECTORSTORE_PATH) / "qdrant"
                )
                Path(path).mkdir(parents=True, exist_ok=True)
                self._client = QdrantClient(path=path)

            dim = len(embed_text("dimension_probe"))
            collections = [c.name for c in self._client.get_collections().collections]
            if collection_name not in collections:
                self._client.create_collection(
                    collection_name=collection_name,
                    vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
                )
            self._available = True
        except Exception as exc:
            logger.warning("Qdrant unavailable, falling back to keyword search: %s", exc)

    @property
    def available(self) -> bool:
        return self._available and self._client is not None

    def upsert(self, doc_id: str, title: str, content: str, metadata: dict[str, Any]) -> None:
        if not self.available:
            return
        from qdrant_client.models import PointStruct

        document = f"{title}\n{content}"
        vector = embed_text(document)
        self._client.upsert(
            collection_name=self._collection,
            points=[
                PointStruct(
                    id=doc_id,
                    vector=vector,
                    payload={**metadata, "title": title, "content": content},
                )
            ],
        )

    def delete(self, doc_id: str) -> None:
        if not self.available:
            return
        from qdrant_client.models import PointIdsList

        self._client.delete(
            collection_name=self._collection,
            points_selector=PointIdsList(points=[doc_id]),
        )

    def search(self, query: str, top_k: int) -> list[dict[str, Any]]:
        if not self.available:
            return []
        vector = embed_text(query)
        hits = self._client.search(
            collection_name=self._collection,
            query_vector=vector,
            limit=top_k,
        )
        results: list[dict[str, Any]] = []
        for hit in hits:
            score = float(hit.score)
            if score < settings.SIMILARITY_THRESHOLD:
                continue
            payload = hit.payload or {}
            results.append(
                {
                    "doc_id": str(hit.id),
                    "title": str(payload.get("title", "")),
                    "content": str(payload.get("content", "")),
                    "score": score,
                    "metadata": payload,
                    "source": "qdrant",
                }
            )
        return results


class KnowledgeStore:
    """Hybrid knowledge store: SQLite registry + ChromaDB vectors."""

    def __init__(self, db_path: Optional[str] = None, vector_path: Optional[str] = None) -> None:
        self.db_path = db_path or settings.SQLITE_PATH
        self.vector_path = vector_path or settings.VECTORSTORE_PATH
        if not uses_postgres():
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._vector: Optional[Any] = None
        backend = settings.KNOWLEDGE_BACKEND.lower()
        if backend == "qdrant":
            self._vector = _QdrantVectorIndex(settings.KNOWLEDGE_COLLECTION)
        elif backend == "chroma":
            self._vector = _ChromaVectorIndex(
                self.vector_path,
                settings.KNOWLEDGE_COLLECTION,
            )

    @property
    def _chroma(self) -> Optional[_ChromaVectorIndex]:
        return self._vector if isinstance(self._vector, _ChromaVectorIndex) else None

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        if uses_postgres():
            return
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_docs (
                    doc_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS embedding_meta (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    model_name TEXT NOT NULL,
                    dimension INTEGER NOT NULL,
                    distance_metric TEXT NOT NULL,
                    version TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def _fetchall(self, query: str, params: tuple[Any, ...] = ()) -> list[Any]:
        if uses_postgres():
            pg_query = query.replace("?", "%s")
            with postgres_connection() as conn:
                return conn.execute(pg_query, params).fetchall()
        with self._connect() as conn:
            return conn.execute(query, params).fetchall()

    def _fetchone(self, query: str, params: tuple[Any, ...] = ()) -> Any:
        if uses_postgres():
            pg_query = query.replace("?", "%s")
            with postgres_connection() as conn:
                return conn.execute(pg_query, params).fetchone()
        with self._connect() as conn:
            return conn.execute(query, params).fetchone()

    def upsert_document(
        self,
        title: str,
        content: str,
        metadata: Optional[dict[str, Any]] = None,
        doc_id: Optional[str] = None,
    ) -> str:
        from datetime import datetime, timezone

        did = doc_id or str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        meta = _stamp_tenant_metadata(metadata)
        params = (did, title, content, json.dumps(meta), now)
        if uses_postgres():
            with postgres_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO knowledge_docs (doc_id, title, content, metadata, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (doc_id) DO UPDATE SET
                        title = EXCLUDED.title,
                        content = EXCLUDED.content,
                        metadata = EXCLUDED.metadata,
                        created_at = EXCLUDED.created_at
                    """,
                    params,
                )
        else:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO knowledge_docs (doc_id, title, content, metadata, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    params,
                )
                conn.commit()
        if self._vector and getattr(self._vector, "available", False):
            self._vector.upsert(did, title, content, meta)
        return did

    def delete_document(self, doc_id: str) -> bool:
        if uses_postgres():
            with postgres_connection() as conn:
                result = conn.execute(
                    "DELETE FROM knowledge_docs WHERE doc_id = %s",
                    (doc_id,),
                )
                deleted = result.rowcount > 0
        else:
            with self._connect() as conn:
                cursor = conn.execute("DELETE FROM knowledge_docs WHERE doc_id = ?", (doc_id,))
                conn.commit()
                deleted = cursor.rowcount > 0
        if deleted and self._vector and getattr(self._vector, "available", False):
            self._vector.delete(doc_id)
        return deleted

    def get_document(self, doc_id: str) -> Optional[dict[str, Any]]:
        row = self._fetchone("SELECT * FROM knowledge_docs WHERE doc_id = ?", (doc_id,))
        if not row:
            return None
        return {
            "doc_id": row["doc_id"],
            "title": row["title"],
            "content": row["content"],
            "metadata": json.loads(row["metadata"]),
            "created_at": row["created_at"],
        }

    def list_documents(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._fetchall(
            "SELECT doc_id, title, created_at FROM knowledge_docs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [
            {"doc_id": row["doc_id"], "title": row["title"], "created_at": row["created_at"]}
            for row in rows
        ]

    def keyword_search(self, query: str, top_k: Optional[int] = None) -> list[dict[str, Any]]:
        limit = top_k or settings.RETRIEVAL_TOP_K
        tokens = [t.lower() for t in query.split() if t.strip()]
        rows = self._fetchall("SELECT * FROM knowledge_docs")

        scored: list[dict[str, Any]] = []
        for row in rows:
            meta = json.loads(row["metadata"])
            if not _tenant_matches(meta):
                continue
            haystack = f"{row['title']} {row['content']}".lower()
            if not tokens:
                score = 0.1
            else:
                score = sum(1 for token in tokens if token in haystack) / len(tokens)
            if tokens and score <= 0:
                continue
            scored.append(
                {
                    "doc_id": row["doc_id"],
                    "title": row["title"],
                    "content": row["content"],
                    "score": score,
                    "metadata": meta,
                    "source": "keyword",
                }
            )
        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[:limit]

    def vector_search(self, query: str, top_k: Optional[int] = None) -> list[dict[str, Any]]:
        limit = top_k or settings.RETRIEVAL_TOP_K
        if not self._vector or not self._vector.available:
            return []
        return self._vector.search(query, limit)

    def search(self, query: str, top_k: Optional[int] = None) -> list[dict[str, Any]]:
        vector_hits = self.vector_search(query, top_k=top_k)
        if vector_hits:
            return vector_hits
        return self.keyword_search(query, top_k=top_k)

    def load_embedding_meta(self) -> Optional[dict[str, Any]]:
        row = self._fetchone("SELECT * FROM embedding_meta WHERE id = 1")
        if not row:
            return None
        return {
            "model_name": row["model_name"],
            "dimension": int(row["dimension"]),
            "distance_metric": row["distance_metric"],
            "version": row["version"],
            "created_at": row["created_at"],
        }

    def save_embedding_meta(self, meta: Any) -> None:
        from app.services.embedding_meta import EmbeddingMeta

        if isinstance(meta, EmbeddingMeta):
            payload = meta.to_dict()
        else:
            payload = dict(meta)
        params = (
            1,
            payload["model_name"],
            int(payload["dimension"]),
            payload["distance_metric"],
            payload["version"],
            payload.get("created_at") or "",
        )
        if uses_postgres():
            with postgres_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO embedding_meta (id, model_name, dimension, distance_metric, version, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        model_name = EXCLUDED.model_name,
                        dimension = EXCLUDED.dimension,
                        distance_metric = EXCLUDED.distance_metric,
                        version = EXCLUDED.version,
                        created_at = EXCLUDED.created_at
                    """,
                    params,
                )
        else:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO embedding_meta
                    (id, model_name, dimension, distance_metric, version, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    params,
                )
                conn.commit()

    def _check_embedding_compatibility(self) -> None:
        from app.services.embedding_meta import (
            get_current_embedding_meta,
            meta_from_dict,
            record_incompatibility,
            validate_index_compatibility,
        )

        current = get_current_embedding_meta()
        stored_raw = self.load_embedding_meta()
        if stored_raw is None:
            self.save_embedding_meta(current)
            return
        stored = meta_from_dict(stored_raw)
        if not validate_index_compatibility(stored, current):
            record_incompatibility(stored, current)
            if settings.EMBEDDING_AUTO_REINDEX:
                from app.services.embedding_reindex import reindex_collection

                reindex_collection(self)

    def _normalize_hits(self, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for rank, hit in enumerate(hits):
            meta = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
            normalized.append(
                {
                    **hit,
                    "chunk_index": meta.get("chunk_index", 0),
                    "source_url": meta.get("source_url") or meta.get("url"),
                    "rank": rank,
                }
            )
        return normalized

    def hybrid_search(self, query: str, top_k: Optional[int] = None) -> list[dict[str, Any]]:
        limit = top_k or settings.RETRIEVAL_TOP_K
        fetch_k = max(limit, settings.RAG_RERANK_CANDIDATE_K) if settings.RAG_RERANK_ENABLED else limit
        self._check_embedding_compatibility()
        vector_hits = self.vector_search(query, top_k=fetch_k)
        keyword_hits = self.keyword_search(query, top_k=fetch_k)
        if not vector_hits:
            merged = keyword_hits
        elif not keyword_hits:
            merged = vector_hits
        else:
            merged = _rrf_merge(vector_hits, keyword_hits, fetch_k)
        merged = self._normalize_hits(merged)
        if settings.RAG_RERANK_ENABLED:
            from app.services.reranker import rerank

            merged = rerank(query, merged, top_k=limit)
        merged = [h for h in merged if _tenant_matches(h.get("metadata") or {})]
        return merged[:limit]

    def count(self) -> int:
        row = self._fetchone("SELECT COUNT(*) AS c FROM knowledge_docs")
        return int(row["c"]) if row else 0


def _rrf_merge(
    a: list[dict[str, Any]],
    b: list[dict[str, Any]],
    top_k: int,
    k: int = 60,
) -> list[dict[str, Any]]:
    scores: dict[str, float] = {}
    docs: dict[str, dict[str, Any]] = {}
    for rank, item in enumerate(a):
        doc_id = item["doc_id"]
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
        docs[doc_id] = item
    for rank, item in enumerate(b):
        doc_id = item["doc_id"]
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
        docs[doc_id] = item
    ordered = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
    result = []
    for doc_id, score in ordered[:top_k]:
        doc = dict(docs[doc_id])
        doc["rrf_score"] = score
        result.append(doc)
    return result


_store: KnowledgeStore | None = None
_stores: dict[str, KnowledgeStore] = {}


def get_knowledge_store() -> KnowledgeStore:
    from app.services.tenant_storage import (
        current_sqlite_path,
        current_vectorstore_path,
        tenant_cache_key,
    )

    key = tenant_cache_key()
    if key == "default":
        global _store
        if _store is None:
            _store = KnowledgeStore()
        return _store
    if key not in _stores:
        _stores[key] = KnowledgeStore(
            db_path=current_sqlite_path(),
            vector_path=current_vectorstore_path(),
        )
    return _stores[key]
