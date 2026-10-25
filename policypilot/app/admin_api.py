"""Read-only administrator overview; Admin is a feature role, never content access."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from policypilot.app.auth_api import CurrentPrincipal
from policypilot.app.models.domain import Permission, SystemRole
from policypilot.app.services.authorization import has_system_role
from policypilot.app.services.content_store import ContentStore
from policypilot.app.services.identity_store import IdentityStore, UserAccount

router = APIRouter(prefix="/api/v1", tags=["administration"])


class AdminAccountResponse(BaseModel):
    user_id: str
    username: str
    enabled: bool
    permissions: list[Permission]
    system_roles: list[SystemRole]
    permission_version: int


class CorpusStatsResponse(BaseModel):
    documents: int = Field(ge=0)
    chunks: int = Field(ge=0)


class AdminOverviewResponse(BaseModel):
    accounts: list[AdminAccountResponse]
    corpus: CorpusStatsResponse


def _admin_service(request: Request) -> tuple[IdentityStore, ContentStore]:
    return request.app.state.identity_store, request.app.state.content_store


AdminServices = Annotated[tuple[IdentityStore, ContentStore], Depends(_admin_service)]


@router.get("/admin/overview", response_model=AdminOverviewResponse)
def overview(principal: CurrentPrincipal, services: AdminServices) -> AdminOverviewResponse:
    if not has_system_role(principal, SystemRole.ADMIN):
        raise HTTPException(status_code=403, detail="Administrator access required")
    identities, content = services
    try:
        accounts: tuple[UserAccount, ...] = identities.list_users()
        documents, chunks = content.counts()
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=503, detail="Administration unavailable") from None
    return AdminOverviewResponse(
        accounts=[
            AdminAccountResponse(
                user_id=account.user_id,
                username=account.username,
                enabled=account.enabled,
                permissions=sorted(account.assigned_permissions),
                system_roles=sorted(account.system_roles),
                permission_version=account.permission_version,
            )
            for account in accounts
        ],
        corpus=CorpusStatsResponse(documents=documents, chunks=chunks),
    )
