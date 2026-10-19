"""Content permissions and feature roles are separate security domains."""

from dataclasses import dataclass, field
from enum import StrEnum


class Permission(StrEnum):
    PUBLIC = "Public"
    EMPLOYEE = "Employee"
    MANAGER = "Manager"
    HR = "HR"
    FINANCE = "Finance"


class SystemRole(StrEnum):
    ADMIN = "Admin"
    EVALUATOR = "Evaluator"


@dataclass(frozen=True)
class Principal:
    """Server-store identity, never constructed from client-supplied grant claims."""

    user_id: str
    assigned_permissions: frozenset[Permission] = field(default_factory=frozenset)
    system_roles: frozenset[SystemRole] = field(default_factory=frozenset)
    permission_version: int = 0

    def __post_init__(self) -> None:
        if not valid_principal(self):
            raise ValueError("Invalid principal")


def valid_principal(value: object) -> bool:
    """Revalidate at trust boundaries, including deserialized/forged instances."""
    if type(value) is not Principal:
        return False
    try:
        return (
            type(value.user_id) is str
            and bool(value.user_id.strip())
            and type(value.permission_version) is int
            and value.permission_version >= 0
            and type(value.assigned_permissions) is frozenset
            and all(type(label) is Permission for label in value.assigned_permissions)
            and type(value.system_roles) is frozenset
            and all(type(role) is SystemRole for role in value.system_roles)
        )
    except AttributeError:
        return False
