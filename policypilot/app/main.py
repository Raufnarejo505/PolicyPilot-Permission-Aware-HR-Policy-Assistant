"""Application factory: liveness, server-authoritative authentication, and queries."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from policypilot.app.admin_api import router as admin_router
from policypilot.app.auth_api import router
from policypilot.app.core.config import Settings
from policypilot.app.core.http_security import AuthenticationBoundary
from policypilot.app.query_api import router as query_router
from policypilot.app.services.authentication import AuthenticationService
from policypilot.app.services.authorization import AuthorizationService
from policypilot.app.services.content_store import ContentStore
from policypilot.app.services.identity_store import IdentityStore
from policypilot.app.services.query import QueryService


class HealthResponse(BaseModel):
    status: str = "ok"


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings if settings is not None else Settings()
    app = FastAPI(
        title="PolicyPilot",
        version="0.1.0",
        docs_url="/docs" if config.environment != "production" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if config.environment != "production" else None,
    )
    identity = IdentityStore(config.identity_database)
    content = ContentStore(config.content_database)
    app.state.settings = config
    app.state.identity_store = identity
    app.state.content_store = content
    app.state.authentication = AuthenticationService(
        identity, session_ttl_seconds=config.session_ttl_seconds
    )
    app.state.authorization = AuthorizationService(identity)
    app.state.query = QueryService(
        content,
        app.state.authorization,
        retrieval_limit=config.retrieval_limit,
        top_n=config.answer_top_n,
    )
    app.add_middleware(AuthenticationBoundary)
    app.include_router(router)
    app.include_router(query_router)
    app.include_router(admin_router)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        # Pydantic errors otherwise reflect submitted credentials and untrusted grant fields.
        return JSONResponse(status_code=422, content={"detail": "Invalid request"})

    @app.get("/api/v1/health", response_model=HealthResponse, tags=["health"])
    def health() -> HealthResponse:
        return HealthResponse()

    return app
