"""知识库：SQLite 或 Postgres 目录 + Chroma 或 Qdrant 向量。

upsert_document 分块嵌入入库；hybrid_search 供 retrieval_node 做 RRF 混合检索。

Hybrid knowledge store with upsert_document and hybrid_search for retrieval_node.
"""

from __future__ import annotations

import json
import logging
import math
import re
from app.services.sqlite_compat import sqlite3
import uuid
from pathlib import Path
from typing import Any, Optional

from app.config.settings import settings
from app.services.db import ensure_embedding_meta_table, postgres_connection, uses_postgres
from app.services.embedding_service import embed_text

logger = logging.getLogger(__name__)
_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


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


def _doc_domain(metadata: dict[str, Any]) -> str:
    domain = str(metadata.get("domain") or "").strip().lower()
    if domain in {"common", "code", "writing"}:
        return domain
    topic = str(metadata.get("topic") or "").strip().lower()
    if topic == "writing":
        return "writing"
    if topic in {"code", "coding", "programming"}:
        return "code"
    return "common"


def _domain_matches(metadata: dict[str, Any], domains: Optional[set[str]]) -> bool:
    if not domains:
        return True
    return _doc_domain(metadata) in domains


def _chunk_row_id(parent_doc_id: str, chunk_index: int) -> str:
    return f"{parent_doc_id}__c{chunk_index:04d}"


def _is_parent_catalog_row(metadata: dict[str, Any]) -> bool:
    return bool(metadata.get("is_parent"))


def _is_searchable_row(metadata: dict[str, Any]) -> bool:
    if _is_parent_catalog_row(metadata):
        return False
    return True


def _tokenize_text(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall((text or "").lower()):
        item = raw.strip()
        if not item:
            continue
        tokens.append(item)
        if any("\u4e00" <= ch <= "\u9fff" for ch in item) and len(item) > 1:
            # CJK bigrams improve recall for no-space Chinese queries.
            tokens.extend(item[i : i + 2] for i in range(len(item) - 1))
    return tokens


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
    """混合知识库门面：SQL 目录 + 向量索引，按租户路由。

    Hybrid store facade: SQLite or Postgres catalog plus Chroma or Qdrant vectors.
    """

    def __init__(self, db_path: Optional[str] = None, vector_path: Optional[str] = None) -> None:
        self.db_path = db_path or settings.SQLITE_PATH
        self.vector_path = vector_path or settings.VECTORSTORE_PATH
        if not uses_postgres():
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        else:
            ensure_embedding_meta_table()
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
            if getattr(settings, "RETRIEVAL_FTS_ENABLED", False):
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_docs_fts USING fts5(
                        doc_id UNINDEXED,
                        title,
                        content,
                        tokenize='unicode61'
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

    def _upsert_row(
        self,
        doc_id: str,
        title: str,
        content: str,
        metadata: dict[str, Any],
        created_at: str,
        *,
        index_vector: bool,
    ) -> None:
        params = (doc_id, title, content, json.dumps(metadata), created_at)
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
                if getattr(settings, "RETRIEVAL_FTS_ENABLED", False):
                    conn.execute(
                        "DELETE FROM knowledge_docs_fts WHERE doc_id = ?",
                        (doc_id,),
                    )
                    conn.execute(
                        """
                        INSERT INTO knowledge_docs_fts (doc_id, title, content)
                        VALUES (?, ?, ?)
                        """,
                        (doc_id, title, content),
                    )
                conn.commit()
        if index_vector and self._vector and getattr(self._vector, "available", False):
            self._vector.upsert(doc_id, title, content, metadata)

    def _delete_row(self, doc_id: str) -> bool:
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
                if getattr(settings, "RETRIEVAL_FTS_ENABLED", False):
                    conn.execute("DELETE FROM knowledge_docs_fts WHERE doc_id = ?", (doc_id,))
                conn.commit()
                deleted = cursor.rowcount > 0
        if deleted and self._vector and getattr(self._vector, "available", False):
            self._vector.delete(doc_id)
        return deleted

    def _iter_all_rows(self) -> list[Any]:
        return self._fetchall("SELECT * FROM knowledge_docs")

    def _child_chunk_ids(self, parent_doc_id: str) -> list[str]:
        prefix = f"{parent_doc_id}__c"
        ids: list[str] = []
        for row in self._iter_all_rows():
            row_id = str(row["doc_id"])
            if row_id.startswith(prefix):
                ids.append(row_id)
                continue
            meta = json.loads(row["metadata"])
            if str(meta.get("parent_doc_id") or "") == parent_doc_id and meta.get("is_chunk"):
                ids.append(row_id)
        return ids

    def _delete_chunks_for_parent(self, parent_doc_id: str) -> None:
        for chunk_id in self._child_chunk_ids(parent_doc_id):
            self._delete_row(chunk_id)

    def _should_chunk(self, content: str, metadata: dict[str, Any]) -> bool:
        if not getattr(settings, "RAG_CHUNK_ENABLED", True):
            return False
        if metadata.get("is_chunk") or metadata.get("skip_chunking"):
            return False
        min_chars = int(getattr(settings, "RAG_CHUNK_MIN_CHARS", 600))
        return len((content or "").strip()) >= min_chars

    def upsert_document(
        self,
        title: str,
        content: str,
        metadata: Optional[dict[str, Any]] = None,
        doc_id: Optional[str] = None,
    ) -> str:
        from datetime import datetime, timezone

        from app.services.knowledge_chunker import chunk_document

        did = doc_id or str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        meta = _stamp_tenant_metadata(metadata)
        domain = _doc_domain(meta)
        meta.setdefault("domain", domain)

        self._delete_chunks_for_parent(did)

        if not self._should_chunk(content, meta):
            self._delete_row(did)
            single_meta = {**meta, "is_parent": False, "is_chunk": False}
            self._upsert_row(did, title, content, single_meta, now, index_vector=True)
            return did

        chunks = chunk_document(title, content, domain=domain, metadata=meta)
        if len(chunks) <= 1:
            self._delete_row(did)
            only = chunks[0] if chunks else None
            body = only.body if only else content
            single_meta = {
                **meta,
                "is_parent": False,
                "is_chunk": False,
                "section_title": only.section_title if only else "",
                "chunk_index": 0,
                "token_count": only.token_count if only else 0,
            }
            self._upsert_row(did, title, body, single_meta, now, index_vector=True)
            return did

        parent_meta = {**meta, "is_parent": True, "is_chunk": False, "chunk_count": len(chunks)}
        self._upsert_row(did, title, content, parent_meta, now, index_vector=False)

        for spec in chunks:
            chunk_id = _chunk_row_id(did, spec.chunk_index)
            chunk_title = f"{title} — {spec.section_title}" if spec.section_title else title
            chunk_meta = {
                **meta,
                "is_parent": False,
                "is_chunk": True,
                "parent_doc_id": did,
                "chunk_index": spec.chunk_index,
                "section_title": spec.section_title,
                "token_count": spec.token_count,
                "chunk_count": len(chunks),
            }
            self._upsert_row(
                chunk_id,
                chunk_title,
                spec.embed_text,
                chunk_meta,
                now,
                index_vector=True,
            )
        return did

    def delete_document(self, doc_id: str) -> bool:
        self._delete_chunks_for_parent(doc_id)
        return self._delete_row(doc_id)

    def get_document(self, doc_id: str) -> Optional[dict[str, Any]]:
        row = self._fetchone("SELECT * FROM knowledge_docs WHERE doc_id = ?", (doc_id,))
        if not row:
            return None
        meta = json.loads(row["metadata"])
        if _is_parent_catalog_row(meta):
            return {
                "doc_id": row["doc_id"],
                "title": row["title"],
                "content": row["content"],
                "metadata": meta,
                "created_at": row["created_at"],
            }
        child_ids = self._child_chunk_ids(doc_id)
        if not child_ids:
            return {
                "doc_id": row["doc_id"],
                "title": row["title"],
                "content": row["content"],
                "metadata": meta,
                "created_at": row["created_at"],
            }
        parts: list[tuple[int, str]] = []
        for child_id in child_ids:
            child = self._fetchone("SELECT * FROM knowledge_docs WHERE doc_id = ?", (child_id,))
            if not child:
                continue
            child_meta = json.loads(child["metadata"])
            idx = int(child_meta.get("chunk_index", 0))
            parts.append((idx, str(child["content"])))
        merged = "\n\n".join(text for _, text in sorted(parts, key=lambda item: item[0]))
        return {
            "doc_id": doc_id,
            "title": row["title"],
            "content": merged or row["content"],
            "metadata": {**meta, "assembled_from_chunks": True, "chunk_count": len(parts)},
            "created_at": row["created_at"],
        }

    def list_documents(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._fetchall(
            "SELECT doc_id, title, metadata, created_at FROM knowledge_docs ORDER BY created_at DESC",
        )
        docs: list[dict[str, Any]] = []
        seen_parents: set[str] = set()
        for row in rows:
            meta = json.loads(row["metadata"])
            if meta.get("is_chunk"):
                parent = str(meta.get("parent_doc_id") or "")
                if parent and parent not in seen_parents:
                    seen_parents.add(parent)
                    docs.append(
                        {
                            "doc_id": parent,
                            "title": row["title"].split(" — ", 1)[0],
                            "created_at": row["created_at"],
                        }
                    )
                continue
            if _is_parent_catalog_row(meta):
                docs.append(
                    {
                        "doc_id": row["doc_id"],
                        "title": row["title"],
                        "created_at": row["created_at"],
                    }
                )
                continue
            if str(meta.get("parent_doc_id") or ""):
                continue
            docs.append(
                {
                    "doc_id": row["doc_id"],
                    "title": row["title"],
                    "created_at": row["created_at"],
                }
            )
        return docs[:limit]

    def get_chunk_by_parent_index(self, parent_doc_id: str, chunk_index: int) -> Optional[dict[str, Any]]:
        row = self._fetchone(
            "SELECT * FROM knowledge_docs WHERE doc_id = ?",
            (_chunk_row_id(parent_doc_id, chunk_index),),
        )
        if not row:
            for candidate in self._iter_all_rows():
                meta = json.loads(candidate["metadata"])
                if (
                    str(meta.get("parent_doc_id") or "") == parent_doc_id
                    and int(meta.get("chunk_index", -1)) == chunk_index
                ):
                    row = candidate
                    break
        if not row:
            return None
        meta = json.loads(row["metadata"])
        return {
            "doc_id": row["doc_id"],
            "title": row["title"],
            "content": row["content"],
            "metadata": meta,
            "created_at": row["created_at"],
        }

    def _keyword_search_fts(
        self,
        query: str,
        *,
        domains: Optional[set[str]] = None,
        limit: int,
    ) -> list[Any] | None:
        if uses_postgres() or not getattr(settings, "RETRIEVAL_FTS_ENABLED", False):
            return None
        fts_query = " OR ".join(f'"{t}"' for t in _tokenize_text(query)[:12])
        if not fts_query:
            return None
        try:
            with self._connect() as conn:
                return conn.execute(
                    """
                    SELECT d.doc_id, d.title, d.content, d.metadata, d.created_at,
                           bm25(knowledge_docs_fts) AS fts_rank
                    FROM knowledge_docs_fts f
                    JOIN knowledge_docs d ON d.doc_id = f.doc_id
                    WHERE knowledge_docs_fts MATCH ?
                    ORDER BY fts_rank
                    LIMIT ?
                    """,
                    (fts_query, max(limit * 4, limit)),
                ).fetchall()
        except Exception:
            return None

    def keyword_search(
        self,
        query: str,
        top_k: Optional[int] = None,
        *,
        domains: Optional[set[str]] = None,
    ) -> list[dict[str, Any]]:
        limit = top_k or settings.RETRIEVAL_TOP_K
        query_tokens = _tokenize_text(query)
        fts_rows = self._keyword_search_fts(query, domains=domains, limit=limit)
        if fts_rows:
            rows = fts_rows
        else:
            rows = self._fetchall("SELECT * FROM knowledge_docs")
        filtered_rows: list[Any] = []
        corpus_tokens: list[list[str]] = []
        doc_freq: dict[str, int] = {}
        avg_doc_len = 0.0

        for row in rows:
            meta = json.loads(row["metadata"])
            if not _is_searchable_row(meta):
                continue
            if not _tenant_matches(meta):
                continue
            if not _domain_matches(meta, domains):
                continue
            filtered_rows.append(row)
            title = row["title"] if "title" in row.keys() else row[1]
            content = row["content"] if "content" in row.keys() else row[2]
            row_tokens = _tokenize_text(f"{title} {content}")
            corpus_tokens.append(row_tokens)
            avg_doc_len += len(row_tokens)
            for token in set(row_tokens):
                doc_freq[token] = doc_freq.get(token, 0) + 1

        doc_count = max(len(filtered_rows), 1)
        avg_doc_len = (avg_doc_len / doc_count) if filtered_rows else 1.0
        bm25_k1 = float(getattr(settings, "RAG_BM25_K1", 1.5))
        bm25_b = float(getattr(settings, "RAG_BM25_B", 0.75))

        scored: list[dict[str, Any]] = []
        for idx, row in enumerate(filtered_rows):
            meta = json.loads(row["metadata"])
            row_tokens = corpus_tokens[idx]
            if not query_tokens:
                score = 0.05
            else:
                token_tf: dict[str, int] = {}
                for t in row_tokens:
                    token_tf[t] = token_tf.get(t, 0) + 1
                score = 0.0
                row_len = max(len(row_tokens), 1)
                for token in query_tokens:
                    tf = token_tf.get(token, 0)
                    if tf <= 0:
                        continue
                    df = doc_freq.get(token, 0)
                    idf = math.log(1.0 + ((doc_count - df + 0.5) / (df + 0.5)))
                    denom = tf + bm25_k1 * (1.0 - bm25_b + bm25_b * (row_len / avg_doc_len))
                    score += idf * ((tf * (bm25_k1 + 1.0)) / max(denom, 1e-8))
            if query_tokens and score <= 0:
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

    def vector_search(
        self,
        query: str,
        top_k: Optional[int] = None,
        *,
        domains: Optional[set[str]] = None,
    ) -> list[dict[str, Any]]:
        limit = top_k or settings.RETRIEVAL_TOP_K
        if not self._vector or not self._vector.available:
            return []
        hits = self._vector.search(query, limit)
        filtered = [h for h in hits if _tenant_matches(h.get("metadata") or {})]
        if not domains:
            return filtered
        return [h for h in filtered if _domain_matches(h.get("metadata") or {}, domains)]

    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        *,
        domains: Optional[set[str]] = None,
    ) -> list[dict[str, Any]]:
        vector_hits = self.vector_search(query, top_k=top_k, domains=domains)
        if vector_hits:
            return vector_hits
        return self.keyword_search(query, top_k=top_k, domains=domains)

    def load_embedding_meta(self) -> Optional[dict[str, Any]]:
        try:
            row = self._fetchone("SELECT * FROM embedding_meta WHERE id = 1")
        except Exception as exc:
            logger.warning(
                "embedding_meta load failed (%s), skipping stored meta",
                exc,
            )
            return None
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
        try:
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
        except Exception as exc:
            logger.warning("embedding_meta save failed (%s)", exc)

    def _check_embedding_compatibility(self) -> None:
        try:
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
        except Exception as exc:
            logger.warning(
                "embedding compatibility check failed, continuing retrieval: %s",
                exc,
            )

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

    def hybrid_search(
        self,
        query: str,
        top_k: Optional[int] = None,
        *,
        domains: Optional[set[str]] = None,
        query_object: Any = None,
        retrieval_decision: Any = None,
    ) -> list[dict[str, Any]]:
        """混合检索：向量 + 关键词 RRF，可选 rerank。

        Main retrieval entry: vector and keyword merge with optional rerank.
        """
        limit = top_k or settings.RETRIEVAL_TOP_K
        multiplier = max(1, int(getattr(settings, "RAG_FETCH_K_MULTIPLIER", 4)))
        fetch_k = limit * multiplier
        if settings.RAG_RERANK_ENABLED:
            fetch_k = max(fetch_k, settings.RAG_RERANK_CANDIDATE_K)
        self._check_embedding_compatibility()
        vector_hits = self.vector_search(query, top_k=fetch_k, domains=domains)
        keyword_hits = self.keyword_search(query, top_k=fetch_k, domains=domains)
        if not vector_hits:
            merged = keyword_hits
            stage = "keyword"
        elif not keyword_hits:
            merged = vector_hits
            stage = "vector"
        else:
            from app.services.retrieval_search_policy import lexical_rrf_weight

            lex_w = lexical_rrf_weight(query_object, retrieval_decision)
            merged = _rrf_merge(vector_hits, keyword_hits, fetch_k, lexical_weight=lex_w)
            stage = "rrf"
        merged = self._normalize_hits(merged)
        from app.services.retrieval_search_policy import filter_stale_at_recall

        merged, stale_dropped = filter_stale_at_recall(
            merged, query_obj=query_object, decision=retrieval_decision
        )
        if stale_dropped:
            for h in merged:
                h.setdefault("retrieval_debug", {})["stale_prefilter_dropped"] = stale_dropped
        from app.services.relevance_gate import annotate_hit

        merged = [annotate_hit(h, stage=stage, query=query) for h in merged]
        if settings.RAG_RERANK_ENABLED:
            from app.services.reranker import rerank

            merged = rerank(query, merged, top_k=limit)
        else:
            from app.services.relevance_gate import apply_relevance_gate

            if getattr(settings, "RAG_RELEVANCE_GATE_ENABLED", True):
                merged = apply_relevance_gate(merged, query, stage=stage)
            merged = merged[:limit]
        merged = _limit_per_doc(merged, max_per_doc=int(getattr(settings, "RAG_MAX_CHUNKS_PER_DOC", 2)))
        merged = self._expand_adjacent_chunks(merged, query=query)
        return merged[:limit]

    def _expand_adjacent_chunks(
        self,
        hits: list[dict[str, Any]],
        *,
        query: str = "",
    ) -> list[dict[str, Any]]:
        if not getattr(settings, "RAG_CHUNK_ADJACENCY_ENABLED", True):
            return hits
        radius = max(0, int(getattr(settings, "RAG_CHUNK_ADJACENCY_RADIUS", 1)))
        if radius <= 0:
            return hits

        from app.services.relevance_gate import annotate_hit, relevance_score

        max_rank = max(1, int(getattr(settings, "RAG_ADJACENCY_MAX_RANK", 2)))
        threshold = float(getattr(settings, "RAG_RERANK_MIN_SCORE", 0.0))

        seen_ids = {str(h.get("doc_id") or "") for h in hits}
        expanded: list[dict[str, Any]] = list(hits)
        for rank, hit in enumerate(hits):
            if rank >= max_rank:
                break
            if not hit.get("relevance_passed", True):
                continue
            center_score = relevance_score(hit)
            if threshold > 0 and center_score < threshold:
                continue
            meta = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
            parent = str(meta.get("parent_doc_id") or "")
            if not parent or not meta.get("is_chunk"):
                continue
            center = int(meta.get("chunk_index", 0))
            for offset in range(-radius, radius + 1):
                if offset == 0:
                    continue
                neighbor = self.get_chunk_by_parent_index(parent, center + offset)
                if not neighbor:
                    continue
                nid = str(neighbor.get("doc_id") or "")
                if not nid or nid in seen_ids:
                    continue
                seen_ids.add(nid)
                neighbor_hit = annotate_hit(
                    {
                        **neighbor,
                        "score": center_score * 0.85,
                        "metadata": neighbor.get("metadata") or {},
                        "source": "adjacency",
                        "adjacency_only": True,
                    },
                    stage="adjacency",
                    query=query,
                    threshold=threshold,
                )
                expanded.append(neighbor_hit)
        return expanded

    def count(self) -> int:
        row = self._fetchone("SELECT COUNT(*) AS c FROM knowledge_docs")
        return int(row["c"]) if row else 0


def _rrf_merge(
    a: list[dict[str, Any]],
    b: list[dict[str, Any]],
    top_k: int,
    k: int = 60,
    *,
    lexical_weight: float = 1.0,
) -> list[dict[str, Any]]:
    scores: dict[str, float] = {}
    docs: dict[str, dict[str, Any]] = {}
    for rank, item in enumerate(a):
        doc_id = item["doc_id"]
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
        docs[doc_id] = item
    for rank, item in enumerate(b):
        doc_id = item["doc_id"]
        scores[doc_id] = scores.get(doc_id, 0.0) + lexical_weight / (k + rank + 1)
        docs[doc_id] = item
    ordered = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
    result = []
    for doc_id, score in ordered[:top_k]:
        doc = dict(docs[doc_id])
        doc["rrf_score"] = score
        result.append(doc)
    return result


def _limit_per_doc(hits: list[dict[str, Any]], *, max_per_doc: int) -> list[dict[str, Any]]:
    if max_per_doc <= 0:
        return hits
    seen: dict[str, int] = {}
    result: list[dict[str, Any]] = []
    for hit in hits:
        meta = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
        group = str(meta.get("parent_doc_id") or hit.get("doc_id") or "")
        count = seen.get(group, 0)
        if count >= max_per_doc:
            continue
        seen[group] = count + 1
        result.append(hit)
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
