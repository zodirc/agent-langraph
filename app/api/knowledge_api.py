"""知识库 HTTP API

Knowledge API: upsert documents, hybrid search (feeds retrieval_node).
POST /knowledge/documents → knowledge_store.upsert; GET search → hybrid_search."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_principal, require_role
from app.api.tenant_access import assert_session_knowledge_access, assert_task_access
from app.services.state_store import get_state_store
from app.services.auth_service import AuthPrincipal
from app.services.knowledge_store import get_knowledge_store

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


class UpsertDocumentRequest(BaseModel):
    title: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    doc_id: Optional[str] = None
    session_id: Optional[str] = None


class SearchResponse(BaseModel):
    query: str
    hits: list[dict[str, Any]]
    total: int


@router.post("/documents")
def upsert_document(
    request: UpsertDocumentRequest,
    principal: AuthPrincipal = Depends(require_role("user", "admin")),
) -> dict[str, Any]:
    if request.session_id:
        assert_session_knowledge_access(principal, request.session_id)
    metadata = dict(request.metadata)
    if request.session_id and not str(metadata.get("domain") or "").strip():
        metadata.setdefault("domain", "source")
    doc_id = get_knowledge_store().upsert_document(
        title=request.title,
        content=request.content,
        metadata={**metadata, "uploaded_by": principal.user_id},
        doc_id=request.doc_id,
        session_id=request.session_id,
    )
    if request.session_id:
        from app.services.retrieval_cache import invalidate_session

        invalidate_session(request.session_id)
        domain = str(metadata.get("domain") or "").lower()
        story_bible_started = False
        if domain == "source" and request.content.strip():
            from app.services.story_bible import distill_story_bible_async

            distill_story_bible_async(
                request.session_id,
                title=request.title,
                content=request.content,
            )
            story_bible_started = True
        result = {"doc_id": doc_id, "status": "upserted"}
        if story_bible_started:
            result["story_bible_distillation"] = "started"
        return result
    return {"doc_id": doc_id, "status": "upserted"}


@router.get("/sessions/{session_id}/story-bible")
def get_session_story_bible(
    session_id: str,
    principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    """Story bible (素材卡) distillation status for UI polling."""
    assert_session_knowledge_access(principal, session_id)
    from app.services.story_bible import story_bible_status

    return story_bible_status(session_id)


@router.get("/documents")
def list_documents(
    limit: int = 50,
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    docs = get_knowledge_store().list_documents(limit=min(limit, 200))
    return {"documents": docs, "total": len(docs)}


@router.get("/documents/{doc_id}")
def get_document(
    doc_id: str,
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    doc = get_knowledge_store().get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")
    return doc


@router.delete("/documents/{doc_id}")
def delete_document(
    doc_id: str,
    _principal: AuthPrincipal = Depends(require_role("admin")),
) -> dict[str, str]:
    if not get_knowledge_store().delete_document(doc_id):
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")
    return {"doc_id": doc_id, "status": "deleted"}


@router.delete("/sessions/{session_id}")
def delete_session_knowledge(
    session_id: str,
    principal: AuthPrincipal = Depends(require_role("user", "admin")),
) -> dict[str, Any]:
    assert_session_knowledge_access(principal, session_id)
    store = get_knowledge_store()
    if get_state_store().load(session_id, read_only=True):
        removed = store.delete_by_session(session_id)
    elif principal.role == "admin":
        removed = store.delete_by_session(session_id)
    else:
        removed = store.delete_by_session(session_id, uploaded_by=principal.user_id)
    from app.services.retrieval_cache import invalidate_session

    invalidate_session(session_id)
    return {"session_id": session_id, "removed": removed, "status": "deleted"}


@router.get("/search", response_model=SearchResponse)
def search_knowledge(
    q: str,
    mode: str = "hybrid",
    top_k: int = 5,
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> SearchResponse:
    store = get_knowledge_store()
    if mode == "vector":
        hits = store.vector_search(q, top_k=top_k)
    elif mode == "keyword":
        hits = store.keyword_search(q, top_k=top_k)
    else:
        hits = store.hybrid_search(q, top_k=top_k)
    return SearchResponse(query=q, hits=hits, total=len(hits))
