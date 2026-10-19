"""Authentication contracts through the real ASGI boundary and temporary SQLite stores."""

import base64
import hashlib
import json
import re
import secrets
import sqlite3
from collections.abc import AsyncIterator
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from argon2 import Type, extract_parameters
from fastapi import FastAPI
from pydantic import SecretStr

from policypilot.app.auth_api import LoginRequest
from policypilot.app.core.config import Settings
from policypilot.app.core.security import PasswordSecurity
from policypilot.app.main import create_app
from policypilot.app.models.domain import Permission, SystemRole
from policypilot.app.services.authentication import AuthenticationService
from policypilot.app.services.identity_store import IdentityStore

pytestmark = pytest.mark.anyio

LOGIN = "/api/v1/auth/login"
ME = "/api/v1/me"
LOGOUT = "/api/v1/auth/logout"
USERNAME = "Generated.Test-User_01"
USER_ID = "generated-test-user-id"
BASELINE = ["Employee", "Public"]
DENIED = {"detail": "Authentication required"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@dataclass(frozen=True)
class PasswordMaterial:
    security: PasswordSecurity
    password: str
    password_hash: str
    replacement: str
    replacement_hash: str


@pytest.fixture(scope="module")
def passwords():
    # Share real default-cost hashes; never lower Argon2 costs to accelerate API tests.
    security = PasswordSecurity()
    password = "generated-test-only-" + secrets.token_urlsafe(24)
    replacement = "generated-reset-only-" + secrets.token_urlsafe(24)
    return PasswordMaterial(
        security, password, security.hash(password), replacement, security.hash(replacement)
    )


@dataclass
class AuthHarness:
    db: Path
    store: IdentityStore
    app: FastAPI
    now: list[int]
    passwords: PasswordMaterial

    def new_app(self):
        store = IdentityStore(self.db)
        app = create_app(Settings(environment="test", identity_database=self.db, _env_file=None))
        app.state.authentication = AuthenticationService(
            store, passwords=self.passwords.security, clock=lambda: self.now[0]
        )
        return app

    def execute(self, sql, parameters=()):
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(sql, parameters)

    def rows(self, sql, parameters=()):
        with closing(sqlite3.connect(self.db)) as connection:
            return connection.execute(sql, parameters).fetchall()


@pytest.fixture
def auth(tmp_path, passwords):
    db = tmp_path / "identity.sqlite3"
    store = IdentityStore(db)
    store.initialize()
    # An epoch-aligned minute makes the throttle's exact reset boundary deterministic.
    now = [1_800_000_000]
    app = create_app(Settings(environment="test", identity_database=db, _env_file=None))
    app.state.authentication = AuthenticationService(
        store, passwords=passwords.security, clock=lambda: now[0]
    )
    return AuthHarness(db, store, app, now, passwords)


@pytest.fixture
def user(auth):
    auth.store.create_user(
        user_id=USER_ID, username=USERNAME, password_hash=auth.passwords.password_hash
    )
    return USER_ID


def assert_private(response):
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert "set-cookie" not in response.headers


async def private_response(response):
    assert_private(response)


def asgi_client(app, *, source=("127.0.0.1", 1234)):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=source),
        base_url="http://test",
        event_hooks={"response": [private_response]},
    )


@pytest.fixture
async def client(auth):
    async with asgi_client(auth.app) as result:
        yield result
        assert not result.cookies


async def login(client, auth, *, username=USERNAME, password=None):
    response = await client.post(
        LOGIN,
        json={
            "username": username,
            "password": auth.passwords.password if password is None else password,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"access_token", "token_type", "expires_in"}
    assert payload["token_type"] == "bearer"  # noqa: S105 — protocol label, not a password
    assert type(payload["expires_in"]) is int
    assert payload["expires_in"] == 3600
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", payload["access_token"])
    return payload["access_token"]


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def assert_denied(response, *, challenge=True):
    assert response.status_code == 401
    assert response.json() == DENIED
    if challenge:
        assert response.headers["www-authenticate"] == "Bearer"


def expected_me(*, permissions=BASELINE, roles=(), version=0):
    return {
        "user_id": USER_ID,
        "content_permissions": sorted(permissions),
        "system_roles": sorted(roles),
        "permission_version": version,
    }


async def test_real_argon2id_opaque_login_me_logout_roundtrip(auth, user, client):
    parameters = extract_parameters(auth.passwords.password_hash)
    assert parameters.type is Type.ID
    assert parameters.memory_cost == 65536
    assert parameters.time_cost == 3
    assert parameters.parallelism == 4
    token = await login(client, auth)
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    assert auth.rows("SELECT token_digest, user_id, expires_at FROM sessions") == [
        (digest, user, auth.now[0] + 3600)
    ]
    response = await client.get(ME, headers=bearer(token))
    assert response.status_code == 200
    assert response.json() == expected_me()
    assert token not in response.text
    assert auth.passwords.password not in response.text
    assert auth.passwords.password_hash not in response.text
    logout = await client.post(LOGOUT, headers=bearer(token))
    assert logout.status_code == 204
    assert logout.content == b""
    assert auth.rows("SELECT token_digest FROM sessions") == []
    assert_denied(await client.get(ME, headers=bearer(token)))


async def test_empty_store_has_no_default_users(auth, client):
    assert auth.rows("SELECT user_id FROM users") == []
    response = await client.post(
        LOGIN, json={"username": "generated-nonexistent-user", "password": auth.passwords.password}
    )
    assert_denied(response)
    assert auth.rows("SELECT user_id FROM users") == []
    assert auth.rows("SELECT token_digest FROM sessions") == []


@pytest.mark.parametrize(
    "roles", [frozenset({role}) for role in SystemRole] + [frozenset(SystemRole)]
)
async def test_system_roles_never_imply_restricted_content(auth, user, client, roles):
    auth.store.set_access(user, assigned_permissions=frozenset(), system_roles=roles)
    token = await login(client, auth)
    response = await client.get(ME, headers=bearer(token))
    assert response.status_code == 200
    assert response.json() == expected_me(roles=roles, version=1)


async def test_grants_and_roles_reload_on_every_request_without_relogin(auth, user, client):
    token = await login(client, auth)
    for version, (grants, roles) in enumerate(
        [
            ({Permission.HR, Permission.MANAGER}, {SystemRole.ADMIN}),
            ({Permission.FINANCE}, {SystemRole.EVALUATOR}),
            (set(), set()),
        ],
        start=1,
    ):
        auth.store.set_access(
            user, assigned_permissions=frozenset(grants), system_roles=frozenset(roles)
        )
        response = await client.get(ME, headers=bearer(token))
        assert response.status_code == 200
        assert response.json() == expected_me(
            permissions=[*BASELINE, *grants], roles=roles, version=version
        )
    assert len(auth.rows("SELECT token_digest FROM sessions")) == 1


def forged_jwt():
    def segment(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    return ".".join(
        [
            segment({"alg": "none", "typ": "JWT"}),
            segment(
                {
                    "sub": USER_ID,
                    "content_permissions": ["Finance", "HR"],
                    "system_roles": ["Admin"],
                    "permission_version": 0,
                    "exp": 4_000_000_000,
                }
            ),
            "",
        ]
    )


@pytest.mark.parametrize("path", [ME, LOGOUT])
@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "",
        "Bearer",
        "Bearer ",
        "Basic generated-test-only",
        "Token generated-test-only",
        "Bearer too-short",
        "Bearer " + "!" * 43,
        "Bearer " + "x" * 42,
        "Bearer " + "x" * 44,
        "Bearer " + "x" * 43 + " trailing",
        "Bearer " + forged_jwt(),
    ],
)
async def test_missing_malformed_wrong_scheme_and_forged_jwt_deny(client, path, authorization):
    headers = {} if authorization is None else {"Authorization": authorization}
    response = await client.request("GET" if path == ME else "POST", path, headers=headers)
    assert_denied(response, challenge=path == ME)


async def test_unknown_well_formed_token_denies_me_but_logout_is_idempotent(client):
    headers = bearer(secrets.token_urlsafe(32))
    assert_denied(await client.get(ME, headers=headers))
    response = await client.post(LOGOUT, headers=headers)
    assert response.status_code == 204
    assert response.content == b""


async def test_bearer_scheme_is_case_insensitive(auth, user, client):
    token = await login(client, auth)
    response = await client.get(ME, headers={"Authorization": f"bEaReR {token}"})
    assert response.status_code == 200
    assert response.json() == expected_me()


@pytest.mark.parametrize("with_token", [False, True])
@pytest.mark.parametrize("channel", ["query", "body", "headers"])
async def test_client_identity_and_grants_are_never_authority(
    auth, user, client, channel, with_token
):
    token = await login(client, auth)
    claims = {
        "user_id": "generated-forged-admin",
        "username": USERNAME,
        "content_permissions": ["Finance", "HR", "Manager"],
        "assigned_permissions": ["Finance"],
        "system_roles": ["Admin", "Evaluator"],
        "permission_version": 999,
        "access_token": token,
    }
    kwargs = {"headers": bearer(token) if with_token else {}}
    if channel == "query":
        kwargs["params"] = {key: json.dumps(value) for key, value in claims.items()}
    elif channel == "body":
        kwargs["json"] = claims
    else:
        kwargs["headers"].update(
            {
                "X-User-Id": claims["user_id"],
                "X-Content-Permissions": "Finance,HR,Manager",
                "X-System-Roles": "Admin,Evaluator",
                "X-Permission-Version": "999",
                "X-Access-Token": token,
            }
        )
    response = await client.request("GET", ME, **kwargs)
    if with_token:
        assert response.status_code == 200
        assert response.json() == expected_me()
    else:
        assert_denied(response)


@pytest.mark.parametrize(
    "field", ["user_id", "content_permissions", "assigned_permissions", "system_roles", "role"]
)
async def test_login_rejects_extra_client_grant_fields_without_echo(auth, user, client, field):
    response = await client.post(
        LOGIN,
        json={
            "username": USERNAME,
            "password": auth.passwords.password,
            field: "generated-sensitive-client-field",
        },
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid request"}
    assert auth.rows("SELECT token_digest FROM sessions") == []
    assert auth.rows("SELECT username_digest FROM login_attempts") == []


async def test_login_query_and_headers_cannot_supply_authoritative_grants(auth, user, client):
    response = await client.post(
        LOGIN,
        params={"content_permissions": "Finance", "system_roles": "Admin"},
        headers={"X-Content-Permissions": "Finance", "X-System-Roles": "Admin"},
        json={"username": USERNAME, "password": auth.passwords.password},
    )
    assert response.status_code == 200
    me = await client.get(ME, headers=bearer(response.json()["access_token"]))
    assert me.status_code == 200
    assert me.json() == expected_me()


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {},
        {"username": USERNAME},
        {"password": "generated-test-invalid-body-secret"},
        {"username": "", "password": "generated-test-invalid-body-secret"},
        {"username": "a" * 65, "password": "generated-test-invalid-body-secret"},
        {"username": "tést", "password": "generated-test-invalid-body-secret"},
        {"username": "name with spaces", "password": "generated-test-invalid-body-secret"},
        {"username": "name\n", "password": "generated-test-invalid-body-secret"},
        {"username": 123, "password": "generated-test-invalid-body-secret"},
        {"username": USERNAME, "password": ""},
        {"username": USERNAME, "password": "s" * 1025},
        {"username": USERNAME, "password": 123},
        {"username": USERNAME, "password": None},
        {"username": USERNAME, "password": ["generated-test-invalid-body-secret"]},
    ],
)
async def test_invalid_login_bodies_are_generic_and_do_not_echo_fields(auth, client, body):
    response = await client.post(
        LOGIN, content=json.dumps(body), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid request"}
    assert auth.rows("SELECT username_digest FROM login_attempts") == []


async def test_malformed_json_does_not_echo_submitted_secret(auth, client):
    response = await client.post(
        LOGIN,
        content='{"password": "' + auth.passwords.password + '", "username":',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid request"}


def test_login_password_is_secretstr_and_repr_is_redacted(passwords):
    payload = LoginRequest(username=USERNAME, password=passwords.password)
    assert isinstance(payload.password, SecretStr)
    assert payload.password.get_secret_value() == passwords.password
    assert passwords.password not in repr(payload)
    assert passwords.password not in payload.model_dump_json()


@pytest.mark.parametrize("username", ["a", "A" * 64, "test.user-name_01"])
async def test_ascii_username_boundaries(auth, client, username):
    auth.store.create_user(
        user_id=USER_ID, username=username, password_hash=auth.passwords.password_hash
    )
    token = await login(client, auth, username=username)
    response = await client.get(ME, headers=bearer(token))
    assert response.status_code == 200
    assert response.json() == expected_me()


async def test_username_is_case_sensitive(auth, user, client):
    assert_denied(
        await client.post(
            LOGIN, json={"username": USERNAME.lower(), "password": auth.passwords.password}
        )
    )
    await login(client, auth)


async def test_missing_wrong_password_and_disabled_credentials_are_indistinguishable(
    auth, user, client
):
    missing = await client.post(
        LOGIN, json={"username": "generated-missing-user", "password": auth.passwords.password}
    )
    wrong = await client.post(
        LOGIN,
        json={
            "username": USERNAME,
            "password": "generated-wrong-only-" + secrets.token_urlsafe(16),
        },
    )
    auth.store.set_enabled(user, False)
    disabled = await client.post(
        LOGIN, json={"username": USERNAME, "password": auth.passwords.password}
    )
    for response in (missing, wrong, disabled):
        assert_denied(response)
    assert missing.content == wrong.content == disabled.content
    assert auth.rows("SELECT token_digest FROM sessions") == []


@pytest.mark.parametrize(
    "mutation", ["disabled", "deleted", "orphaned-session", "missing-resolution"]
)
async def test_missing_deleted_or_disabled_current_user_denies(
    auth, user, client, monkeypatch, mutation
):
    token = await login(client, auth)
    if mutation == "disabled":
        # Bypass set_enabled's eager revocation to test the request-time check independently.
        auth.execute("UPDATE users SET enabled = 0 WHERE user_id = ?", (user,))
    elif mutation == "deleted":
        auth.execute("DELETE FROM users WHERE user_id = ?", (user,))
    elif mutation == "orphaned-session":
        with closing(sqlite3.connect(auth.db)) as connection, connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("DELETE FROM users WHERE user_id = ?", (user,))
        assert len(auth.rows("SELECT token_digest FROM sessions")) == 1
    else:
        monkeypatch.setattr(auth.store, "get_user", lambda user_id: None)
    assert_denied(await client.get(ME, headers=bearer(token)))


@pytest.mark.parametrize("column", ["permissions", "system_roles"])
@pytest.mark.parametrize("encoded", ['["Unknown"]', '"Finance"', "[null]", "{}", "not-json"])
async def test_corrupt_stored_grants_fail_closed(auth, user, client, column, encoded):
    token = await login(client, auth)
    sql = {
        "permissions": "UPDATE users SET permissions = ? WHERE user_id = ?",
        "system_roles": "UPDATE users SET system_roles = ? WHERE user_id = ?",
    }[column]
    auth.execute(sql, (encoded, user))
    assert_denied(await client.get(ME, headers=bearer(token)))
    assert_denied(
        await client.post(LOGIN, json={"username": USERNAME, "password": auth.passwords.password})
    )


@pytest.mark.parametrize(
    ("operation", "method"),
    [
        ("login", "consume_login_attempt"),
        ("login", "get_credentials"),
        ("login", "create_session"),
        ("me", "get_session"),
        ("me", "get_user"),
        ("logout", "revoke_session"),
    ],
)
async def test_store_exceptions_are_generic_401_without_internal_details(
    auth, user, client, monkeypatch, operation, method, caplog
):
    token = await login(client, auth)
    internal = "generated-internal-only-sqlite-path-and-secret-" + secrets.token_hex(8)
    monkeypatch.setattr(auth.store, method, Mock(side_effect=sqlite3.OperationalError(internal)))
    if operation == "login":
        response = await client.post(
            LOGIN, json={"username": USERNAME, "password": auth.passwords.password}
        )
    elif operation == "me":
        response = await client.get(ME, headers=bearer(token))
    else:
        response = await client.post(LOGOUT, headers=bearer(token))
    assert_denied(response, challenge=operation != "logout")
    for sensitive in (internal, token, auth.passwords.password, auth.passwords.password_hash):
        assert sensitive not in response.text
        assert sensitive not in caplog.text


async def test_password_reset_revokes_every_existing_session(auth, user, client):
    tokens = [await login(client, auth), await login(client, auth)]
    assert tokens[0] != tokens[1]
    auth.store.set_password_hash(user, auth.passwords.replacement_hash)
    assert auth.rows("SELECT token_digest FROM sessions") == []
    for token in tokens:
        assert_denied(await client.get(ME, headers=bearer(token)))
    assert_denied(
        await client.post(LOGIN, json={"username": USERNAME, "password": auth.passwords.password})
    )
    token = await login(client, auth, password=auth.passwords.replacement)
    response = await client.get(ME, headers=bearer(token))
    assert response.status_code == 200
    assert response.json() == expected_me(version=1)


async def test_session_expiry_exact_boundary_and_expired_logout_idempotence(auth, user, client):
    token = await login(client, auth)
    auth.now[0] += 3599
    assert (await client.get(ME, headers=bearer(token))).status_code == 200
    auth.now[0] += 1
    assert_denied(await client.get(ME, headers=bearer(token)))
    for _ in range(2):
        response = await client.post(LOGOUT, headers=bearer(token))
        assert response.status_code == 204
        assert response.content == b""
    assert_denied(await client.get(ME, headers=bearer(token)))


async def test_revoked_logout_is_idempotent_and_does_not_revoke_other_sessions(auth, user, client):
    first = await login(client, auth)
    second = await login(client, auth)
    for _ in range(2):
        response = await client.post(LOGOUT, headers=bearer(first))
        assert response.status_code == 204
        assert response.content == b""
    assert_denied(await client.get(ME, headers=bearer(first)))
    assert (await client.get(ME, headers=bearer(second))).status_code == 200


@pytest.mark.parametrize("path", [LOGIN, LOGOUT, ME])
@pytest.mark.parametrize("chunked", [False, True])
async def test_auth_body_over_4096_bytes_rejected_before_authentication(
    auth, client, path, chunked
):
    body = b" " * 4097

    async def chunks() -> AsyncIterator[bytes]:
        for start in range(0, len(body), 701):
            yield body[start : start + 701]

    response = await client.request(
        "GET" if path == ME else "POST",
        path,
        content=chunks() if chunked else body,
        headers={"Content-Type": "application/json"},
    )
    if chunked:
        assert "content-length" not in response.request.headers
        assert response.request.headers["transfer-encoding"] == "chunked"
    else:
        assert response.request.headers["content-length"] == "4097"
    assert response.status_code == 413
    assert response.json() == {"detail": "Request too large"}
    assert auth.rows("SELECT username_digest FROM login_attempts") == []


@pytest.mark.parametrize("chunked", [False, True])
async def test_exactly_4096_bytes_is_allowed_through_to_login(auth, user, client, chunked):
    body = json.dumps({"username": USERNAME, "password": auth.passwords.password}).encode()
    body += b" " * (4096 - len(body))
    assert len(body) == 4096

    async def chunks() -> AsyncIterator[bytes]:
        yield body[:2048]
        yield body[2048:]

    response = await client.post(
        LOGIN,
        content=chunks() if chunked else body,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 200


@pytest.mark.parametrize("budget", ["username", "global"])
async def test_persisted_login_throttle_precedes_password_verification(
    auth, user, client, monkeypatch, budget
):
    limit = 5 if budget == "username" else 30
    real_verify = auth.passwords.security.verify
    checks = []

    def verify_after_budget(password_hash, password):
        # Observe SQLite at verification time, rather than merely checking final counters.
        attempts = auth.rows("SELECT attempts FROM login_attempts WHERE username_digest = ''")
        checks.append(attempts[0][0])
        assert attempts == [(len(checks),)]
        return real_verify(password_hash, password)

    verification = Mock(side_effect=verify_after_budget)
    monkeypatch.setattr(auth.passwords.security, "verify", verification)
    split = limit // 2

    async def attempt(active_client, index, active_app):
        # Include success and failure in the username budget; distinct absent users exhaust global.
        username = USERNAME if budget == "username" else f"generated-missing-{index}"
        password = auth.passwords.password if index % 2 == 0 else auth.passwords.replacement
        payload = {"username": username, "password": password}
        if budget == "global":
            # Distinct transport peers isolate the global budget from the caller's 10/min cap.
            async with asgi_client(active_app, source=(f"192.0.2.{index + 1}", 1234)) as caller:
                response = await caller.post(LOGIN, json=payload)
        else:
            response = await active_client.post(LOGIN, json=payload)
        if budget == "username" and index % 2 == 0:
            assert response.status_code == 200
        else:
            assert_denied(response)

    for index in range(split):
        await attempt(client, index, auth.app)
    # Recreate both app and store against the same file: no in-memory limiter can pass this.
    restarted_app = auth.new_app()
    async with asgi_client(restarted_app) as restarted:
        for index in range(split, limit):
            await attempt(restarted, index, restarted_app)
        assert verification.call_count == limit
        before = auth.rows("SELECT * FROM login_attempts ORDER BY username_digest")
        for _ in range(2):
            response = await restarted.post(
                LOGIN, json={"username": USERNAME, "password": auth.passwords.password}
            )
            assert response.status_code == 429
            assert response.json() == {"detail": "Login temporarily unavailable"}
            assert response.headers["retry-after"] == "60"
        assert verification.call_count == limit
        assert auth.rows("SELECT * FROM login_attempts ORDER BY username_digest") == before
        assert checks == list(range(1, limit + 1))
        if budget == "username":
            # Per-user exhaustion must not block a different username below the global limit.
            other = await restarted.post(
                LOGIN,
                json={"username": "generated-other-missing", "password": auth.passwords.password},
            )
            assert_denied(other)
            assert verification.call_count == limit + 1
        auth.now[0] += 59
        still_blocked = await restarted.post(
            LOGIN, json={"username": USERNAME, "password": auth.passwords.password}
        )
        assert still_blocked.status_code == 429
        assert still_blocked.headers["retry-after"] == "60"
        # Reset the observation spy, not the real password work, for the new persisted window.
        checks.clear()
        auth.now[0] += 1
        await login(restarted, auth)
        assert checks == [1]
        assert auth.rows("SELECT attempts FROM login_attempts WHERE username_digest = ''") == [(1,)]


@pytest.mark.parametrize(
    "source", [("192.0.2.10", 1234), None], ids=["peer-host", "missing-client"]
)
async def test_caller_cap_persists_ignores_spoofed_headers_and_preserves_global_budget(
    auth, user, monkeypatch, source
):
    host = "unknown" if source is None else source[0]
    caller_key = "caller:" + hashlib.sha256(host.encode("utf-8")).hexdigest()
    real_verify = auth.passwords.security.verify

    def verify_after_caller_budget(password_hash, password):
        # Both persisted budgets must be consumed before real Argon2 work starts.
        global_attempts = auth.rows(
            "SELECT attempts FROM login_attempts WHERE username_digest = ''"
        )
        caller_attempts = auth.rows(
            "SELECT attempts FROM login_attempts WHERE username_digest = ?", (caller_key,)
        )
        assert global_attempts == caller_attempts == [(verification.call_count,)]
        return real_verify(password_hash, password)

    verification = Mock(side_effect=verify_after_caller_budget)
    monkeypatch.setattr(auth.passwords.security, "verify", verification)
    async with asgi_client(auth.app, source=source) as caller:
        for index in range(10):
            spoofed_host = f"198.51.100.{index + 1}"
            response = await caller.post(
                LOGIN,
                json={
                    "username": f"generated-rotating-missing-{index}",
                    "password": auth.passwords.password,
                },
                headers={"Forwarded": f"for={spoofed_host}", "X-Forwarded-For": spoofed_host},
            )
            assert_denied(response)
    assert verification.call_count == 10
    assert auth.rows("SELECT attempts FROM login_attempts WHERE username_digest = ''") == [(10,)]
    assert auth.rows(
        "SELECT attempts FROM login_attempts WHERE username_digest = ?", (caller_key,)
    ) == [(10,)]
    before = auth.rows("SELECT * FROM login_attempts ORDER BY username_digest")

    # The cap survives a fresh app/store and, for an existing peer, a change of source port.
    restarted_app = auth.new_app()
    restarted_source = None if source is None else (source[0], source[1] + 1)
    async with asgi_client(restarted_app, source=restarted_source) as caller:
        for index in range(24):
            spoofed_host = f"198.51.100.{index + 20}"
            headers = [
                {},
                {"Forwarded": f"for={spoofed_host}"},
                {"X-Forwarded-For": spoofed_host},
                {"Forwarded": f"for={spoofed_host}", "X-Forwarded-For": spoofed_host},
            ][index % 4]
            response = await caller.post(
                LOGIN,
                json={
                    "username": f"generated-blocked-missing-{index}",
                    "password": auth.passwords.password,
                },
                headers=headers,
            )
            assert response.status_code == 429
            assert response.json() == {"detail": "Login temporarily unavailable"}
            assert response.headers["retry-after"] == "60"
        assert verification.call_count == 10
        assert auth.rows("SELECT * FROM login_attempts ORDER BY username_digest") == before

        # More than 30 total requests must not exhaust global when the caller denies 24 of them.
        monkeypatch.setattr(auth.passwords.security, "verify", Mock(wraps=real_verify))
        async with asgi_client(restarted_app, source=("192.0.2.11", 1234)) as other:
            token = await login(other, auth)
            response = await other.get(ME, headers=bearer(token))
            assert response.status_code == 200
            assert response.json() == expected_me()
        assert auth.rows("SELECT attempts FROM login_attempts WHERE username_digest = ''") == [
            (11,)
        ]
        assert auth.rows(
            "SELECT attempts FROM login_attempts WHERE username_digest = ?", (caller_key,)
        ) == [(10,)]

        auth.now[0] += 59
        response = await caller.post(
            LOGIN, json={"username": USERNAME, "password": auth.passwords.password}
        )
        assert response.status_code == 429
        assert response.headers["retry-after"] == "60"
        auth.now[0] += 1
        await login(caller, auth)
        assert auth.rows("SELECT attempts FROM login_attempts WHERE username_digest = ''") == [(1,)]
        assert auth.rows(
            "SELECT attempts FROM login_attempts WHERE username_digest = ?", (caller_key,)
        ) == [(1,)]


async def test_login_throttle_cleans_expired_sessions_even_for_invalid_credentials(
    auth, user, client
):
    token = await login(client, auth)
    assert len(auth.rows("SELECT token_digest FROM sessions")) == 1
    auth.now[0] += 3600
    assert_denied(await client.get(ME, headers=bearer(token)))
    # Session reads deny but do not clean up: the following login attempt must do the deletion.
    assert len(auth.rows("SELECT token_digest FROM sessions")) == 1
    response = await client.post(
        LOGIN,
        json={"username": "generated-cleanup-missing-user", "password": auth.passwords.password},
    )
    assert_denied(response)
    assert auth.rows("SELECT token_digest FROM sessions") == []
    await login(client, auth)
    assert len(auth.rows("SELECT token_digest FROM sessions")) == 1


async def test_only_newest_ten_active_sessions_per_user_are_retained(auth, user, client):
    tokens = []
    for index in range(11):
        if index and index % 5 == 0:
            auth.now[0] += 60
        tokens.append(await login(client, auth))
        assert len(
            auth.rows("SELECT token_digest FROM sessions WHERE user_id = ?", (user,))
        ) == min(index + 1, 10)
    assert len(set(tokens)) == 11
    assert auth.rows("SELECT MIN(expires_at) FROM sessions")[0][0] > auth.now[0]
    assert_denied(await client.get(ME, headers=bearer(tokens[0])))
    for token in tokens[1:]:
        response = await client.get(ME, headers=bearer(token))
        assert response.status_code == 200
        assert response.json() == expected_me()
    assert set(auth.rows("SELECT token_digest FROM sessions WHERE user_id = ?", (user,))) == {
        (hashlib.sha256(token.encode("ascii")).hexdigest(),) for token in tokens[1:]
    }
