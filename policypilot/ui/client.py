"""Stateless, loopback-only dashboard adapter; never an authorization authority."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, cast
from urllib.parse import quote

import httpx

from policypilot.app.auth_api import MeResponse
from policypilot.app.core.security import token_digest
from policypilot.app.models.domain import Permission, SystemRole

ErrorKind = Literal["unauthorized", "forbidden", "throttled", "unavailable", "invalid"]
_MESSAGES: dict[ErrorKind, str] = {
    "unauthorized": "Authentication required.",
    "forbidden": "Access denied.",
    "throttled": "Too many attempts. Try again later.",
    "unavailable": "API temporarily unavailable.",
    "invalid": "Invalid API configuration, request, or response.",
}
_BASE_URL = re.compile(r"http://(?:127\.0\.0\.1|localhost|\[::1\])(?::([0-9]{1,5}))?/?", re.ASCII)
_USERNAME = re.compile(r"[A-Za-z0-9_.-]{1,64}", re.ASCII)
_PERMISSIONS = frozenset(permission.value for permission in Permission)
_ROLES = frozenset(role.value for role in SystemRole)


class DashboardError(Exception):
    """An allowlisted failure category and constant, display-safe message."""

    def __init__(self, kind: ErrorKind) -> None:
        self.kind = kind
        super().__init__(_MESSAGES[kind])


@dataclass(frozen=True)
class QueryCitation:
    chunk_id: str = field(repr=False)
    document_id: str = field(repr=False)
    title: str = field(repr=False)
    section: str = field(repr=False)
    source_url: str = field(repr=False)
    snippet: str = field(repr=False)


@dataclass(frozen=True)
class QueryAnswer:
    status: Literal["answered", "abstained"]
    answer: str = field(repr=False)
    citations: tuple[QueryCitation, ...] = field(repr=False)


@dataclass(frozen=True)
class SourceView:
    chunk_id: str = field(repr=False)
    document_id: str = field(repr=False)
    title: str = field(repr=False)
    section: str = field(repr=False)
    source_url: str = field(repr=False)
    text: str = field(repr=False)


@dataclass(frozen=True)
class AdminAccount:
    user_id: str = field(repr=False)
    username: str = field(repr=False)
    enabled: bool = field(repr=False)
    permissions: tuple[str, ...] = field(repr=False)
    system_roles: tuple[str, ...] = field(repr=False)
    permission_version: int = field(repr=False)


@dataclass(frozen=True)
class AdminOverview:
    accounts: tuple[AdminAccount, ...] = field(repr=False)
    documents: int = field(repr=False)
    chunks: int = field(repr=False)


@dataclass(frozen=True)
class PolicySummary:
    document_id: str = field(repr=False)
    title: str = field(repr=False)
    source_url: str = field(repr=False)
    version: str = field(repr=False)
    updated_at: datetime = field(repr=False)
    sections: tuple[str, ...] = field(repr=False)
    chunks: int = field(repr=False)
    visible_via: tuple[str, ...] = field(repr=False)


def _object(value: object, keys: set[str]) -> dict[str, object]:
    if type(value) is not dict or value.keys() != keys:
        raise DashboardError("invalid")
    return cast(dict[str, object], value)


def _labels(value: object, allowed: frozenset[str]) -> list[str]:
    if (
        type(value) is not list
        or not all(type(label) is str and label in allowed for label in value)
        or len(value) != len(set(value))
    ):
        raise DashboardError("invalid")
    return cast(list[str], value)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Invalid JSON")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise ValueError("Invalid JSON")


def _json(response: httpx.Response) -> object:
    if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != (
        "application/json"
    ):
        raise DashboardError("invalid")
    try:
        value: object = response.json(
            object_pairs_hook=_unique_object, parse_constant=_reject_constant
        )
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise DashboardError("invalid") from None


def _token(token: str) -> None:
    try:
        # Share the server's exact opaque-token syntax; never interpret token claims.
        token_digest(token)
    except ValueError:
        raise DashboardError("invalid") from None


class ApiClient:
    """Synchronous requests with no retained session, cookies, grants, or responses."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        match = _BASE_URL.fullmatch(base_url) if type(base_url) is str else None
        if match is None or (match[1] is not None and not 1 <= int(match[1]) <= 65535):
            raise DashboardError("invalid")
        self._base_url = base_url.removesuffix("/")
        self._transport = transport

    def _request(
        self,
        method: str,
        path: str,
        status: int,
        *,
        token: str | None = None,
        credentials: dict[str, str] | None = None,
    ) -> httpx.Response:
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        try:
            # A new client per operation prevents cookie/auth state crossing UI reruns.
            with httpx.Client(
                base_url=self._base_url,
                timeout=5,
                trust_env=False,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                response = client.request(method, path, headers=headers, json=credentials)
        except (httpx.HTTPError, OSError):
            raise DashboardError("unavailable") from None
        if response.status_code == 401:
            raise DashboardError("unauthorized")
        if response.status_code == 403:
            raise DashboardError("forbidden")
        if response.status_code == 429:
            raise DashboardError("throttled")
        if 500 <= response.status_code <= 599:
            raise DashboardError("unavailable")
        if response.status_code != status:
            raise DashboardError("invalid")
        return response

    def login(self, username: str, password: str) -> str:
        """Return a validated opaque token, without retaining credentials or the token."""
        if type(username) is not str or _USERNAME.fullmatch(username) is None:
            raise DashboardError("invalid")
        if type(password) is not str:
            raise DashboardError("invalid")
        try:
            valid_password = 1 <= len(password.encode("utf-8")) <= 1024
        except UnicodeError:
            raise DashboardError("invalid") from None
        if not valid_password:
            raise DashboardError("invalid")
        response = self._request(
            "POST",
            "/api/v1/auth/login",
            200,
            credentials={"username": username, "password": password},
        )
        payload = _object(_json(response), {"access_token", "token_type", "expires_in"})
        token = payload["access_token"]
        expiry = payload["expires_in"]
        if (
            type(token) is not str
            or type(payload["token_type"]) is not str
            or payload["token_type"] not in ("bearer",)
            or type(expiry) is not int
            or expiry <= 0
        ):
            raise DashboardError("invalid")
        _token(token)
        return token

    def me(self, token: str) -> MeResponse:
        """Read current server-reported identity; this display data is not a grant cache."""
        _token(token)
        response = self._request("GET", "/api/v1/me", 200, token=token)
        payload = _object(
            _json(response),
            {"user_id", "content_permissions", "system_roles", "permission_version"},
        )
        user_id = payload["user_id"]
        version = payload["permission_version"]
        if (
            type(user_id) is not str
            or not user_id.strip()
            or type(version) is not int
            or version < 0
        ):
            raise DashboardError("invalid")
        permissions = _labels(payload["content_permissions"], _PERMISSIONS)
        roles = _labels(payload["system_roles"], _ROLES)
        if not {"Public", "Employee"}.issubset(permissions):
            raise DashboardError("invalid")
        # The API models coerce input and ignore extras; validate externally before reuse.
        return MeResponse(
            user_id=user_id,
            content_permissions=[Permission(label) for label in permissions],
            system_roles=[SystemRole(role) for role in roles],
            permission_version=version,
        )

    def query(self, token: str, question: str) -> QueryAnswer:
        """Ask the authorized API; the server resolves permissions on every call."""
        _token(token)
        if (
            type(question) is not str
            or not 1 <= len(question) <= 2000
            or not question.strip()
            or "\x00" in question
            or "\x7f" in question
        ):
            raise DashboardError("invalid")
        response = self._request(
            "POST",
            "/api/v1/query",
            200,
            token=token,
            credentials={"question": question},
        )
        payload = _object(_json(response), {"status", "answer", "citations"})
        status = payload["status"]
        answer = payload["answer"]
        citations = payload["citations"]
        if (
            type(status) is not str
            or status not in ("answered", "abstained")
            or type(answer) is not str
            or not answer
            or type(citations) is not list
        ):
            raise DashboardError("invalid")
        parsed: list[QueryCitation] = []
        for item in citations:
            citation = _object(
                item,
                {"chunk_id", "document_id", "title", "section", "source_url", "snippet"},
            )
            values = [
                citation["chunk_id"],
                citation["document_id"],
                citation["title"],
                citation["section"],
                citation["source_url"],
                citation["snippet"],
            ]
            if any(type(value) is not str for value in values):
                raise DashboardError("invalid")
            chunk_id, document_id, title, section, source_url, snippet = (
                cast(str, value) for value in values
            )
            if not all((chunk_id, document_id, title, section, source_url)):
                raise DashboardError("invalid")
            parsed.append(
                QueryCitation(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    title=title,
                    section=section,
                    source_url=source_url,
                    snippet=snippet,
                )
            )
        if (status == "answered") != bool(parsed):
            raise DashboardError("invalid")
        return QueryAnswer(
            status=cast(Literal["answered", "abstained"], status),
            answer=answer,
            citations=tuple(parsed),
        )

    def policies(self, token: str) -> tuple[PolicySummary, ...]:
        """List authorized policy metadata; the API applies current permissions."""
        _token(token)
        response = self._request("GET", "/api/v1/policies", 200, token=token)
        payload = _object(_json(response), {"policies"})
        policies = payload["policies"]
        if type(policies) is not list or len(policies) > 1000:
            raise DashboardError("invalid")
        parsed: list[PolicySummary] = []
        for item in policies:
            policy = _object(
                item,
                {
                    "document_id",
                    "title",
                    "source_url",
                    "version",
                    "updated_at",
                    "sections",
                    "chunks",
                    "visible_via",
                },
            )
            document_id = policy["document_id"]
            title = policy["title"]
            source_url = policy["source_url"]
            version = policy["version"]
            updated_at = policy["updated_at"]
            sections = policy["sections"]
            chunks = policy["chunks"]
            if (
                type(document_id) is not str
                or not document_id.strip()
                or type(title) is not str
                or not title.strip()
                or type(source_url) is not str
                or not source_url.strip()
                or type(version) is not str
                or not version.strip()
                or type(updated_at) is not str
                or type(sections) is not list
                or not sections
                or any(type(section) is not str or not section.strip() for section in sections)
                or type(chunks) is not int
                or chunks < 1
            ):
                raise DashboardError("invalid")
            try:
                parsed_updated_at = datetime.fromisoformat(updated_at)
            except ValueError:
                raise DashboardError("invalid") from None
            if parsed_updated_at.tzinfo is None:
                raise DashboardError("invalid")
            visible_via = _labels(policy["visible_via"], _PERMISSIONS)
            if not visible_via:
                raise DashboardError("invalid")
            parsed.append(
                PolicySummary(
                    document_id=document_id,
                    title=title,
                    source_url=source_url,
                    version=version,
                    updated_at=parsed_updated_at,
                    sections=tuple(cast(list[str], sections)),
                    chunks=chunks,
                    visible_via=tuple(visible_via),
                )
            )
        if len({policy.document_id for policy in parsed}) != len(parsed):
            raise DashboardError("invalid")
        return tuple(parsed)

    def source(self, token: str, chunk_id: str) -> SourceView:
        """Read an authorized source; unauthorized and missing are indistinguishable."""
        _token(token)
        if (
            type(chunk_id) is not str
            or not 0 < len(chunk_id) <= 4096
            or not chunk_id.strip()
            or "\x00" in chunk_id
            or "\x7f" in chunk_id
        ):
            raise DashboardError("invalid")
        response = self._request(
            "GET", f"/api/v1/sources/{quote(chunk_id, safe='')}", 200, token=token
        )
        payload = _object(
            _json(response),
            {"chunk_id", "document_id", "title", "section", "source_url", "text"},
        )
        values = [
            payload["chunk_id"],
            payload["document_id"],
            payload["title"],
            payload["section"],
            payload["source_url"],
            payload["text"],
        ]
        if any(type(value) is not str for value in values):
            raise DashboardError("invalid")
        source_chunk_id, document_id, title, section, source_url, text = (
            cast(str, value) for value in values
        )
        if not all((source_chunk_id, document_id, title, section, source_url, text)):
            raise DashboardError("invalid")
        return SourceView(
            chunk_id=source_chunk_id,
            document_id=document_id,
            title=title,
            section=section,
            source_url=source_url,
            text=text,
        )

    def admin_overview(self, token: str) -> AdminOverview:
        """Read account metadata and corpus counts; Admin role is verified by the API."""
        _token(token)
        response = self._request("GET", "/api/v1/admin/overview", 200, token=token)
        payload = _object(_json(response), {"accounts", "corpus"})
        accounts = payload["accounts"]
        corpus = _object(payload["corpus"], {"documents", "chunks"})
        documents = corpus["documents"]
        chunks = corpus["chunks"]
        if (
            type(accounts) is not list
            or type(documents) is not int
            or documents < 0
            or type(chunks) is not int
            or chunks < 0
        ):
            raise DashboardError("invalid")
        parsed: list[AdminAccount] = []
        for item in accounts:
            account = _object(
                item,
                {
                    "user_id",
                    "username",
                    "enabled",
                    "permissions",
                    "system_roles",
                    "permission_version",
                },
            )
            user_id = account["user_id"]
            username = account["username"]
            enabled = account["enabled"]
            version = account["permission_version"]
            permissions = _labels(account["permissions"], _PERMISSIONS)
            roles = _labels(account["system_roles"], _ROLES)
            if (
                type(user_id) is not str
                or not user_id.strip()
                or type(username) is not str
                or not username.strip()
                or type(enabled) is not bool
                or type(version) is not int
                or version < 0
            ):
                raise DashboardError("invalid")
            parsed.append(
                AdminAccount(
                    user_id=user_id,
                    username=username,
                    enabled=enabled,
                    permissions=tuple(permissions),
                    system_roles=tuple(roles),
                    permission_version=version,
                )
            )
        return AdminOverview(tuple(parsed), documents, chunks)

    def logout(self, token: str) -> None:
        _token(token)
        response = self._request("POST", "/api/v1/auth/logout", 204, token=token)
        if response.content:
            raise DashboardError("invalid")

    def health(self) -> bool:
        """Liveness only: does not authenticate or assert store/retrieval/model readiness."""
        try:
            response = self._request("GET", "/api/v1/health", 200)
            payload = _object(_json(response), {"status"})
            return type(payload["status"]) is str and payload["status"] == "ok"
        except DashboardError:
            return False
