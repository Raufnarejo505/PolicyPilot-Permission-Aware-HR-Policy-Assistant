"""Authorized query, source-read, and policy-list API; no client-supplied grants."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from policypilot.app.auth_api import CurrentPrincipal
from policypilot.app.models.domain import Permission
from policypilot.app.services.query import (
    Answer,
    PolicySummary,
    QueryError,
    QueryService,
    Source,
    SourceNotFound,
)

router = APIRouter(prefix="/api/v1", tags=["query"])


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=2000)


class CitationResponse(BaseModel):
    chunk_id: str
    document_id: str
    title: str
    section: str
    source_url: str
    snippet: str = Field(repr=False)


class QueryResponse(BaseModel):
    status: Literal["answered", "abstained"]
    answer: str = Field(repr=False)
    citations: list[CitationResponse] = Field(default_factory=list, repr=False)


class SourceResponse(BaseModel):
    chunk_id: str
    document_id: str
    title: str
    section: str
    source_url: str
    text: str = Field(repr=False)


class PolicySummaryResponse(BaseModel):
    document_id: str
    title: str
    source_url: str
    version: str
    updated_at: datetime
    sections: list[str]
    chunks: int = Field(ge=1)
    visible_via: list[Permission]


class PolicyListResponse(BaseModel):
    policies: list[PolicySummaryResponse]


def query_service(request: Request) -> QueryService:
    service: QueryService = request.app.state.query
    return service


Service = Annotated[QueryService, Depends(query_service)]


def _query_response(answer: Answer) -> QueryResponse:
    return QueryResponse(
        status=answer.status,
        answer=answer.text,
        citations=[
            CitationResponse(
                chunk_id=citation.chunk_id,
                document_id=citation.document_id,
                title=citation.title,
                section=citation.section,
                source_url=citation.source_url,
                snippet=citation.snippet,
            )
            for citation in answer.citations
        ],
    )


def _policy_response(policy: PolicySummary) -> PolicySummaryResponse:
    return PolicySummaryResponse(
        document_id=policy.document_id,
        title=policy.title,
        source_url=policy.source_url,
        version=policy.version,
        updated_at=policy.updated_at,
        sections=list(policy.sections),
        chunks=policy.chunks,
        visible_via=list(policy.visible_via),
    )


@router.get("/policies", response_model=PolicyListResponse)
def list_policies(principal: CurrentPrincipal, service: Service) -> PolicyListResponse:
    try:
        policies = service.policies(principal.user_id)
    except QueryError:
        raise HTTPException(status_code=503, detail="Policies unavailable") from None
    return PolicyListResponse(policies=[_policy_response(policy) for policy in policies])


@router.post("/query", response_model=QueryResponse)
def ask(payload: QueryRequest, principal: CurrentPrincipal, service: Service) -> QueryResponse:
    try:
        return _query_response(service.answer(principal.user_id, payload.question))
    except QueryError:
        raise HTTPException(status_code=503, detail="Query unavailable") from None


@router.get("/sources/{chunk_id}", response_model=SourceResponse)
def read_source(chunk_id: str, principal: CurrentPrincipal, service: Service) -> SourceResponse:
    try:
        source: Source = service.source(principal.user_id, chunk_id)
    except SourceNotFound:
        raise HTTPException(status_code=404, detail="Source not found") from None
    except QueryError:
        raise HTTPException(status_code=503, detail="Source unavailable") from None
    chunk = source.chunk
    return SourceResponse(
        chunk_id=chunk.chunk_id,
        document_id=chunk.document.doc_id,
        title=chunk.document.title,
        section=chunk.section,
        source_url=chunk.document.source_url,
        text=chunk.text,
    )
