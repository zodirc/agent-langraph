from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_principal, require_role
from app.services.auth_service import AuthPrincipal
from app.services.knowledge_store import get_knowledge_store

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


class UpsertDocumentRequest(BaseModel):
    title: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    doc_id: Optional[str] = None


class SearchResponse(BaseModel):
    query: str
    hits: list[dict[str, Any]]
    total: int


@router.post("/documents")
def upsert_document(
    request: UpsertDocumentRequest,
    principal: AuthPrincipal = Depends(require_role("user", "admin")),
) -> dict[str, Any]:
    doc_id = get_knowledge_store().upsert_document(
        title=request.title,
        content=request.content,
        metadata={**request.metadata, "uploaded_by": principal.user_id},
        doc_id=request.doc_id,
    )
    return {"doc_id": doc_id, "status": "upserted"}


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
