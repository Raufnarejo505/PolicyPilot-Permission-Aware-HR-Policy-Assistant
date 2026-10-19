"""Deny-by-default authorization, independent of retrieval and model behavior."""

from typing import Protocol

from policypilot.app.models.domain import Permission, Principal, SystemRole, valid_principal

_BASELINE = frozenset({Permission.PUBLIC, Permission.EMPLOYEE})
_DENIED = "Authorization denied"


class AuthorizationError(Exception):
    """Generic failure: never exposes store errors, grants, or identity existence."""


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
