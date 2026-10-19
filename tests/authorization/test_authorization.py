"""Authorization contract tests, independent of implementation internals.

Assumptions: role checks require SystemRole members (not strings), identity
matching is exact, and malformed/unsupported permission containers deny access.
No query, authentication, or login behavior is specified by this suite.
"""

from dataclasses import FrozenInstanceError, is_dataclass
from enum import Enum, StrEnum
from itertools import combinations
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from hypothesis import given
from hypothesis import strategies as st

from policypilot.app.models.domain import Permission, Principal, SystemRole
from policypilot.app.services.authorization import (
    AuthorizationError,
    AuthorizationService,
    can_read,
    effective_permissions,
    has_system_role,
)

PERMISSIONS = tuple(Permission)
ROLES = tuple(SystemRole)
BASELINE = frozenset({Permission.PUBLIC, Permission.EMPLOYEE})
USER_ID = "alice"


def subsets(values):
    return tuple(
        frozenset(subset)
        for size in range(len(values) + 1)
        for subset in combinations(values, size)
    )


PERMISSION_SUBSETS = subsets(PERMISSIONS)
NONEMPTY_PERMISSION_SUBSETS = PERMISSION_SUBSETS[1:]
ROLE_SUBSETS = subsets(ROLES)
CONTAINERS = (list, tuple, set, frozenset)


def subset_id(values):
    return "+".join(sorted(value.value for value in values)) or "none"


class LookalikePermission(StrEnum):
    HR = "HR"


class LookalikeRole(StrEnum):
    ADMIN = "Admin"


# Every invalid constructor field is also checked at the authorization boundary:
# a frozen dataclass alone is not a runtime trust boundary.
INVALID_FIELDS = (
    ("user_id", None),
    ("user_id", ""),
    ("user_id", " \t\n"),
    ("user_id", 1),
    ("user_id", True),
    ("user_id", b"alice"),
    ("user_id", []),
    ("user_id", {}),
    ("assigned_permissions", None),
    ("assigned_permissions", []),
    ("assigned_permissions", (Permission.HR,)),
    ("assigned_permissions", {Permission.HR}),
    ("assigned_permissions", {}),
    ("assigned_permissions", "HR"),
    ("assigned_permissions", Permission.HR),
    ("assigned_permissions", frozenset({"HR"})),
    ("assigned_permissions", frozenset({LookalikePermission.HR})),
    ("assigned_permissions", frozenset({SystemRole.ADMIN})),
    ("assigned_permissions", frozenset({Permission.HR, "Finance"})),
    ("assigned_permissions", frozenset({None})),
    ("system_roles", None),
    ("system_roles", []),
    ("system_roles", (SystemRole.ADMIN,)),
    ("system_roles", {SystemRole.ADMIN}),
    ("system_roles", {}),
    ("system_roles", "Admin"),
    ("system_roles", SystemRole.ADMIN),
    ("system_roles", frozenset({"Admin"})),
    ("system_roles", frozenset({LookalikeRole.ADMIN})),
    ("system_roles", frozenset({Permission.HR})),
    ("system_roles", frozenset({SystemRole.ADMIN, "Evaluator"})),
    ("system_roles", frozenset({None})),
    ("permission_version", -1),
    ("permission_version", True),
    ("permission_version", False),
    ("permission_version", None),
    ("permission_version", 0.0),
    ("permission_version", 1.5),
    ("permission_version", "0"),
    ("permission_version", []),
    ("permission_version", {}),
)


def principal_fields():
    return {
        "user_id": USER_ID,
        "assigned_permissions": frozenset({Permission.HR}),
        "system_roles": frozenset(ROLES),
        "permission_version": 1,
    }


def forged_principal(overrides=None, missing=None):
    principal = object.__new__(Principal)
    fields = principal_fields()
    fields.update(overrides or {})
    for name, value in fields.items():
        if name != missing:
            object.__setattr__(principal, name, value)
    return principal


def assert_invalid_principal(principal):
    with pytest.raises(AuthorizationError):
        effective_permissions(principal)
    for labels in ([Permission.PUBLIC], ["Employee"], list(PERMISSIONS)):
        assert can_read(principal, labels) is False
    for role in ROLES:
        assert has_system_role(principal, role) is False


INVALID_PRINCIPALS = (
    None,
    False,
    0,
    USER_ID,
    {},
    principal_fields(),
    SimpleNamespace(**principal_fields()),
    object(),
)
INVALID_LABELS = (
    "",
    "public",
    "PUBLIC",
    "employee",
    "hr",
    "Admin",
    "Evaluator",
    "Unknown",
    " Public",
    "Public ",
    "HR\n",
    None,
    True,
    0,
    b"Public",
    SystemRole.ADMIN,
    SystemRole.EVALUATOR,
    [],
    [Permission.PUBLIC],
    (Permission.PUBLIC,),
    {"label": "Public"},
)


def test_enum_contract():
    assert issubclass(Permission, str)
    assert issubclass(Permission, Enum)
    assert {name: member.value for name, member in Permission.__members__.items()} == {
        "PUBLIC": "Public",
        "EMPLOYEE": "Employee",
        "MANAGER": "Manager",
        "HR": "HR",
        "FINANCE": "Finance",
    }

    assert issubclass(SystemRole, Enum)
    assert {name: member.value for name, member in SystemRole.__members__.items()} == {
        "ADMIN": "Admin",
        "EVALUATOR": "Evaluator",
    }


def test_principal_defaults_and_frozen_dataclass_contract():
    principal = Principal(user_id=USER_ID)
    assert is_dataclass(principal)
    assert principal.user_id == USER_ID
    assert principal.assigned_permissions == frozenset()
    assert isinstance(principal.assigned_permissions, frozenset)
    assert principal.system_roles == frozenset()
    assert isinstance(principal.system_roles, frozenset)
    assert principal.permission_version == 0
    for field, value in principal_fields().items():
        with pytest.raises(FrozenInstanceError):
            setattr(principal, field, value)


@pytest.mark.parametrize(("field", "value"), INVALID_FIELDS)
def test_principal_constructor_rejects_invalid_fields(field, value):
    fields = principal_fields()
    fields[field] = value
    with pytest.raises(ValueError):
        Principal(**fields)


@pytest.mark.parametrize("principal", INVALID_PRINCIPALS)
def test_non_principals_fail_closed(principal):
    assert_invalid_principal(principal)


@pytest.mark.parametrize(("field", "value"), INVALID_FIELDS)
def test_forged_invalid_dataclass_fails_closed(field, value):
    assert_invalid_principal(forged_principal({field: value}))


def test_forged_dataclass_missing_required_identity_fails_closed():
    assert_invalid_principal(forged_principal(missing="user_id"))


@pytest.mark.parametrize(("field", "value"), INVALID_FIELDS)
def test_post_construction_corruption_fails_closed(field, value):
    principal = Principal(**principal_fields())
    object.__setattr__(principal, field, value)
    assert_invalid_principal(principal)


@pytest.mark.parametrize("assigned", PERMISSION_SUBSETS, ids=subset_id)
@pytest.mark.parametrize("roles", ROLE_SUBSETS, ids=subset_id)
def test_effective_permissions_are_exact_baseline_plus_assignments(assigned, roles):
    principal = Principal(USER_ID, assigned, roles)
    effective = effective_permissions(principal)
    assert isinstance(effective, frozenset)
    assert all(isinstance(permission, Permission) for permission in effective)
    assert effective == BASELINE | assigned
    assert principal.assigned_permissions == assigned
    assert principal.system_roles == roles


# 32 assigned-grant subsets x 31 nonempty policy subsets x all four role sets.
# Each case checks both enum labels and their exact wire-format strings.
@pytest.mark.parametrize("assigned", PERMISSION_SUBSETS, ids=subset_id)
@pytest.mark.parametrize("labels", NONEMPTY_PERMISSION_SUBSETS, ids=subset_id)
@pytest.mark.parametrize("roles", ROLE_SUBSETS, ids=subset_id)
def test_exhaustive_union_access_matrix_and_role_isolation(assigned, labels, roles):
    principal = Principal(USER_ID, assigned, roles)
    expected = bool((BASELINE | assigned) & labels)
    assert can_read(principal, list(labels)) is expected
    assert can_read(principal, [permission.value for permission in labels]) is expected


@pytest.mark.parametrize("container", CONTAINERS, ids=lambda value: value.__name__)
def test_supported_containers_accept_mixed_exact_labels(container):
    principal = Principal(USER_ID, frozenset({Permission.HR}))
    assert can_read(principal, container(["HR", Permission.FINANCE])) is True
    assert can_read(principal, container(["Finance", Permission.MANAGER])) is False
    assert can_read(principal, container([])) is False


@pytest.mark.parametrize("container", CONTAINERS, ids=lambda value: value.__name__)
def test_permission_container_subclasses_fail_closed(container):
    custom_container = type("CustomContainer", (container,), {})
    principal = Principal(USER_ID, frozenset(PERMISSIONS))
    assert can_read(principal, custom_container([Permission.PUBLIC])) is False


@pytest.mark.parametrize("label", INVALID_LABELS)
def test_any_invalid_label_denies_entire_decision(label):
    # Even an all-permissions principal must not bypass malformed metadata.
    principal = Principal(USER_ID, frozenset(PERMISSIONS), frozenset(ROLES))
    assert can_read(principal, [label]) is False
    assert can_read(principal, [Permission.PUBLIC, label]) is False
    assert can_read(principal, [label, "Public"]) is False


@pytest.mark.parametrize(
    "labels",
    [
        None,
        "Public",
        Permission.PUBLIC,
        b"Public",
        1,
        True,
        {"Public": True},
        {Permission.PUBLIC: None},
        object(),
    ],
)
def test_unsupported_permission_containers_deny(labels):
    assert can_read(Principal(USER_ID), labels) is False


def test_permission_iterators_and_generators_are_not_supported_containers():
    principal = Principal(USER_ID)
    assert can_read(principal, iter([Permission.PUBLIC])) is False
    assert can_read(principal, (label for label in ["Public"])) is False


@pytest.mark.parametrize("container", CONTAINERS, ids=lambda value: value.__name__)
@pytest.mark.parametrize("invalid_label", ["Unknown", "Public ", SystemRole.ADMIN, None])
def test_invalid_label_denies_in_every_supported_container(container, invalid_label):
    assert can_read(Principal(USER_ID), container(["Public", invalid_label])) is False


@pytest.mark.parametrize("roles", ROLE_SUBSETS, ids=subset_id)
def test_system_roles_are_explicit_and_never_inferred_from_content_grants(roles):
    principal = Principal(USER_ID, frozenset(PERMISSIONS), roles)
    for role in ROLES:
        assert has_system_role(principal, role) is (role in roles)


@pytest.mark.parametrize(
    "role",
    [None, "Admin", "Evaluator", "admin", Permission.HR, LookalikeRole.ADMIN, True, [], {}],
)
def test_invalid_role_arguments_fail_closed(role):
    principal = Principal(USER_ID, system_roles=frozenset(ROLES))
    assert has_system_role(principal, role) is False


@pytest.mark.parametrize("user_id", [None, "", " \n\t", 0, True, b"alice", [], {}])
def test_resolve_rejects_invalid_request_before_store_lookup(user_id):
    store = Mock(spec=["get_user"])
    service = AuthorizationService(store)
    with pytest.raises(AuthorizationError):
        service.resolve(user_id)
    store.get_user.assert_not_called()


@pytest.mark.parametrize("principal", INVALID_PRINCIPALS)
def test_resolve_rejects_missing_or_invalid_store_result(principal):
    store = Mock(spec=["get_user"])
    store.get_user.return_value = principal
    with pytest.raises(AuthorizationError):
        AuthorizationService(store).resolve(USER_ID)
    store.get_user.assert_called_once_with(USER_ID)


@pytest.mark.parametrize(("field", "value"), INVALID_FIELDS)
def test_resolve_runtime_validates_forged_store_results(field, value):
    store = Mock(spec=["get_user"])
    store.get_user.return_value = forged_principal({field: value})
    with pytest.raises(AuthorizationError):
        AuthorizationService(store).resolve(USER_ID)
    store.get_user.assert_called_once_with(USER_ID)


def test_resolve_rejects_store_principal_missing_required_identity():
    store = Mock(spec=["get_user"])
    store.get_user.return_value = forged_principal(missing="user_id")
    with pytest.raises(AuthorizationError):
        AuthorizationService(store).resolve(USER_ID)
    store.get_user.assert_called_once_with(USER_ID)


@pytest.mark.parametrize("returned_id", ["bob", "Alice", " alice "])
def test_resolve_rejects_identity_mismatch(returned_id):
    store = Mock(spec=["get_user"])
    store.get_user.return_value = Principal(returned_id)
    with pytest.raises(AuthorizationError):
        AuthorizationService(store).resolve(USER_ID)
    store.get_user.assert_called_once_with(USER_ID)


@pytest.mark.parametrize(
    "exception_type", [RuntimeError, ValueError, KeyError, OSError, AuthorizationError]
)
def test_store_failures_become_generic_authorization_errors(exception_type):
    internal_detail = "private-store-connection-credential-DO-NOT-EXPOSE"
    store = Mock(spec=["get_user"])
    store.get_user.side_effect = exception_type(internal_detail)
    with pytest.raises(AuthorizationError) as caught:
        AuthorizationService(store).resolve(USER_ID)
    assert internal_detail not in str(caught.value)
    store.get_user.assert_called_once_with(USER_ID)


def test_resolve_reloads_every_time_and_observes_revocation_and_deletion():
    granted = Principal(USER_ID, frozenset({Permission.HR}), frozenset(ROLES), 1)
    revoked = Principal(USER_ID, permission_version=2)
    restored = Principal(USER_ID, frozenset({Permission.FINANCE}), permission_version=3)
    store = Mock(spec=["get_user"])
    store.get_user.side_effect = [granted, revoked, None, restored]
    service = AuthorizationService(store)

    first = service.resolve(USER_ID)
    assert first == granted
    assert can_read(first, ["HR"]) is True
    assert has_system_role(first, SystemRole.ADMIN) is True

    second = service.resolve(USER_ID)
    assert second == revoked
    assert can_read(second, ["HR"]) is False
    assert has_system_role(second, SystemRole.ADMIN) is False
    assert can_read(second, ["Public"]) is True

    with pytest.raises(AuthorizationError):
        service.resolve(USER_ID)
    fourth = service.resolve(USER_ID)
    assert fourth == restored
    assert can_read(fourth, ["Finance"]) is True
    assert can_read(fourth, ["HR"]) is False
    assert store.get_user.call_args_list == [call(USER_ID)] * 4
    # A previously resolved immutable snapshot must not be silently rewritten.
    assert first == granted
    assert first.permission_version == 1


def test_resolve_does_not_cache_grants_by_permission_version():
    store = Mock(spec=["get_user"])
    store.get_user.side_effect = [
        Principal(USER_ID, frozenset({Permission.HR}), permission_version=7),
        Principal(USER_ID, permission_version=7),
    ]
    service = AuthorizationService(store)
    assert can_read(service.resolve(USER_ID), ["HR"]) is True
    assert can_read(service.resolve(USER_ID), ["HR"]) is False
    assert store.get_user.call_args_list == [call(USER_ID)] * 2


def test_resolve_revalidates_a_previously_valid_store_object_after_corruption():
    principal = Principal(USER_ID, frozenset({Permission.HR}))
    store = Mock(spec=["get_user"])
    store.get_user.return_value = principal
    service = AuthorizationService(store)
    assert can_read(service.resolve(USER_ID), ["HR"]) is True

    object.__setattr__(principal, "assigned_permissions", frozenset({"HR"}))
    with pytest.raises(AuthorizationError):
        service.resolve(USER_ID)
    assert can_read(principal, ["HR", "Public"]) is False
    assert store.get_user.call_args_list == [call(USER_ID)] * 2


def test_resolve_never_falls_back_to_cached_principal_after_store_failure():
    store = Mock(spec=["get_user"])
    store.get_user.side_effect = [
        Principal(USER_ID),
        RuntimeError("unavailable"),
        Principal(USER_ID),
    ]
    service = AuthorizationService(store)
    assert service.resolve(USER_ID) == Principal(USER_ID)
    with pytest.raises(AuthorizationError):
        service.resolve(USER_ID)
    assert service.resolve(USER_ID) == Principal(USER_ID)
    assert store.get_user.call_args_list == [call(USER_ID)] * 3


permission_sets = st.frozensets(st.sampled_from(PERMISSIONS))
role_sets = st.frozensets(st.sampled_from(ROLES))
versions = st.integers(min_value=0)
identities = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-", min_size=1, max_size=40)
valid_labels = st.one_of(
    st.sampled_from(PERMISSIONS),
    st.sampled_from(tuple(permission.value for permission in PERMISSIONS)),
)


@given(user_id=identities, assigned=permission_sets, roles=role_sets, version=versions)
def test_property_valid_principals_preserve_exact_fields_and_baseline(
    user_id, assigned, roles, version
):
    principal = Principal(user_id, assigned, roles, version)
    assert principal.user_id == user_id
    assert principal.assigned_permissions == assigned
    assert principal.system_roles == roles
    assert principal.permission_version == version
    assert effective_permissions(principal) == BASELINE | assigned


@given(
    assigned=permission_sets,
    roles=role_sets,
    labels=st.lists(valid_labels, max_size=20),
    container=st.sampled_from(CONTAINERS),
)
def test_property_read_decision_matches_union_for_every_container(
    assigned, roles, labels, container
):
    principal = Principal(USER_ID, assigned, roles)
    normalized = {Permission(label) for label in labels}
    expected = bool((BASELINE | assigned) & normalized)
    assert can_read(principal, container(labels)) is expected


@given(
    assigned=permission_sets,
    labels=st.lists(valid_labels, max_size=15),
    invalid=st.text().filter(lambda value: value not in {p.value for p in PERMISSIONS}),
    insertion=st.integers(min_value=0, max_value=15),
)
def test_property_invalid_string_poisoning_never_grants_access(
    assigned, labels, invalid, insertion
):
    principal = Principal(USER_ID, assigned, frozenset(ROLES))
    labels.insert(min(insertion, len(labels)), invalid)
    assert can_read(principal, labels) is False


@given(assigned=permission_sets, extra=permission_sets, labels=permission_sets)
def test_property_adding_valid_grants_or_policy_labels_cannot_revoke_access(
    assigned, extra, labels
):
    principal = Principal(USER_ID, assigned)
    expanded = Principal(USER_ID, assigned | extra)
    before = can_read(principal, list(labels))
    if before:
        assert can_read(expanded, list(labels)) is True
        assert can_read(principal, list(labels | extra)) is True


@given(assigned=permission_sets, roles=role_sets, labels=permission_sets)
def test_property_system_roles_do_not_change_content_access(assigned, roles, labels):
    without_roles = Principal(USER_ID, assigned)
    with_roles = Principal(USER_ID, assigned, roles)
    assert effective_permissions(with_roles) == effective_permissions(without_roles)
    assert can_read(with_roles, list(labels)) is can_read(without_roles, list(labels))


@given(
    user_id=identities,
    initial=permission_sets,
    replacement=permission_sets,
    initial_roles=role_sets,
    replacement_roles=role_sets,
    version=versions,
)
def test_property_resolve_reads_current_store_state_each_invocation(
    user_id, initial, replacement, initial_roles, replacement_roles, version
):
    first = Principal(user_id, initial, initial_roles, version)
    second = Principal(user_id, replacement, replacement_roles, version + 1)
    store = Mock(spec=["get_user"])
    store.get_user.side_effect = [first, second]
    service = AuthorizationService(store)
    assert service.resolve(user_id) == first
    current = service.resolve(user_id)
    assert current == second
    for permission in PERMISSIONS:
        assert can_read(current, [permission]) is (permission in BASELINE | replacement)
    for role in ROLES:
        assert has_system_role(current, role) is (role in replacement_roles)
    assert store.get_user.call_args_list == [call(user_id)] * 2
