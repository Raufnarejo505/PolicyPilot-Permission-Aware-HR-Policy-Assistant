"""Exercise the actual ASGI app without starting external services."""

import httpx
import pytest
from pydantic import ValidationError

from policypilot.app.core.config import Settings
from policypilot.app.main import create_app

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_liveness_has_no_sensitive_metadata():
    app = create_app(Settings(environment="test", _env_file=None))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert app.state.settings.environment == "test"


def test_default_factory_loads_configuration(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POLICYPILOT_ENVIRONMENT", "test")
    assert create_app().state.settings.environment == "test"


def test_invalid_configuration_prevents_app_creation(monkeypatch):
    monkeypatch.setenv("POLICYPILOT_CACHE_ENABLED", "true")
    with pytest.raises(ValidationError):
        create_app()


@pytest.mark.parametrize("path", ["/docs", "/openapi.json", "/redoc"])
async def test_production_disables_api_documentation(path):
    app = create_app(Settings(environment="production", _env_file=None))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get(path)).status_code == 404


async def test_development_openapi_has_health_route():
    app = create_app(Settings(environment="development", _env_file=None))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/openapi.json")
        assert response.status_code == 200
        assert "/api/v1/health" in response.json()["paths"]
        assert (await client.get("/docs")).status_code == 200


@pytest.mark.parametrize("path", ["/api/v1/query", "/api/v1/sources/test", "/api/v1/admin"])
async def test_no_unprotected_placeholder_business_endpoints(path):
    app = create_app(Settings(environment="test", _env_file=None))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get(path)).status_code == 404
