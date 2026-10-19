"""Focused service contracts; API/SQLite integration is covered in separate suites."""

import secrets
import traceback
from dataclasses import dataclass
from unittest.mock import Mock, call

import pytest

from policypilot.app.core.security import PasswordSecurity, token_digest, username_digest
from policypilot.app.models.domain import Permission, Principal, SystemRole
from policypilot.app.services.authentication import (
    AuthenticationError,
    AuthenticationService,
    IssuedSession,
    LoginThrottled,
)
from policypilot.app.services.identity_store import Credential, IdentityStore, Session

TOKEN = "A" * 43
USERNAME = "Test.Employee"
INTERNAL_DETAIL = "private database /srv/identity.sqlite3; credential and grant details"


@dataclass
class AuthHarness:
    service: AuthenticationService
    store: Mock
    passwords: Mock
    clock: Mock
    credential: Credential
    password: str


@pytest.fixture
def auth():
    store = Mock(spec=IdentityStore)
    passwords = Mock(spec=PasswordSecurity)
    clock = Mock(return_value=1_800_000_000.75)
    principal = Principal("test-user")
    credential = Credential(principal, "stored-argon2id-record", True)
    store.consume_login_attempt.return_value = True
    store.get_credentials.return_value = credential
    store.create_session.return_value = True
    store.get_session.return_value = Session(principal.user_id, 1_800_000_600)
    store.get_user.return_value = principal
    passwords.verify.return_value = True
    service = AuthenticationService(
        store, passwords=passwords, clock=clock, session_ttl_seconds=600
    )
    return AuthHarness(service, store, passwords, clock, credential, secrets.token_urlsafe(24))


def assert_generic_failure(caught):
    error = caught.value
    assert type(error) is AuthenticationError
    assert error.args == ("Authentication required",)
    assert error.__cause__ is None
    assert error.__suppress_context__ is True
    displayed = "".join(traceback.format_exception(error))
    assert INTERNAL_DETAIL not in displayed
    assert "During handling of the above exception" not in displayed
    assert "The above exception was the direct cause" not in displayed


def test_login_consumes_budget_then_verifies_and_persists_only_digest(auth, monkeypatch):
    events = Mock()
    events.attach_mock(auth.clock, "clock")
    events.attach_mock(auth.store, "store")
    events.attach_mock(auth.passwords, "passwords")
    generate = Mock(return_value=TOKEN)
    events.attach_mock(generate, "new_token")
    monkeypatch.setattr("policypilot.app.services.authentication.new_token", generate)

    issued = auth.service.login(USERNAME, auth.password)

    assert issued.access_token == TOKEN
    assert issued.expires_in == 600
    assert events.mock_calls == [
        call.clock(),
        call.store.consume_login_attempt(
            username_digest(USERNAME), 1_800_000_000, caller_digest=username_digest("local")
        ),
        call.store.get_credentials(USERNAME),
        call.passwords.verify(auth.credential.password_hash, auth.password),
        call.new_token(),
        call.store.create_session(
            token_digest(TOKEN),
            auth.credential.principal.user_id,
            1_800_000_600,
            expected_password_hash=auth.credential.password_hash,
        ),
    ]
    assert TOKEN not in repr(auth.store.mock_calls)


def test_login_hashes_provided_caller_without_persisting_raw_identity(auth):
    caller_id = "server-derived-test-caller"

    issued = auth.service.login(USERNAME, auth.password, caller_id=caller_id)

    assert issued.expires_in == 600
    auth.store.consume_login_attempt.assert_called_once_with(
        username_digest(USERNAME), 1_800_000_000, caller_digest=username_digest(caller_id)
    )
    assert auth.store.consume_login_attempt.call_args.kwargs == {
        "caller_digest": username_digest(caller_id)
    }
    for persisted_call in auth.store.mock_calls:
        assert caller_id not in repr(persisted_call.args)
        assert caller_id not in repr(persisted_call.kwargs)


def test_issued_session_repr_hides_the_bearer_secret():
    issued = IssuedSession(TOKEN, 600)
    assert issued.access_token == TOKEN
    assert repr(issued) == "IssuedSession(expires_in=600)"
    assert TOKEN not in repr(issued)


@pytest.mark.parametrize("ttl", [None, True, False, 60.0, "600", 0, 59, 86401])
def test_invalid_ttl_is_rejected_without_store_or_password_work(auth, ttl):
    with pytest.raises(ValueError, match="^Invalid session lifetime$"):
        AuthenticationService(auth.store, passwords=auth.passwords, session_ttl_seconds=ttl)
    assert auth.store.mock_calls == []
    assert auth.passwords.mock_calls == []


@pytest.mark.parametrize("ttl", [60, 86400])
def test_ttl_boundaries_use_the_injected_clock(auth, ttl):
    service = AuthenticationService(
        auth.store, passwords=auth.passwords, session_ttl_seconds=ttl, clock=auth.clock
    )
    issued = service.login(USERNAME, auth.password)
    assert issued.expires_in == ttl
    assert auth.store.create_session.call_args.args[2] == 1_800_000_000 + ttl
    auth.clock.assert_called_once_with()


@pytest.mark.parametrize("failure", ["wrong-password", "missing-user", "disabled-user"])
def test_credential_denials_are_indistinguishable_and_never_create_sessions(auth, failure):
    expected_hash = auth.credential.password_hash
    if failure == "wrong-password":
        auth.passwords.verify.return_value = False
    elif failure == "missing-user":
        auth.store.get_credentials.return_value = None
        expected_hash = None
    else:
        auth.store.get_credentials.return_value = Credential(
            auth.credential.principal, auth.credential.password_hash, False
        )
    with pytest.raises(AuthenticationError) as caught:
        auth.service.login(USERNAME, auth.password)
    assert_generic_failure(caught)
    auth.store.consume_login_attempt.assert_called_once_with(
        username_digest(USERNAME), 1_800_000_000, caller_digest=username_digest("local")
    )
    auth.passwords.verify.assert_called_once_with(expected_hash, auth.password)
    auth.store.create_session.assert_not_called()


def test_stale_password_race_cannot_issue_a_session(auth):
    # The store's compare-and-create reports a reset/disable after verification.
    auth.store.create_session.return_value = False
    with pytest.raises(AuthenticationError) as caught:
        auth.service.login(USERNAME, auth.password)
    assert_generic_failure(caught)
    auth.passwords.verify.assert_called_once_with(auth.credential.password_hash, auth.password)
    assert auth.store.create_session.call_args.kwargs == {
        "expected_password_hash": auth.credential.password_hash
    }


def test_throttle_denies_before_credential_lookup_verification_or_token_generation(
    auth, monkeypatch
):
    auth.store.consume_login_attempt.return_value = False
    generate = Mock()
    monkeypatch.setattr("policypilot.app.services.authentication.new_token", generate)
    with pytest.raises(LoginThrottled) as caught:
        auth.service.login(USERNAME, auth.password)
    assert type(caught.value) is LoginThrottled
    assert caught.value.args == ("Login temporarily unavailable",)
    assert USERNAME not in str(caught.value)
    assert auth.password not in str(caught.value)
    assert auth.store.mock_calls == [
        call.consume_login_attempt(
            username_digest(USERNAME), 1_800_000_000, caller_digest=username_digest("local")
        )
    ]
    auth.passwords.verify.assert_not_called()
    generate.assert_not_called()


@pytest.mark.parametrize(
    ("operation", "method"),
    [
        ("login", "consume_login_attempt"),
        ("login", "get_credentials"),
        ("login", "create_session"),
        ("authenticate", "get_session"),
        ("authenticate", "get_user"),
        ("logout", "revoke_session"),
    ],
)
def test_every_store_failure_is_generic_without_displayed_exception_chaining(
    auth, operation, method
):
    getattr(auth.store, method).side_effect = RuntimeError(INTERNAL_DETAIL)
    with pytest.raises(AuthenticationError) as caught:
        if operation == "login":
            auth.service.login(USERNAME, auth.password)
        else:
            getattr(auth.service, operation)(TOKEN)
    assert_generic_failure(caught)
    if method in ("consume_login_attempt", "get_credentials"):
        auth.passwords.verify.assert_not_called()
        auth.store.create_session.assert_not_called()
    if method == "get_session":
        auth.store.get_user.assert_not_called()


def test_verifier_failure_is_generic_and_does_not_create_session(auth):
    auth.passwords.verify.side_effect = RuntimeError(INTERNAL_DETAIL)
    with pytest.raises(AuthenticationError) as caught:
        auth.service.login(USERNAME, auth.password)
    assert_generic_failure(caught)
    auth.store.create_session.assert_not_called()


def test_authenticate_resolves_current_user_and_clock_on_every_call(auth):
    original = auth.credential.principal
    updated = Principal(
        original.user_id,
        frozenset({Permission.HR}),
        frozenset({SystemRole.EVALUATOR}),
        permission_version=1,
    )
    auth.clock.side_effect = [1_800_000_001.9, 1_800_000_002.1, 1_800_000_003.8]
    auth.store.get_user.side_effect = [original, updated, None]
    assert auth.service.authenticate(TOKEN) is original
    assert auth.service.authenticate(TOKEN) is updated
    with pytest.raises(AuthenticationError) as caught:
        auth.service.authenticate(TOKEN)
    assert_generic_failure(caught)
    assert auth.store.get_session.call_args_list == [
        call(token_digest(TOKEN), now) for now in (1_800_000_001, 1_800_000_002, 1_800_000_003)
    ]
    assert auth.store.get_user.call_args_list == [call(original.user_id)] * 3
    auth.passwords.verify.assert_not_called()


def test_missing_session_denies_without_user_lookup(auth):
    auth.store.get_session.return_value = None
    with pytest.raises(AuthenticationError) as caught:
        auth.service.authenticate(TOKEN)
    assert_generic_failure(caught)
    auth.store.get_user.assert_not_called()


@pytest.mark.parametrize("current", [None, object(), Principal("different-user")])
def test_invalid_current_identity_denies_even_with_a_valid_session(auth, current):
    auth.store.get_user.return_value = current
    with pytest.raises(AuthenticationError) as caught:
        auth.service.authenticate(TOKEN)
    assert_generic_failure(caught)


@pytest.mark.parametrize("operation", ["authenticate", "logout"])
def test_malformed_session_denies_before_store_access(auth, operation):
    with pytest.raises(AuthenticationError) as caught:
        getattr(auth.service, operation)("not-a-session")
    assert_generic_failure(caught)
    assert auth.store.mock_calls == []


@pytest.mark.parametrize("operation", ["login", "authenticate"])
def test_injected_clock_failure_is_generic_before_store_access(auth, operation):
    auth.clock.side_effect = RuntimeError(INTERNAL_DETAIL)
    with pytest.raises(AuthenticationError) as caught:
        if operation == "login":
            auth.service.login(USERNAME, auth.password)
        else:
            auth.service.authenticate(TOKEN)
    assert_generic_failure(caught)
    assert auth.store.mock_calls == []


def test_logout_revokes_only_the_digest_without_clock_or_identity_lookup(auth):
    assert auth.service.logout(TOKEN) is None
    assert auth.service.logout(TOKEN) is None
    assert auth.store.mock_calls == [call.revoke_session(token_digest(TOKEN))] * 2
    auth.clock.assert_not_called()
    auth.passwords.verify.assert_not_called()
