"""Deny-by-default authorization, independent of retrieval and model behavior."""

from dataclasses import dataclass, field
from typing import Protocol

from policypilot.app.models.content import Chunk, valid_chunk
from policypilot.app.models.domain import Permission, Principal, SystemRole, valid_principal

_BASELINE = frozenset({Permission.PUBLIC, Permission.EMPLOYEE})
_DENIED = "Authorization denied"
_BUNDLE_TOKEN = object()


class AuthorizationError(Exception):
    """Generic failure: never exposes store errors, grants, or identity existence."""


@dataclass(frozen=True, init=False)
class AuthorizedEvidenceBundle:
    """Point-in-time evidence for internal downstream consumers, never a cache grant.

    Only AuthorizationService issues bundles. Python's private construction token is
    an API misuse guard, not protection against hostile code running in this process.
    Future requests, citations, and source reads must authorize again; this value
    proves neither index freshness nor that the supplied user ID was authenticated.
    """

    principal: Principal = field(repr=False)
    chunks: tuple[Chunk, ...] = field(repr=False)

    def __init__(self, token: object, principal: Principal, chunks: tuple[Chunk, ...]) -> None:
        if token is not _BUNDLE_TOKEN:
            raise AuthorizationError(_DENIED)
        object.__setattr__(self, "principal", principal)
        object.__setattr__(self, "chunks", chunks)


class UserStore(Protocol):
    def get_user(self, user_id: str) -> Principal | None:
        """Return current server-side state, not cached session claims."""
        ...


def effective_permissions(principal: Principal | None) -> frozenset[Permission]:
    if not valid_principal(principal) or principal is None:
        raise AuthorizationError(_DENIED)
    return _BASELINE | principal.assigned_permissions


def can_read(principal: Principal | None, allowed_permissions: object) -> bool:
    if not isinstance(allowed_permissions, (list, tuple, set, frozenset)):
        return False
    if type(allowed_permissions) not in (list, tuple, set, frozenset):
        return False
    # Reject mixed valid/invalid metadata rather than retaining only valid labels.
    if not allowed_permissions:
        return False
    try:
        labels: set[Permission] = set()
        for label in allowed_permissions:
            if type(label) not in (str, Permission):
                return False
            labels.add(Permission(label))
        return bool(effective_permissions(principal) & labels)
    except (AuthorizationError, TypeError, ValueError):
        return False


def has_system_role(principal: Principal | None, role: SystemRole) -> bool:
    return (
        valid_principal(principal)
        and principal is not None
        and type(role) is SystemRole
        and role in principal.system_roles
    )


class AuthorizationService:
    def __init__(self, store: UserStore) -> None:
        self._store = store

    def authorize(self, user_id: str, candidates: tuple[Chunk, ...]) -> AuthorizedEvidenceBundle:
        """Recheck every candidate before reranking/model input, including bad index hits.

        Callers must authenticate first and prefilter search by current grants. This
        independent check does not replace either step. Invalid/denied candidates
        are omitted without diagnostics or counts; no evidence remains an empty tuple.
        """
        principal = self.resolve(user_id)
        if type(candidates) is not tuple:
            raise AuthorizationError(_DENIED)
        permitted = tuple(
            candidate
            for candidate in candidates
            if valid_chunk(candidate) and can_read(principal, candidate.allowed_permissions)
        )
        return AuthorizedEvidenceBundle(_BUNDLE_TOKEN, principal, permitted)

    def resolve(self, user_id: str) -> Principal:
        if type(user_id) is not str or not user_id.strip():
            raise AuthorizationError(_DENIED)
        try:
            principal = self._store.get_user(user_id)
            if not valid_principal(principal) or principal is None or principal.user_id != user_id:
                raise AuthorizationError(_DENIED)
            return principal
        except Exception:
            # Store failures and unknown identities must expose the same safe failure.
            raise AuthorizationError(_DENIED) from None
