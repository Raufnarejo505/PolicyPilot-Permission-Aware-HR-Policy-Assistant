"""ASGI query/source authorization tests; synthetic content and identities only."""

import secrets
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from policypilot.app.core.config import Settings
from policypilot.app.core.security import PasswordSecurity
from policypilot.app.main import create_app
from policypilot.app.models.content import DocumentMetadata
from policypilot.app.models.domain import Permission
from policypilot.app.services.content_store import ContentStore
from policypilot.app.services.identity_store import IdentityStore
from policypilot.app.services.ingestion import IngestionService, SourceDocument

pytestmark = pytest.mark.anyio

PASSWORD = secrets.token_urlsafe(24)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def prepared(name: str, labels: frozenset[Permission], text: str):
    metadata = DocumentMetadata(
        name,
        f"{name.title()} policy",
        f"https://example.invalid/{name}",
        date(2026, 10, 25),
        "Synthetic fixture; CC BY 4.0.",
        "v1",
        datetime(2026, 10, 25, tzinfo=UTC),
        labels,
    )
    return IngestionService().prepare(SourceDocument(metadata, text.encode()))


def build_app(tmp_path: Path, *, content: bool = True):
    identity_path = tmp_path / "identity.sqlite3"
    content_path = tmp_path / "content.sqlite3"
    identities = IdentityStore(identity_path)
    identities.initialize()
    identities.create_user(
        user_id="alice",
        username="alice",
        password_hash=PasswordSecurity().hash(PASSWORD),
        assigned_permissions=frozenset({Permission.HR}),
    )
    identities.create_user(
        user_id="bob",
        username="bob",
        password_hash=PasswordSecurity().hash(PASSWORD),
    )
    if content:
        store = ContentStore(content_path)
        store.initialize()
        store.replace_many(
            (
                prepared(
                    "hr", frozenset({Permission.HR}), "Secret HR leave policy for executives."
                ),
                prepared(
                    "public",
                    frozenset({Permission.PUBLIC}),
                    "Public leave policy for everyone.",
                ),
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
    return app, identities, content_path


async def login(client: httpx.AsyncClient, username: str) -> str:
    response = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 200
    return response.json()["access_token"]


async def test_query_and_source_roundtrip_with_no_store(tmp_path):
    app, _, _ = build_app(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        token = await login(client, "alice")
        headers = {"Authorization": f"Bearer {token}"}
        response = await client.post("/api/v1/query", headers=headers, json={"question": "leave"})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        assert body["status"] == "answered"
        assert body["citations"]
        assert "Secret HR" in body["answer"] or any(
            "Secret HR" in citation["snippet"] for citation in body["citations"]
        )
        source = await client.get(
            f"/api/v1/sources/{body['citations'][0]['chunk_id']}", headers=headers
        )
        assert source.status_code == 200
        assert source.headers["cache-control"] == "no-store"
        assert source.json()["text"]


async def test_employee_cannot_query_or_read_hr_source(tmp_path):
    app, _, _ = build_app(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        alice = await login(client, "alice")
        bob = await login(client, "bob")
        hr_answer = await client.post(
            "/api/v1/query",
            headers={"Authorization": f"Bearer {alice}"},
            json={"question": "executives"},
        )
        chunk_id = hr_answer.json()["citations"][0]["chunk_id"]
        answer = await client.post(
            "/api/v1/query",
            headers={"Authorization": f"Bearer {bob}"},
            json={"question": "executives"},
        )
        unauthorized = await client.get(
            f"/api/v1/sources/{chunk_id}", headers={"Authorization": f"Bearer {bob}"}
        )
        missing = await client.get(
            "/api/v1/sources/absent", headers={"Authorization": f"Bearer {bob}"}
        )
    assert answer.status_code == 200
    assert answer.json()["status"] == "abstained"
    assert answer.json()["citations"] == []
    assert unauthorized.status_code == missing.status_code == 404
    assert unauthorized.json() == missing.json() == {"detail": "Source not found"}


async def test_permission_change_applies_to_existing_session(tmp_path):
    app, identities, _ = build_app(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        token = await login(client, "alice")
        headers = {"Authorization": f"Bearer {token}"}
        before = await client.post(
            "/api/v1/query", headers=headers, json={"question": "executives"}
        )
        chunk_id = before.json()["citations"][0]["chunk_id"]
        identities.set_access("alice", assigned_permissions=frozenset(), system_roles=frozenset())
        after = await client.post("/api/v1/query", headers=headers, json={"question": "executives"})
        source = await client.get(f"/api/v1/sources/{chunk_id}", headers=headers)
    assert before.json()["status"] == "answered"
    assert after.json()["status"] == "abstained"
    assert source.status_code == 404


async def test_missing_or_uninitialized_content_store_is_generic(tmp_path):
    app, _, _ = build_app(tmp_path, content=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        token = await login(client, "alice")
        headers = {"Authorization": f"Bearer {token}"}
        answer = await client.post("/api/v1/query", headers=headers, json={"question": "leave"})
        source = await client.get("/api/v1/sources/chunk", headers=headers)
    assert answer.status_code == 503
    assert answer.json() == {"detail": "Query unavailable"}
    assert source.status_code == 503
    assert source.json() == {"detail": "Source unavailable"}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"question": ""},
        {"question": "x" * 2001},
        {"question": "leave", "permissions": ["HR"]},
        {"question": 1},
    ],
)
async def test_invalid_query_payloads_are_generic(tmp_path, payload):
    app, _, _ = build_app(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        token = await login(client, "alice")
        response = await client.post(
            "/api/v1/query",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid request"}


async def test_oversized_query_body_is_rejected_before_parsing(tmp_path):
    app, _, _ = build_app(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        token = await login(client, "alice")
        response = await client.post(
            "/api/v1/query",
            headers={"Authorization": f"Bearer {token}"},
            content=b"x" * 5000,
        )
    assert response.status_code == 413
    assert response.json() == {"detail": "Request too large"}
    assert response.headers["cache-control"] == "no-store"


async def test_query_and_source_require_bearer(tmp_path):
    app, _, _ = build_app(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        query = await client.post("/api/v1/query", json={"question": "leave"})
        source = await client.get("/api/v1/sources/chunk")
    assert query.status_code == 401
    assert source.status_code == 401


async def test_answer_does_not_expose_denied_snippet_or_question(tmp_path):
    app, _, _ = build_app(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        token = await login(client, "bob")
        response = await client.post(
            "/api/v1/query",
            headers={"Authorization": f"Bearer {token}"},
            json={"question": "executives"},
        )
    assert response.status_code == 200
    text = response.text
    assert "Secret HR" not in text
    assert "executives" not in response.json()["answer"]


async def test_policy_list_is_permission_filtered_and_has_no_passage_text(tmp_path):
    app, _, _ = build_app(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        alice = await login(client, "alice")
        bob = await login(client, "bob")
        alice_response = await client.get(
            "/api/v1/policies", headers={"Authorization": f"Bearer {alice}"}
        )
        bob_response = await client.get(
            "/api/v1/policies", headers={"Authorization": f"Bearer {bob}"}
        )
    assert alice_response.status_code == bob_response.status_code == 200
    assert alice_response.headers["cache-control"] == "no-store"
    alice_body = alice_response.json()
    bob_body = bob_response.json()
    assert {policy["title"] for policy in alice_body["policies"]} == {
        "Hr policy",
        "Public policy",
    }
    assert {policy["title"] for policy in bob_body["policies"]} == {"Public policy"}
    policy = bob_body["policies"][0]
    assert set(policy) == {
        "document_id",
        "title",
        "source_url",
        "version",
        "updated_at",
        "sections",
        "chunks",
        "visible_via",
    }
    assert policy["visible_via"] == ["Public"]
    assert policy["chunks"] >= 1
    assert policy["sections"]
    assert "Secret HR" not in bob_response.text
    assert "Public leave policy for everyone" not in bob_response.text


async def test_policy_list_requires_bearer_and_fails_generically(tmp_path):
    app, _, _ = build_app(tmp_path, content=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        anonymous = await client.get("/api/v1/policies")
        token = await login(client, "alice")
        missing = await client.get("/api/v1/policies", headers={"Authorization": f"Bearer {token}"})
    assert anonymous.status_code == 401
    assert missing.status_code == 503
    assert missing.json() == {"detail": "Policies unavailable"}
