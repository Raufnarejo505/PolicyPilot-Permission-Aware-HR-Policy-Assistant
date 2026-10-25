"""Dashboard HTTP boundary: synthetic data and in-process transports only."""

import asyncio
import json
import logging
import secrets
import traceback
from datetime import UTC, date, datetime

import httpx
import pytest

from policypilot.app.auth_api import MeResponse
from policypilot.app.core.config import Settings
from policypilot.app.core.security import PasswordSecurity
from policypilot.app.main import create_app
from policypilot.app.models.content import DocumentMetadata
from policypilot.app.models.domain import Permission, SystemRole
from policypilot.app.services.content_store import ContentStore
from policypilot.app.services.identity_store import IdentityStore
from policypilot.app.services.ingestion import IngestionService, SourceDocument
from policypilot.ui.client import ApiClient, DashboardError

TOKEN = "a" * 43  # Synthetic, syntactically valid opaque session; not a real credential.
PRIVATE = "synthetic-private-response-marker"
LOGIN = {"access_token": TOKEN, "token_type": "bearer", "expires_in": 3600}
ME = {
    "user_id": "synthetic-user",
    "content_permissions": ["Employee", "Public"],
    "system_roles": [],
    "permission_version": 0,
}
MESSAGES = {
    "unauthorized": "Authentication required.",
    "forbidden": "Access denied.",
    "throttled": "Too many attempts. Try again later.",
    "unavailable": "API temporarily unavailable.",
    "invalid": "Invalid API configuration, request, or response.",
}


def api_response(payload=ME, *, status=200, headers=None):
    return ApiClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, json=payload, headers=headers)
        )
    )


def operation(client, name):
    if name == "login":
        return client.login("synthetic-user", "synthetic-password")
    if name == "me":
        return client.me(TOKEN)
    if name == "logout":
        return client.logout(TOKEN)
    return client.health()


def assert_safe(error, kind, caplog=None):
    assert isinstance(error, DashboardError)
    assert error.kind == kind
    assert str(error) == MESSAGES[kind]
    assert error.args == (MESSAGES[kind],)
    rendered = "".join(traceback.format_exception(error))
    assert PRIVATE not in rendered
    assert TOKEN not in rendered
    if caplog is not None:
        assert PRIVATE not in caplog.text
        assert TOKEN not in caplog.text


def test_request_contract_and_no_cookie_or_authorization_carryover(monkeypatch):
    requests = []
    options = []
    real_client = httpx.Client
    monkeypatch.setenv("HTTP_PROXY", "http://unreachable.invalid:1234")
    monkeypatch.setenv("ALL_PROXY", "http://unreachable.invalid:1234")

    def handle(request):
        requests.append(request)
        path = request.url.path
        if path.endswith("login"):
            return httpx.Response(200, json=LOGIN, headers={"Set-Cookie": "session=untrusted"})
        if path.endswith("logout"):
            return httpx.Response(204, headers={"Set-Cookie": "session=untrusted"})
        return httpx.Response(200, json=ME if path.endswith("me") else {"status": "ok"})

    def client_factory(*args, **kwargs):
        options.append(kwargs)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", client_factory)
    transport = httpx.MockTransport(handle)
    client = ApiClient("http://localhost:8765/", transport=transport)
    assert client.login("synthetic-user", "synthetic-password") == TOKEN
    me = client.me(TOKEN)
    assert type(me) is MeResponse
    assert me.content_permissions == [Permission.EMPLOYEE, Permission.PUBLIC]
    assert me.system_roles == []
    assert client.logout(TOKEN) is None
    assert client.health() is True
    assert client.me(TOKEN) == me
    assert len(options) == 5
    assert all(option["timeout"] == 5 for option in options)
    assert all(option["trust_env"] is False for option in options)
    assert all(option["follow_redirects"] is False for option in options)
    assert all(option["transport"] is transport for option in options)
    assert [(r.method, r.url.path) for r in requests] == [
        ("POST", "/api/v1/auth/login"),
        ("GET", "/api/v1/me"),
        ("POST", "/api/v1/auth/logout"),
        ("GET", "/api/v1/health"),
        ("GET", "/api/v1/me"),
    ]
    assert json.loads(requests[0].content) == {
        "username": "synthetic-user",
        "password": "synthetic-password",  # pragma: allowlist secret
    }
    for index, request in enumerate(requests):
        assert request.url.host == "localhost"
        assert request.url.port == 8765
        assert not request.url.query
        assert "cookie" not in request.headers
        if index in (1, 2, 4):
            assert request.headers["authorization"] == f"Bearer {TOKEN}"
            assert request.content == b""
        else:
            assert "authorization" not in request.headers
        assert not any("role" in name or "permission" in name for name in request.headers)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000",
        "http://127.0.0.1/",
        "http://localhost",
        "http://localhost:1/",
        "http://[::1]:65535",
        "http://[::1]/",
    ],
)
def test_loopback_configuration(url):
    seen = []

    def handle(request):
        seen.append(request.url)
        return httpx.Response(200, json={"status": "ok"})

    assert ApiClient(url, transport=httpx.MockTransport(handle)).health()
    assert seen[0].path == "/api/v1/health"


@pytest.mark.parametrize(
    "url",
    [
        None,
        1,
        b"http://localhost",
        "",
        "https://localhost",
        "ftp://localhost",
        "http://example.com",
        "http://127.0.0.2",
        "http://127.1",
        "http://2130706433",
        "http://localhost.evil.invalid",
        "http://localhost.",
        "http://[::ffff:127.0.0.1]",
        "http://user:synthetic-private-response-marker@localhost",  # pragma: allowlist secret
        "http://@localhost",
        "http://localhost/api",
        "http://localhost//",
        "http://localhost?",
        "http://localhost#",
        "http://localhost/?x=1",
        "http://localhost/#x",
        "http://localhost:",
        "http://localhost:0",
        "http://localhost:65536",
        "http://localhost:-1",
        "http://localhost:abc",
        " http://localhost",
        "http://localhost\n",
        "http://local\thost",
        "http://localhost\\@example.com",
        "http://%6cocalhost",
        "http://[::1",
        "http://::1",
        "http://localhost:１２３",
    ],
)
def test_invalid_configuration_is_generic(url):
    with pytest.raises(DashboardError) as caught:
        ApiClient(url)
    assert_safe(caught.value, "invalid")


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "token",
        {},
        {**LOGIN, "extra": PRIVATE},
        {k: v for k, v in LOGIN.items() if k != "token_type"},
        {**LOGIN, "access_token": None},
        {**LOGIN, "access_token": 43},
        {**LOGIN, "access_token": "a" * 42},
        {**LOGIN, "access_token": "a" * 44},
        {**LOGIN, "access_token": "a" * 42 + "\n"},
        {**LOGIN, "access_token": "a" * 42 + "+"},
        {**LOGIN, "access_token": "a" * 42 + "é"},
        {**LOGIN, "token_type": "Bearer"},
        {**LOGIN, "token_type": 1},
        {**LOGIN, "expires_in": True},
        {**LOGIN, "expires_in": "3600"},
        {**LOGIN, "expires_in": 1.5},
        {**LOGIN, "expires_in": 0},
        {**LOGIN, "expires_in": -1},
    ],
)
def test_login_rejects_malformed_payload(payload):
    with pytest.raises(DashboardError) as caught:
        api_response(payload).login("synthetic-user", "synthetic-password")
    assert_safe(caught.value, "invalid")


@pytest.mark.parametrize(
    "username,password",
    [
        (None, "password"),
        (1, "password"),
        (True, "password"),
        ("", "password"),
        ("user name", "password"),
        ("é", "password"),
        ("u" * 65, "password"),
        ("user", None),
        ("user", b"password"),
        ("user", ""),
        ("user", "p" * 1025),
        ("user", "é" * 513),
        ("user", "\ud800"),
    ],
)
def test_bad_credentials_reject_before_transport(username, password):
    calls = []
    client = ApiClient(transport=httpx.MockTransport(lambda request: calls.append(request)))
    with pytest.raises(DashboardError) as caught:
        client.login(username, password)
    assert_safe(caught.value, "invalid")
    assert calls == []


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "identity",
        {},
        {**ME, "extra": PRIVATE},
        {k: v for k, v in ME.items() if k != "system_roles"},
        {**ME, "user_id": None},
        {**ME, "user_id": 1},
        {**ME, "user_id": ""},
        {**ME, "user_id": " \t"},
        {**ME, "permission_version": -1},
        {**ME, "permission_version": True},
        {**ME, "permission_version": "0"},
        {**ME, "permission_version": 0.0},
        {**ME, "permission_version": None},
        {**ME, "content_permissions": None},
        {**ME, "content_permissions": "Public"},
        *(
            {**ME, "content_permissions": p}
            for p in [
                [],
                ["Public"],
                ["Employee"],
                ["Employee", "Public", "Unknown"],
                ["Employee", "Public", "public"],
                ["Employee", "Public", "Admin"],
                ["Employee", "Public", 1],
                ["Employee", "Public", None],
                ["Employee", "Public", ["HR"]],
                ["Employee", "Public", {"name": "HR"}],
                ["Employee", "Public", "HR", "HR"],
                ["Employee", "Public", "Public"],
            ]
        ),
        *(
            {**ME, "system_roles": r}
            for r in [
                None,
                "Admin",
                ["Unknown"],
                ["admin"],
                ["HR"],
                [1],
                [True],
                [None],
                ["Admin", "Admin"],
                ["Admin", "Evaluator", "Finance"],
                [["Admin"]],
            ]
        ),
    ],
)
def test_me_rejects_malformed_identity_and_unknown_or_mixed_permissions(payload):
    with pytest.raises(DashboardError) as caught:
        api_response(payload).me(TOKEN)
    assert_safe(caught.value, "invalid")


def test_all_canonical_labels_roles_and_nonnegative_version_are_preserved():
    payload = {
        **ME,
        "content_permissions": [p.value for p in Permission],
        "system_roles": [r.value for r in SystemRole],
        "permission_version": 123,
    }
    result = api_response(payload).me(TOKEN)
    assert result.model_dump(mode="json") == payload


@pytest.mark.parametrize("method", ["me", "logout"])
@pytest.mark.parametrize(
    "token",
    [None, 1, True, b"a" * 43, "", "a" * 42, "a" * 44, "a" * 42 + "\n", "a" * 42 + "+", "é" * 43],
)
def test_invalid_bearer_never_reaches_transport(method, token):
    calls = []
    client = ApiClient(transport=httpx.MockTransport(lambda request: calls.append(request)))
    with pytest.raises(DashboardError) as caught:
        getattr(client, method)(token)
    assert_safe(caught.value, "invalid")
    assert calls == []


@pytest.mark.parametrize("method", ["login", "me", "logout", "health"])
@pytest.mark.parametrize(
    "status,kind",
    [
        (201, "invalid"),
        (204, "invalid"),
        (301, "invalid"),
        (302, "invalid"),
        (303, "invalid"),
        (307, "invalid"),
        (308, "invalid"),
        (400, "invalid"),
        (401, "unauthorized"),
        (403, "forbidden"),
        (404, "invalid"),
        (418, "invalid"),
        (422, "invalid"),
        (429, "throttled"),
        (500, "unavailable"),
        (503, "unavailable"),
        (599, "unavailable"),
    ],
)
def test_http_errors_and_redirects_fail_closed_without_following(method, status, kind, caplog):
    if method == "logout" and status == 204:
        return
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            status,
            json={"detail": PRIVATE},
            headers={"Location": f"http://example.invalid/{PRIVATE}"},
        )

    caplog.set_level(logging.DEBUG)
    client = ApiClient(transport=httpx.MockTransport(handle))
    if method == "health":
        assert client.health() is False
    else:
        with pytest.raises(DashboardError) as caught:
            operation(client, method)
        assert_safe(caught.value, kind, caplog)
    assert len(calls) == 1
    assert PRIVATE not in caplog.text
    assert TOKEN not in caplog.text


@pytest.mark.parametrize("method", ["login", "me", "logout", "health"])
@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError,
        httpx.ReadTimeout,
        httpx.WriteError,
        httpx.RemoteProtocolError,
        httpx.DecodingError,
        OSError,
    ],
)
def test_network_errors_have_no_raw_exception_chain_or_sensitive_logs(method, error, caplog):
    def handle(request):
        raise error(PRIVATE + TOKEN)

    caplog.set_level(logging.DEBUG)
    client = ApiClient(transport=httpx.MockTransport(handle))
    if method == "health":
        assert client.health() is False
    else:
        with pytest.raises(DashboardError) as caught:
            operation(client, method)
        assert_safe(caught.value, "unavailable", caplog)
    assert PRIVATE not in caplog.text
    assert TOKEN not in caplog.text


@pytest.mark.parametrize("method", ["login", "me", "health"])
@pytest.mark.parametrize(
    "content,content_type",
    [
        (b"not-json", "application/json"),
        (b"{}", "text/html"),
        (b"{}", ""),
        (b'{"status":"ok","status":"ok"}', "application/json"),
        (b'{"value":NaN}', "application/json"),
        (b'{"value":Infinity}', "application/json"),
        (b'{"value":-Infinity}', "application/json"),
        (b"\xff", "application/json"),
    ],
)
def test_invalid_json_and_content_types_fail_closed(method, content, content_type):
    client = ApiClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=content, headers={"Content-Type": content_type}
            )
        )
    )
    if method == "health":
        assert client.health() is False
    else:
        with pytest.raises(DashboardError) as caught:
            operation(client, method)
        assert_safe(caught.value, "invalid")


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"status": True},
        {"status": "ready"},
        {"status": "ok", "identity_store": "ready"},
    ],
)
def test_health_is_strict_liveness_only(payload):
    assert api_response(payload).health() is False


def test_logout_requires_empty_204():
    for status, content in [(200, b""), (204, b"unexpected")]:
        client = ApiClient(
            transport=httpx.MockTransport(
                lambda request, status=status, content=content: httpx.Response(
                    status, content=content
                )
            )
        )
        with pytest.raises(DashboardError) as caught:
            client.logout(TOKEN)
        assert_safe(caught.value, "invalid")


def test_fresh_me_requests_do_not_cache_identity():
    responses = iter(
        [ME, {**ME, "content_permissions": ["Public", "Employee", "HR"], "permission_version": 1}]
    )
    client = ApiClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=next(responses)))
    )
    assert client.me(TOKEN).permission_version == 0
    assert client.me(TOKEN).permission_version == 1


def test_http_client_is_closed_after_every_operation(monkeypatch):
    clients = []
    real_client = httpx.Client

    def client_factory(*args, **kwargs):
        client = real_client(*args, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(httpx, "Client", client_factory)
    assert api_response({"status": "ok"}).health()
    assert api_response(status=503).health() is False
    assert all(client.is_closed for client in clients)


def test_real_fastapi_login_me_permission_refresh_logout_and_liveness(tmp_path):
    path = tmp_path / "identity.sqlite3"
    store = IdentityStore(path)
    store.initialize()
    password = "synthetic-ui-" + secrets.token_urlsafe(24)  # pragma: allowlist secret
    store.create_user(
        user_id="synthetic-user",
        username="synthetic-user",
        password_hash=PasswordSecurity().hash(password),
    )
    content_path = tmp_path / "content.sqlite3"
    content = ContentStore(content_path)
    content.initialize()
    content.replace(
        IngestionService().prepare(
            SourceDocument(
                DocumentMetadata(
                    "doc-1",
                    "Annual Leave Policy",
                    "https://example.invalid/leave",
                    date(2026, 10, 25),
                    "Synthetic fixture; CC BY 4.0.",
                    "v1",
                    datetime(2026, 10, 25, tzinfo=UTC),
                    frozenset({Permission.PUBLIC}),
                ),
                b"Annual leave policy: full-time employees receive 26 days.",
            )
        )
    )
    app = create_app(
        Settings(
            environment="test",
            identity_database=path,
            content_database=content_path,
            _env_file=None,
        )
    )

    class InProcessTransport(httpx.BaseTransport):
        def handle_request(self, request):
            async def send():
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), trust_env=False
                ) as server:
                    response = await server.request(
                        request.method,
                        str(request.url),
                        headers=request.headers,
                        content=request.content,
                    )
                    return httpx.Response(
                        response.status_code, headers=response.headers, content=response.content
                    )

            return asyncio.run(send())

    def roundtrip():
        client = ApiClient(transport=InProcessTransport())
        assert client.health()
        token = client.login("synthetic-user", password)
        assert len(token) == 43
        first = client.me(token)
        assert first.user_id == "synthetic-user"
        assert set(first.content_permissions) == {Permission.PUBLIC, Permission.EMPLOYEE}
        with pytest.raises(DashboardError) as forbidden:
            client.admin_overview(token)
        assert_safe(forbidden.value, "forbidden")
        store.set_access(
            "synthetic-user",
            assigned_permissions=frozenset({Permission.HR}),
            system_roles=frozenset({SystemRole.ADMIN}),
        )
        second = client.me(token)
        assert set(second.content_permissions) == {
            Permission.PUBLIC,
            Permission.EMPLOYEE,
            Permission.HR,
        }
        assert second.system_roles == [SystemRole.ADMIN]
        assert second.permission_version > first.permission_version
        overview = client.admin_overview(token)
        assert overview.documents == 1
        assert overview.chunks == 1
        assert any(
            account.username == "synthetic-user" and account.system_roles == ("Admin",)
            for account in overview.accounts
        )
        answer = client.query(token, "annual leave")
        assert answer.status == "answered"
        assert answer.citations
        source = client.source(token, answer.citations[0].chunk_id)
        assert "26 days" in source.text
        client.logout(token)
        client.logout(token)
        with pytest.raises(DashboardError) as caught:
            client.me(token)
        assert_safe(caught.value, "unauthorized")
        with pytest.raises(DashboardError) as query_error:
            client.query(token, "annual leave")
        assert_safe(query_error.value, "unauthorized")
        assert client.health()

    roundtrip()


QUERY = {
    "status": "answered",
    "answer": "Annual leave is 26 days. [1]",
    "citations": [
        {
            "chunk_id": "chunk-1",
            "document_id": "doc-1",
            "title": "Annual Leave Policy",
            "section": "Eligibility",
            "source_url": "https://example.invalid/leave",
            "snippet": "Full-time employees receive 26 days.",
        }
    ],
}
SOURCE = {
    "chunk_id": "chunk-1",
    "document_id": "doc-1",
    "title": "Annual Leave Policy",
    "section": "Eligibility",
    "source_url": "https://example.invalid/leave",
    "text": "Full authorized source text.",
}


def query_payload(**changes):
    payload = json.loads(json.dumps(QUERY))
    payload.update(changes)
    return payload


def test_query_contract_is_strict_and_secret_free():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=QUERY)

    client = ApiClient(transport=httpx.MockTransport(handle))
    result = client.query(TOKEN, "How much leave?")
    assert result.status == "answered"
    assert result.answer.startswith("Annual leave")
    assert result.citations[0].title == "Annual Leave Policy"
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/api/v1/query"
    assert json.loads(requests[0].content) == {"question": "How much leave?"}
    assert requests[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in repr(result)
    assert "Full-time employees" not in repr(result)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"status": "answered"},
        query_payload(status="ready"),
        query_payload(status=True),
        query_payload(answer=""),
        query_payload(answer=1),
        query_payload(citations={}),
        query_payload(citations=[]),
        query_payload(status="abstained"),
        query_payload(citations=[{**QUERY["citations"][0], "extra": "unexpected"}]),
        query_payload(citations=[{"chunk_id": "chunk-1"}]),
        query_payload(citations=[{**QUERY["citations"][0], "snippet": 1}]),
        query_payload(citations=[{**QUERY["citations"][0], "chunk_id": ""}]),
    ],
)
def test_query_invalid_shapes_fail_closed(payload):
    client = api_response(payload)
    with pytest.raises(DashboardError) as caught:
        client.query(TOKEN, "leave")
    assert_safe(caught.value, "invalid")


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "unauthorized"), (403, "forbidden"), (429, "throttled"), (503, "unavailable")],
)
def test_query_http_failures_are_allowlisted(status, kind):
    client = api_response(QUERY, status=status)
    with pytest.raises(DashboardError) as caught:
        client.query(TOKEN, "leave")
    assert_safe(caught.value, kind)


@pytest.mark.parametrize("question", ["", " ", "x" * 2001, None, 1, "bad\x00", "bad\x7f"])
def test_query_rejects_invalid_local_input(question):
    client = api_response(QUERY)
    with pytest.raises(DashboardError) as caught:
        client.query(TOKEN, question)
    assert_safe(caught.value, "invalid")


@pytest.mark.parametrize("token", ["", "short", "!" * 43, "a" * 42, "a" * 44, None, 1])
def test_query_rejects_invalid_token_syntax(token):
    client = api_response(QUERY)
    with pytest.raises(DashboardError) as caught:
        client.query(token, "leave")
    assert_safe(caught.value, "invalid")


def test_query_is_not_cached_between_calls():
    responses = iter(
        [
            query_payload(answer="First answer [1]"),
            query_payload(answer="Second answer [1]"),
        ]
    )
    client = ApiClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=next(responses)))
    )
    assert client.query(TOKEN, "leave").answer == "First answer [1]"
    assert client.query(TOKEN, "leave").answer == "Second answer [1]"


def test_source_contract_and_failures():
    request = None

    def handle(request_value):
        nonlocal request
        request = request_value
        return httpx.Response(200, json=SOURCE)

    client = ApiClient(transport=httpx.MockTransport(handle))
    source = client.source(TOKEN, "chunk-1")
    assert source.text == "Full authorized source text."
    assert request.method == "GET"
    assert request.url.path == "/api/v1/sources/chunk-1"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in repr(source)
    assert "Full authorized" not in repr(source)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {**SOURCE, "extra": "unexpected"},
        {**SOURCE, "text": ""},
        {**SOURCE, "text": 1},
        {**SOURCE, "chunk_id": ""},
    ],
)
def test_source_invalid_shapes_fail_closed(payload):
    client = api_response(payload)
    with pytest.raises(DashboardError) as caught:
        client.source(TOKEN, "chunk-1")
    assert_safe(caught.value, "invalid")


@pytest.mark.parametrize("chunk_id", ["", " ", "x" * 4097, None, 1, "bad\x00", "bad\x7f"])
def test_source_rejects_invalid_identifier(chunk_id):
    client = api_response(SOURCE)
    with pytest.raises(DashboardError) as caught:
        client.source(TOKEN, chunk_id)
    assert_safe(caught.value, "invalid")


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "unauthorized"), (403, "forbidden"), (404, "invalid"), (503, "unavailable")],
)
def test_source_http_failures_are_allowlisted(status, kind):
    client = api_response(SOURCE, status=status)
    with pytest.raises(DashboardError) as caught:
        client.source(TOKEN, "chunk-1")
    assert_safe(caught.value, kind)


ADMIN_OVERVIEW = {
    "accounts": [
        {
            "user_id": "admin",
            "username": "admin",
            "enabled": True,
            "permissions": [],
            "system_roles": ["Admin"],
            "permission_version": 2,
        },
        {
            "user_id": "alice",
            "username": "alice",
            "enabled": True,
            "permissions": ["HR"],
            "system_roles": [],
            "permission_version": 1,
        },
    ],
    "corpus": {"documents": 18, "chunks": 656},
}


def test_admin_overview_contract_and_secret_free_repr():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=ADMIN_OVERVIEW)

    client = ApiClient(transport=httpx.MockTransport(handle))
    overview = client.admin_overview(TOKEN)
    assert overview.documents == 18
    assert overview.chunks == 656
    assert overview.accounts[0].username == "admin"
    assert overview.accounts[0].system_roles == ("Admin",)
    assert overview.accounts[1].permissions == ("HR",)
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/api/v1/admin/overview"
    assert requests[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in repr(overview)
    assert "alice" not in repr(overview)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {**ADMIN_OVERVIEW, "accounts": {}},
        {"accounts": [], "corpus": {"documents": 0}},
        {"accounts": [], "corpus": {"documents": -1, "chunks": 0}},
        {"accounts": [], "corpus": {"documents": 0, "chunks": True}},
        {"accounts": [{}], "corpus": {"documents": 0, "chunks": 0}},
        {
            "accounts": [{**ADMIN_OVERVIEW["accounts"][0], "extra": 1}],
            "corpus": {"documents": 0, "chunks": 0},
        },
        {
            "accounts": [{**ADMIN_OVERVIEW["accounts"][0], "enabled": "yes"}],
            "corpus": {"documents": 0, "chunks": 0},
        },
        {
            "accounts": [{**ADMIN_OVERVIEW["accounts"][0], "permission_version": -1}],
            "corpus": {"documents": 0, "chunks": 0},
        },
        {
            "accounts": [{**ADMIN_OVERVIEW["accounts"][0], "permissions": ["Unknown"]}],
            "corpus": {"documents": 0, "chunks": 0},
        },
        {
            "accounts": [{**ADMIN_OVERVIEW["accounts"][0], "system_roles": ["Unknown"]}],
            "corpus": {"documents": 0, "chunks": 0},
        },
        {
            "accounts": [{**ADMIN_OVERVIEW["accounts"][0], "username": ""}],
            "corpus": {"documents": 0, "chunks": 0},
        },
    ],
)
def test_admin_overview_invalid_shapes_fail_closed(payload):
    client = api_response(payload)
    with pytest.raises(DashboardError) as caught:
        client.admin_overview(TOKEN)
    assert_safe(caught.value, "invalid")


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "unauthorized"), (403, "forbidden"), (429, "throttled"), (503, "unavailable")],
)
def test_admin_overview_http_failures_are_allowlisted(status, kind):
    client = api_response(ADMIN_OVERVIEW, status=status)
    with pytest.raises(DashboardError) as caught:
        client.admin_overview(TOKEN)
    assert_safe(caught.value, kind)


POLICIES = {
    "policies": [
        {
            "document_id": "doc-leave",
            "title": "Annual Leave Policy",
            "source_url": "https://example.invalid/leave",
            "version": "v1",
            "updated_at": "2026-10-25T00:00:00+00:00",
            "sections": ["Eligibility", "Carry-over"],
            "chunks": 12,
            "visible_via": ["Public", "Employee"],
        }
    ]
}


def test_policy_contract_and_secret_free_repr():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=POLICIES)

    client = ApiClient(transport=httpx.MockTransport(handle))
    policies = client.policies(TOKEN)
    assert policies[0].title == "Annual Leave Policy"
    assert policies[0].visible_via == ("Public", "Employee")
    assert policies[0].sections == ("Eligibility", "Carry-over")
    assert policies[0].updated_at.tzinfo is not None
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/api/v1/policies"
    assert requests[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in repr(policies)
    assert "Annual Leave" not in repr(policies)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {**POLICIES, "extra": 1},
        {"policies": {}},
        {"policies": [POLICIES["policies"][0]] * 1001},
        {"policies": [{**POLICIES["policies"][0], "extra": 1}]},
        {"policies": [{**POLICIES["policies"][0], "updated_at": "2026-10-25T00:00:00"}]},
        {"policies": [{**POLICIES["policies"][0], "sections": []}]},
        {"policies": [{**POLICIES["policies"][0], "sections": [""]}]},
        {"policies": [{**POLICIES["policies"][0], "chunks": 0}]},
        {"policies": [{**POLICIES["policies"][0], "visible_via": []}]},
        {"policies": [{**POLICIES["policies"][0], "visible_via": ["Unknown"]}]},
        {"policies": [{**POLICIES["policies"][0], "title": ""}]},
        {"policies": [{**POLICIES["policies"][0], "document_id": ""}]},
        {"policies": [POLICIES["policies"][0], POLICIES["policies"][0]]},
    ],
)
def test_policy_invalid_shapes_fail_closed(payload):
    client = api_response(payload)
    with pytest.raises(DashboardError) as caught:
        client.policies(TOKEN)
    assert_safe(caught.value, "invalid")


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "unauthorized"), (403, "forbidden"), (429, "throttled"), (503, "unavailable")],
)
def test_policy_http_failures_are_allowlisted(status, kind):
    client = api_response(POLICIES, status=status)
    with pytest.raises(DashboardError) as caught:
        client.policies(TOKEN)
    assert_safe(caught.value, kind)
