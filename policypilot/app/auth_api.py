"""JSON login and bearer sessions; no cookies, registration, or grant-edit endpoints."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from policypilot.app.models.domain import Permission, Principal, SystemRole
from policypilot.app.services.authentication import (
    AuthenticationError,
    AuthenticationService,
    LoginThrottled,
)
from policypilot.app.services.authorization import effective_permissions

router = APIRouter(prefix="/api/v1", tags=["authentication"])
_bearer = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$", strict=True)
    password: SecretStr = Field(min_length=1, max_length=1024)


class LoginResponse(BaseModel):
    access_token: str = Field(repr=False)
    token_type: Literal["bearer"] = Field(default="bearer")
    expires_in: int


class MeResponse(BaseModel):
    user_id: str
    content_permissions: list[Permission]
    system_roles: list[SystemRole]
    permission_version: int


def authentication(request: Request) -> AuthenticationService:
    service: AuthenticationService = request.app.state.authentication
    return service


Service = Annotated[AuthenticationService, Depends(authentication)]
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]


def require_token(credentials: Credentials) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials


Token = Annotated[str, Depends(require_token)]


def current_principal(service: Service, token: Token) -> Principal:
    try:
        return service.authenticate(token)
    except AuthenticationError:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


@router.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, service: Service) -> LoginResponse:
    try:
        caller = request.client.host if request.client is not None else "unknown"
        session = service.login(
            payload.username, payload.password.get_secret_value(), caller_id=caller
        )
        return LoginResponse(access_token=session.access_token, expires_in=session.expires_in)
    except LoginThrottled:
        raise HTTPException(
            status_code=429,
            detail="Login temporarily unavailable",
            headers={"Retry-After": "60"},
        ) from None
    except AuthenticationError:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None


@router.get("/me", response_model=MeResponse)
def me(principal: CurrentPrincipal) -> MeResponse:
    return MeResponse(
        user_id=principal.user_id,
        content_permissions=sorted(effective_permissions(principal)),
        system_roles=sorted(principal.system_roles),
        permission_version=principal.permission_version,
    )


@router.post("/auth/logout", status_code=204)
def logout(service: Service, token: Token) -> Response:
    try:
        # Idempotent for syntactically valid expired/already-revoked tokens.
        service.logout(token)
    except AuthenticationError:
        raise HTTPException(status_code=401, detail="Authentication required") from None
    return Response(status_code=204)
