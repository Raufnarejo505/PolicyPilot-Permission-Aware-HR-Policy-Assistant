"""Application factory: liveness and server-authoritative authentication."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from policypilot.app.auth_api import router
from policypilot.app.core.config import Settings
from policypilot.app.core.http_security import AuthenticationBoundary
from policypilot.app.services.authentication import AuthenticationService
from policypilot.app.services.identity_store import IdentityStore


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
    app.state.settings = config
    app.state.authentication = AuthenticationService(
        IdentityStore(config.identity_database), session_ttl_seconds=config.session_ttl_seconds
    )
    app.add_middleware(AuthenticationBoundary)
    app.include_router(router)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        # Pydantic errors otherwise reflect submitted credentials and untrusted grant fields.
        return JSONResponse(status_code=422, content={"detail": "Invalid request"})

    @app.get("/api/v1/health", response_model=HealthResponse, tags=["health"])
    def health() -> HealthResponse:
        return HealthResponse()

    return app
