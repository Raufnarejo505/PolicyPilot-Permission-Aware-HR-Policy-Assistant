"""Admin overview authorization tests; no password hashes or restricted text exposed."""

import secrets
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from policypilot.app.core.config import Settings
from policypilot.app.core.security import PasswordSecurity
from policypilot.app.main import create_app
from policypilot.app.models.content import DocumentMetadata
from policypilot.app.models.domain import Permission, SystemRole
from policypilot.app.services.content_store import ContentStore
from policypilot.app.services.identity_store import IdentityStore
from policypilot.app.services.ingestion import IngestionService, SourceDocument

pytestmark = pytest.mark.anyio
PASSWORD = secrets.token_urlsafe(24)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def build_app(tmp_path: Path, *, content: bool = True):
    identity_path = tmp_path / "identity.sqlite3"
    content_path = tmp_path / "content.sqlite3"
    identities = IdentityStore(identity_path)
    identities.initialize()
    identities.create_user(
        user_id="admin",
        username="admin",
        password_hash=PasswordSecurity().hash(PASSWORD),
        system_roles=frozenset({SystemRole.ADMIN}),
    )
    identities.create_user(
        user_id="employee",
        username="employee",
        password_hash=PasswordSecurity().hash(PASSWORD),
        assigned_permissions=frozenset({Permission.HR}),
    )
    if content:
        store = ContentStore(content_path)
        store.initialize()
        store.replace(
            IngestionService().prepare(
                SourceDocument(
                    DocumentMetadata(
                        "doc-1",
                        "Synthetic policy",
                        "https://example.invalid/policy",
                        date(2026, 10, 25),
                        "Synthetic fixture; CC BY 4.0.",
                        "v1",
                        datetime(2026, 10, 25, tzinfo=UTC),
                        frozenset({Permission.PUBLIC}),
                    ),
                    b"Synthetic public policy text.",
                )
            )
        )
    app = create_app(
        Settings(
            environment="test",
            identity_database=identity_path,
            content_database=content_path,
            _env_file=None,
        )
    )
    return app, identities, identity_path


async def login(client: httpx.AsyncClient, username: str) -> str:
    response = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 200
    return response.json()["access_token"]


async def test_admin_overview_lists_accounts_and_corpus_without_secrets(tmp_path):
    app, _, _ = build_app(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        token = await login(client, "admin")
        response = await client.get(
            "/api/v1/admin/overview", headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert set(body) == {"accounts", "corpus"}
    assert body["corpus"] == {"documents": 1, "chunks": 1}
    assert [account["username"] for account in body["accounts"]] == ["admin", "employee"]
    first = body["accounts"][0]
    assert set(first) == {
        "user_id",
        "username",
        "enabled",
        "permissions",
        "system_roles",
        "permission_version",
    }
    assert first["enabled"] is True
    assert first["system_roles"] == ["Admin"]
    assert first["permissions"] == []
    assert "password" not in response.text.lower()
    assert "Synthetic public policy text" not in response.text


async def test_non_admin_cannot_read_admin_overview(tmp_path):
    app, _, _ = build_app(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        token = await login(client, "employee")
        response = await client.get(
            "/api/v1/admin/overview", headers={"Authorization": f"Bearer {token}"}
        )
        anonymous = await client.get("/api/v1/admin/overview")
    assert response.status_code == 403
    assert response.json() == {"detail": "Administrator access required"}
    assert "accounts" not in response.text
    assert anonymous.status_code == 401


async def test_missing_or_corrupt_stores_fail_generically(tmp_path):
    app, _, identity_path = build_app(tmp_path, content=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        token = await login(client, "admin")
        headers = {"Authorization": f"Bearer {token}"}
        missing = await client.get("/api/v1/admin/overview", headers=headers)
        assert missing.status_code == 503
        assert missing.json() == {"detail": "Administration unavailable"}

        with closing(sqlite3.connect(identity_path)) as connection, connection:
            connection.execute(
                "UPDATE users SET permissions = ? WHERE username = ?",
                ('["NotAPermission"]', "employee"),
            )
        corrupt = await client.get("/api/v1/admin/overview", headers=headers)
    assert corrupt.status_code == 503
    assert corrupt.json() == {"detail": "Administration unavailable"}


async def test_admin_overview_does_not_grant_content_access(tmp_path):
    app, identities, _ = build_app(tmp_path)
    identities.set_access(
        "admin",
        assigned_permissions=frozenset(),
        system_roles=frozenset({SystemRole.ADMIN}),
    )
    app.state.content_store.replace(
        IngestionService().prepare(
            SourceDocument(
                DocumentMetadata(
                    "doc-hr",
                    "Restricted HR policy",
                    "https://example.invalid/hr",
                    date(2026, 10, 25),
                    "Synthetic fixture; CC BY 4.0.",
                    "v1",
                    datetime(2026, 10, 25, tzinfo=UTC),
                    frozenset({Permission.HR}),
                ),
                b"Secret HR executives compensation policy.",
            )
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        token = await login(client, "admin")
        overview = await client.get(
            "/api/v1/admin/overview", headers={"Authorization": f"Bearer {token}"}
        )
        query = await client.post(
            "/api/v1/query",
            headers={"Authorization": f"Bearer {token}"},
            json={"question": "secret executives compensation"},
        )
    assert overview.status_code == 200
    assert overview.json()["corpus"]["documents"] == 2
    assert query.status_code == 200
    assert query.json()["status"] == "abstained"
    assert query.json()["citations"] == []
