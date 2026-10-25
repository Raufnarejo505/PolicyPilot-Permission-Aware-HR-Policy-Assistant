"""Real SQLite persistence, deny-by-default decoding, and atomic session lifecycle."""

import hashlib
import os
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, TypedDict

import pytest

from policypilot.app.models.domain import Permission, Principal, SystemRole
from policypilot.app.services.authorization import AuthorizationService, effective_permissions
from policypilot.app.services.identity_store import Credential, IdentityStore, Session

HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHRzYWx0c2FsdA$"
    "aGFzaGhhc2hoYXNoaGFzaGhhc2hoYXNoaGFzaGhhc2g"
)
NEW_HASH = HASH + "A"
DIGEST = hashlib.sha256(b"opaque-example-session").hexdigest()
OTHER_DIGEST = hashlib.sha256(b"another-example-session").hexdigest()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "identities.sqlite3"


@pytest.fixture
def store(db_path: Path) -> IdentityStore:
    result = IdentityStore(db_path)
    result.initialize()
    return result


def add_user(store: IdentityStore, user_id: str = "u1", username: str = "Alice") -> None:
    store.create_user(user_id=user_id, username=username, password_hash=HASH)


def execute(db_path: Path, sql: str, parameters: tuple[object, ...] = ()) -> None:
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(sql, parameters)


def test_explicit_initialization_and_no_default_users(db_path: Path) -> None:
    store = IdentityStore(db_path)
    assert not db_path.exists()
    with pytest.raises(sqlite3.OperationalError):
        store.get_user("u1")
    assert not db_path.exists()
    store.initialize()
    store.initialize()
    assert store.get_user("u1") is None
    assert store.get_credentials("Alice") is None
    assert store.get_session(DIGEST, 0) is None
    with closing(sqlite3.connect(db_path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM users").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM sessions").fetchone() == (0,)


def test_missing_parent_is_not_created(tmp_path: Path) -> None:
    parent = tmp_path / "absent"
    with pytest.raises(FileNotFoundError):
        IdentityStore(parent / "identity.db").initialize()
    assert not parent.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permissions")
def test_new_database_is_private_even_with_permissive_umask(db_path: Path) -> None:
    previous_umask = os.umask(0)
    try:
        IdentityStore(db_path).initialize()
    finally:
        os.umask(previous_umask)
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permissions")
def test_existing_database_permissions_and_contents_are_preserved(db_path: Path) -> None:
    execute(db_path, "CREATE TABLE existing_data (value TEXT)")
    execute(db_path, "INSERT INTO existing_data VALUES (?)", ("keep this data",))
    db_path.chmod(0o640)
    store = IdentityStore(db_path)
    store.initialize()
    store.initialize()
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o640
    with closing(sqlite3.connect(db_path)) as connection:
        assert connection.execute("SELECT * FROM existing_data").fetchall() == [("keep this data",)]
    assert store.get_user("u1") is None


def test_existing_non_database_file_is_not_truncated(db_path: Path) -> None:
    contents = b"existing user-owned file, not a SQLite database"
    db_path.write_bytes(contents)
    with pytest.raises(sqlite3.DatabaseError):
        IdentityStore(db_path).initialize()
    assert db_path.read_bytes() == contents


def test_new_file_may_remain_empty_after_initialization_failure(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_connection(*args: object, **kwargs: object) -> sqlite3.Connection:
        raise sqlite3.OperationalError("forced connection failure")

    monkeypatch.setattr("policypilot.app.services.identity_store.sqlite3.connect", fail_connection)
    with pytest.raises(sqlite3.OperationalError, match="forced connection failure"):
        IdentityStore(db_path).initialize()
    assert db_path.read_bytes() == b""
    if os.name == "posix":
        assert stat.S_IMODE(db_path.stat().st_mode) == 0o600


def test_uninitialized_existing_database_is_rejected(db_path: Path) -> None:
    execute(db_path, "CREATE TABLE unrelated (value TEXT)")
    with pytest.raises(ValueError, match="schema version"):
        IdentityStore(db_path).get_user("u1")


@pytest.mark.parametrize("version", [2, 99])
def test_unknown_schema_rejected_without_changes(db_path: Path, version: int) -> None:
    execute(db_path, f"PRAGMA user_version = {version}")
    store = IdentityStore(db_path)
    with pytest.raises(ValueError, match="schema version"):
        store.initialize()
    with pytest.raises(ValueError, match="schema version"):
        store.get_user("u1")
    with pytest.raises(ValueError, match="schema version"):
        add_user(store)
    with closing(sqlite3.connect(db_path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (version,)
        assert connection.execute("SELECT name FROM sqlite_master").fetchall() == []


def test_initialization_failure_rolls_back_schema(db_path: Path) -> None:
    execute(db_path, "CREATE TABLE sessions (existing TEXT)")
    with pytest.raises(sqlite3.OperationalError):
        IdentityStore(db_path).initialize()
    with closing(sqlite3.connect(db_path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (0,)
        assert connection.execute("SELECT name FROM sqlite_master").fetchall() == [("sessions",)]


def test_persistence_defaults_and_immutable_records(store: IdentityStore, db_path: Path) -> None:
    add_user(store)
    assert store.create_session(DIGEST, "u1", 100, expected_password_hash=HASH)
    reloaded = IdentityStore(db_path)
    reloaded.initialize()
    principal = Principal("u1")
    assert reloaded.get_user("u1") == principal
    credential = reloaded.get_credentials("Alice")
    assert credential == Credential(principal, HASH, True)
    assert effective_permissions(principal) == frozenset({Permission.PUBLIC, Permission.EMPLOYEE})
    session = reloaded.get_session(DIGEST, 99)
    assert session == Session("u1", 100)
    assert credential is not None and session is not None
    with pytest.raises(FrozenInstanceError):
        credential.enabled = False  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        session.expires_at = 200  # type: ignore[misc]
    with closing(sqlite3.connect(db_path)) as connection:
        assert connection.execute("SELECT * FROM sessions").fetchall() == [(DIGEST, "u1", 100)]
    assert b"opaque-example-session" not in db_path.read_bytes()


def test_credential_repr_omits_password_hash(store: IdentityStore) -> None:
    add_user(store)
    credential = store.get_credentials("Alice")
    assert credential is not None
    assert credential.password_hash == HASH
    representation = repr(credential)
    assert HASH not in representation
    assert "password_hash" not in representation
    assert "$argon2id$" not in representation


@pytest.mark.parametrize("username", ["a", "a" * 64, "User_1.-", "alice"])
def test_valid_case_sensitive_usernames(store: IdentityStore, username: str) -> None:
    add_user(store)
    add_user(store, "u2", username)
    assert store.get_credentials(username) is not None
    assert store.get_credentials("ALICE") is None


@pytest.mark.parametrize("username", ["", "a" * 65, "é", "a b", "a\n", "' OR 1=1--", "用户"])
def test_invalid_usernames(store: IdentityStore, username: str) -> None:
    with pytest.raises(ValueError):
        add_user(store, username=username)
    assert store.get_user("u1") is None


@pytest.mark.parametrize("value", ["", "plain text", "$argon2id$", "$argon2id$ ", "$argon2i$x"])
def test_invalid_hashes_do_not_mutate(store: IdentityStore, value: str) -> None:
    with pytest.raises(ValueError, match="^Invalid identity data$"):
        store.create_user(user_id="bad", username="Bad", password_hash=value)
    add_user(store)
    assert store.create_session(DIGEST, "u1", 100, expected_password_hash=HASH)
    with pytest.raises(ValueError):
        store.set_password_hash("u1", value)
    assert store.get_credentials("Alice") == Credential(Principal("u1"), HASH, True)
    assert store.get_session(DIGEST, 0) is not None


@pytest.mark.parametrize(
    "overrides",
    [
        {"user_id": " "},
        {"assigned_permissions": frozenset({"HR"})},
        {"assigned_permissions": {Permission.HR}},
        {"system_roles": frozenset({"Admin"})},
        {"system_roles": frozenset({Permission.HR})},
    ],
)
def test_invalid_principals_rejected(store: IdentityStore, overrides: dict[str, Any]) -> None:
    arguments: dict[str, Any] = {"user_id": "u1", "username": "Alice", "password_hash": HASH}
    arguments.update(overrides)
    with pytest.raises(ValueError):
        store.create_user(**arguments)
    assert store.get_credentials("Alice") is None


@pytest.mark.parametrize("user_id,username", [("u1", "Bob"), ("u2", "Alice")])
def test_duplicate_identifiers_rollback(store: IdentityStore, user_id: str, username: str) -> None:
    add_user(store)
    with pytest.raises(sqlite3.IntegrityError):
        add_user(store, user_id, username)
    assert store.get_credentials("Alice") == Credential(Principal("u1"), HASH, True)
    assert store.get_credentials("Bob") is None
    assert store.get_user("u2") is None
    add_user(store, "u3", "Carol")


@pytest.mark.parametrize(
    "encoded",
    ["not json", "null", "{}", '"HR"', "[1]", "[true]", '["HR", "Unknown"]', '["hr"]'],
)
@pytest.mark.parametrize("column", ["permissions", "system_roles"])
def test_malformed_grants_deny_generically(
    store: IdentityStore, db_path: Path, encoded: str, column: str
) -> None:
    add_user(store)
    statement = (
        "UPDATE users SET permissions = ? WHERE user_id = ?"
        if column == "permissions"
        else "UPDATE users SET system_roles = ? WHERE user_id = ?"
    )
    execute(db_path, statement, (encoded, "u1"))
    for lookup in (lambda: store.get_user("u1"), lambda: store.get_credentials("Alice")):
        with pytest.raises(ValueError, match="^Invalid identity data$") as error:
            lookup()
        assert error.value.__suppress_context__


def test_stored_invalid_principal_and_hash_deny(store: IdentityStore, db_path: Path) -> None:
    add_user(store)
    execute(db_path, "UPDATE users SET permission_version = 'invalid'")
    with pytest.raises(ValueError, match="^Invalid identity data$"):
        store.get_user("u1")
    execute(db_path, "UPDATE users SET permission_version = 0, password_hash = 'bad'")
    with pytest.raises(ValueError, match="^Invalid identity data$"):
        store.get_credentials("Alice")


def test_current_access_versions_and_sessions(store: IdentityStore, db_path: Path) -> None:
    add_user(store)
    assert store.create_session(DIGEST, "u1", 100, expected_password_hash=HASH)
    other = IdentityStore(db_path)
    service = AuthorizationService(other)
    store.set_access(
        "u1",
        assigned_permissions=frozenset({Permission.HR, Permission.MANAGER}),
        system_roles=frozenset({SystemRole.ADMIN, SystemRole.EVALUATOR}),
    )
    principal = Principal(
        "u1", frozenset({Permission.HR, Permission.MANAGER}), frozenset(SystemRole), 1
    )
    assert service.resolve("u1") == principal
    assert store.get_credentials("Alice") == Credential(principal, HASH, True)
    assert store.get_session(DIGEST, 0) == Session("u1", 100)
    assert Permission.FINANCE not in effective_permissions(principal)
    store.set_access("u1", assigned_permissions=frozenset(), system_roles=frozenset())
    assert service.resolve("u1") == Principal("u1", permission_version=2)
    assert other.get_session(DIGEST, 0) == Session("u1", 100)


def test_concurrent_access_updates_increment_atomically(store: IdentityStore) -> None:
    add_user(store)

    def update(_: int) -> None:
        store.set_access(
            "u1", assigned_permissions=frozenset({Permission.HR}), system_roles=frozenset()
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(update, range(12)))
    assert store.get_user("u1") == Principal(
        "u1", frozenset({Permission.HR}), permission_version=12
    )


@pytest.mark.parametrize("operation", ["access", "enabled", "password"])
def test_missing_users_raise(store: IdentityStore, operation: str) -> None:
    with pytest.raises(ValueError, match="^Invalid identity data$"):
        if operation == "access":
            store.set_access("absent", assigned_permissions=frozenset(), system_roles=frozenset())
        elif operation == "enabled":
            store.set_enabled("absent", False)
        else:
            store.set_password_hash("absent", HASH)


@pytest.mark.parametrize("now,exists", [(0, True), (99, True), (100, False), (101, False)])
def test_expiration_boundary(store: IdentityStore, now: int, exists: bool) -> None:
    add_user(store)
    assert store.create_session(DIGEST, "u1", 100, expected_password_hash=HASH)
    assert (store.get_session(DIGEST, now) is not None) is exists


def test_disabled_and_reenabled_users(store: IdentityStore) -> None:
    add_user(store)
    add_user(store, "u2", "Bob")
    assert store.create_session(DIGEST, "u1", 100, expected_password_hash=HASH)
    assert store.create_session(OTHER_DIGEST, "u2", 100, expected_password_hash=HASH)
    store.set_enabled("u1", False)
    assert store.get_user("u1") is None
    assert store.get_credentials("Alice") == Credential(
        Principal("u1", permission_version=1), HASH, False
    )
    assert store.get_session(DIGEST, 0) is None
    assert not store.create_session(DIGEST, "u1", 100, expected_password_hash=HASH)
    assert store.get_session(OTHER_DIGEST, 0) == Session("u2", 100)
    store.set_enabled("u1", True)
    assert store.get_user("u1") == Principal("u1", permission_version=2)
    assert store.get_session(DIGEST, 0) is None
    assert store.create_session(DIGEST, "u1", 100, expected_password_hash=HASH)
    store.set_enabled("u1", True)
    assert store.get_session(DIGEST, 0) is None
    assert store.get_user("u1") == Principal("u1", permission_version=3)


def test_session_lookup_independently_checks_disabled_user(
    store: IdentityStore, db_path: Path
) -> None:
    add_user(store)
    assert store.create_session(DIGEST, "u1", 100, expected_password_hash=HASH)
    execute(db_path, "UPDATE users SET enabled = 0")
    assert store.get_session(DIGEST, 0) is None


def test_password_reset_and_stale_login_race(store: IdentityStore, db_path: Path) -> None:
    add_user(store)
    stale = store.get_credentials("Alice")
    assert stale is not None
    assert store.create_session(DIGEST, "u1", 100, expected_password_hash=HASH)
    IdentityStore(db_path).set_password_hash("u1", NEW_HASH)
    assert store.get_user("u1") == Principal("u1", permission_version=1)
    assert store.get_session(DIGEST, 0) is None
    assert not store.create_session(DIGEST, "u1", 100, expected_password_hash=stale.password_hash)
    assert store.get_session(DIGEST, 0) is None
    assert store.create_session(DIGEST, "u1", 100, expected_password_hash=NEW_HASH)
    assert store.get_credentials("Alice") == Credential(
        Principal("u1", permission_version=1), NEW_HASH, True
    )
    store.set_password_hash("u1", NEW_HASH)
    assert store.get_session(DIGEST, 0) is None
    assert store.get_user("u1") == Principal("u1", permission_version=2)


def test_deleted_users_and_foreign_key_cascade(store: IdentityStore, db_path: Path) -> None:
    add_user(store)
    assert store.create_session(DIGEST, "u1", 100, expected_password_hash=HASH)
    with pytest.raises(sqlite3.IntegrityError):
        execute(db_path, "INSERT INTO sessions VALUES (?, ?, ?)", (OTHER_DIGEST, "missing", 100))
    execute(db_path, "DELETE FROM users WHERE user_id = ?", ("u1",))
    assert store.get_user("u1") is None
    assert store.get_credentials("Alice") is None
    assert store.get_session(DIGEST, 0) is None
    assert not store.create_session(DIGEST, "u1", 100, expected_password_hash=HASH)
    with closing(sqlite3.connect(db_path)) as connection:
        assert connection.execute("SELECT * FROM sessions").fetchall() == []


def test_orphan_session_is_not_resolved(store: IdentityStore, db_path: Path) -> None:
    # Simulate externally corrupted storage with foreign keys disabled.
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute("INSERT INTO sessions VALUES (?, ?, ?)", (DIGEST, "missing", 100))
    assert store.get_session(DIGEST, 0) is None


@pytest.mark.parametrize(
    "digest", ["", "raw-token", "a" * 63, "a" * 65, "A" * 64, "g" * 64, "é" * 64]
)
def test_invalid_digests_rejected(store: IdentityStore, digest: str) -> None:
    add_user(store)
    with pytest.raises(ValueError, match="^Invalid identity data$"):
        store.create_session(digest, "u1", 100, expected_password_hash=HASH)
    with pytest.raises(ValueError, match="^Invalid identity data$"):
        store.get_session(digest, 0)
    with pytest.raises(ValueError, match="^Invalid identity data$"):
        store.revoke_session(digest)


@pytest.mark.parametrize("expires_at", [0, -1, True, 1.5, "100"])
def test_invalid_expiration(store: IdentityStore, expires_at: Any) -> None:
    add_user(store)
    with pytest.raises(ValueError):
        store.create_session(DIGEST, "u1", expires_at, expected_password_hash=HASH)
    assert store.get_session(DIGEST, 0) is None


def test_duplicate_sessions_and_revocation(store: IdentityStore) -> None:
    add_user(store)
    store.revoke_session(DIGEST)
    assert store.create_session(DIGEST, "u1", 100, expected_password_hash=HASH)
    with pytest.raises(sqlite3.IntegrityError):
        store.create_session(DIGEST, "u1", 200, expected_password_hash=HASH)
    assert store.get_session(DIGEST, 0) == Session("u1", 100)
    store.revoke_session(DIGEST)
    store.revoke_session(DIGEST)
    assert store.get_session(DIGEST, 0) is None


@pytest.mark.parametrize("operation", ["enabled", "password"])
def test_failed_revocation_rolls_back_entire_mutation(
    store: IdentityStore, db_path: Path, operation: str
) -> None:
    add_user(store)
    assert store.create_session(DIGEST, "u1", 100, expected_password_hash=HASH)
    execute(
        db_path,
        """CREATE TRIGGER fail_delete BEFORE DELETE ON sessions
            BEGIN SELECT RAISE(ABORT, 'forced transaction failure'); END""",
    )
    with pytest.raises(sqlite3.IntegrityError, match="forced transaction failure"):
        if operation == "enabled":
            store.set_enabled("u1", False)
        else:
            store.set_password_hash("u1", NEW_HASH)
    assert store.get_credentials("Alice") == Credential(Principal("u1"), HASH, True)
    assert store.get_session(DIGEST, 0) == Session("u1", 100)
    execute(db_path, "DROP TRIGGER fail_delete")
    store.set_enabled("u1", False)
    assert store.get_session(DIGEST, 0) is None


def test_parameterized_sql_treats_injection_as_data(store: IdentityStore) -> None:
    malicious = "x'; DROP TABLE users; --"
    add_user(store, malicious)
    assert store.get_user(malicious) == Principal(malicious)
    assert store.get_credentials("Alice' OR 1=1 --") is None
    assert store.get_user("' OR 1=1 --") is None
    assert not store.create_session(DIGEST, "' OR 1=1 --", 100, expected_password_hash=HASH)
    assert not store.create_session(
        DIGEST, malicious, 100, expected_password_hash=HASH + "' OR 1=1--"
    )
    assert store.create_session(DIGEST, malicious, 100, expected_password_hash=HASH)
    store.set_access(
        malicious, assigned_permissions=frozenset({Permission.HR}), system_roles=frozenset()
    )
    assert store.get_session(DIGEST, 0) == Session(malicious, 100)
    store.set_password_hash(malicious, NEW_HASH)
    store.set_enabled(malicious, False)
    assert store.get_credentials("Alice") == Credential(
        Principal(malicious, frozenset({Permission.HR}), permission_version=3), NEW_HASH, False
    )


def test_failed_access_validation_preserves_state(store: IdentityStore) -> None:
    add_user(store)
    with pytest.raises(ValueError):
        store.set_access(
            "u1",
            assigned_permissions=frozenset({"Unknown"}),  # type: ignore[arg-type]
            system_roles=frozenset(),
        )
    assert store.get_user("u1") == Principal("u1")
    with pytest.raises(ValueError):
        store.set_enabled("u1", 1)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        store.get_session(DIGEST, True)


def test_foreign_keys_enabled_for_store_connections(store: IdentityStore, db_path: Path) -> None:
    add_user(store)
    execute(
        db_path,
        """CREATE TRIGGER insert_invalid_session AFTER UPDATE ON users
            BEGIN INSERT INTO sessions VALUES (
                'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                'missing', 100); END""",
    )
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        store.set_access("u1", assigned_permissions=frozenset(), system_roles=frozenset())
    assert store.get_user("u1") == Principal("u1")


def test_uri_special_characters_in_database_path(tmp_path: Path) -> None:
    store = IdentityStore(tmp_path / "identity?# database.sqlite3")
    store.initialize()
    add_user(store)
    assert store.get_user("u1") == Principal("u1")


def login_buckets(db_path: Path) -> list[tuple[str, int, int]]:
    with closing(sqlite3.connect(db_path)) as connection:
        return [
            (row[0], row[1], row[2])
            for row in connection.execute(
                "SELECT username_digest, attempts, expires_at FROM login_attempts "
                "ORDER BY username_digest"
            )
        ]


def test_login_budget_counts_valid_and_invalid_credentials(
    store: IdentityStore, db_path: Path
) -> None:
    add_user(store)
    alice_digest = hashlib.sha256(b"Alice").hexdigest()
    missing_digest = hashlib.sha256(b"Missing").hexdigest()
    for _ in range(5):
        assert store.consume_login_attempt(alice_digest, 1)
        assert store.get_credentials("Alice") is not None
        assert store.consume_login_attempt(missing_digest, 1)
        assert store.get_credentials("Missing") is None
    before = login_buckets(db_path)
    for now in (1, 30, 59):
        assert not store.consume_login_attempt(alice_digest, now)
        assert not store.consume_login_attempt(missing_digest, now)
    assert login_buckets(db_path) == before
    assert dict((digest, count) for digest, count, _ in before) == {
        "": 10,
        alice_digest: 5,
        missing_digest: 5,
    }
    assert all(expires_at == 60 for _, _, expires_at in before)


def test_login_global_budget_bounds_rows_and_does_not_extend_on_denial(
    store: IdentityStore, db_path: Path
) -> None:
    for index in range(30):
        digest = hashlib.sha256(str(index).encode()).hexdigest()
        assert store.consume_login_attempt(digest, 1)
    before = login_buckets(db_path)
    assert len(before) == 31
    assert before[0] == ("", 30, 60)
    for index in range(100):
        digest = hashlib.sha256(f"denied-{index}".encode()).hexdigest()
        assert not store.consume_login_attempt(digest, 59)
    assert login_buckets(db_path) == before
    assert store.consume_login_attempt(DIGEST, 60)
    assert login_buckets(db_path) == [("", 1, 120), (DIGEST, 1, 120)]


class _WindowPolicy(TypedDict):
    window_seconds: int
    username_limit: int
    global_limit: int


@pytest.mark.parametrize("window_seconds,first,last,boundary", [(60, 0, 59, 60), (10, 11, 19, 20)])
def test_login_fixed_window_boundary_and_expired_bucket_cleanup(
    store: IdentityStore, db_path: Path, window_seconds: int, first: int, last: int, boundary: int
) -> None:
    policy: _WindowPolicy = {
        "window_seconds": window_seconds,
        "username_limit": 1,
        "global_limit": 2,
    }
    assert store.consume_login_attempt(DIGEST, first, **policy)
    assert not store.consume_login_attempt(DIGEST, last, **policy)
    assert store.consume_login_attempt(OTHER_DIGEST, last, **policy)
    assert not store.consume_login_attempt(OTHER_DIGEST, last, **policy)
    assert store.consume_login_attempt(DIGEST, boundary, **policy)
    expires_at = boundary + window_seconds
    assert login_buckets(db_path) == [("", 1, expires_at), (DIGEST, 1, expires_at)]
    assert store.consume_login_attempt(OTHER_DIGEST, boundary, **policy)
    assert not store.consume_login_attempt(DIGEST, boundary, **policy)


def test_login_budget_persists_across_store_instances(store: IdentityStore, db_path: Path) -> None:
    assert store.consume_login_attempt(DIGEST, 1, username_limit=2)
    other = IdentityStore(db_path)
    other.initialize()
    assert other.consume_login_attempt(DIGEST, 2, username_limit=2)
    assert not IdentityStore(db_path).consume_login_attempt(DIGEST, 3, username_limit=2)
    assert not store.consume_login_attempt(DIGEST, 59, username_limit=2)
    assert IdentityStore(db_path).consume_login_attempt(DIGEST, 60, username_limit=2)


@pytest.mark.parametrize("distinct_usernames,expected", [(False, 5), (True, 30)])
def test_concurrent_login_attempts_cannot_exceed_budgets(
    store: IdentityStore, db_path: Path, distinct_usernames: bool, expected: int
) -> None:
    def attempt(index: int) -> bool:
        digest = hashlib.sha256(str(index).encode()).hexdigest() if distinct_usernames else DIGEST
        return IdentityStore(db_path).consume_login_attempt(digest, 1)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(60)))
    assert sum(results) == expected
    rows = login_buckets(db_path)
    assert rows[0] == ("", expected, 60)
    assert sum(count for digest, count, _ in rows if digest) == expected
    assert len(rows) == (31 if distinct_usernames else 2)
    assert not store.consume_login_attempt(DIGEST, 59)


@pytest.mark.parametrize(
    "digest",
    ["", "Alice", "' OR 1=1--", "a" * 63, "a" * 65, "A" * 64, "g" * 64, None, True, b"a" * 64],
)
def test_invalid_login_digest_does_not_create_buckets(
    store: IdentityStore, db_path: Path, digest: Any
) -> None:
    with pytest.raises(ValueError, match="^Invalid identity data$"):
        store.consume_login_attempt(digest, 1)
    assert login_buckets(db_path) == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"now": -1},
        {"now": True},
        {"now": 1.5},
        {"now": "1"},
        {"window_seconds": 0},
        {"window_seconds": -1},
        {"window_seconds": True},
        {"window_seconds": 1.5},
        {"window_seconds": "60"},
        {"username_limit": 0},
        {"username_limit": -1},
        {"username_limit": True},
        {"username_limit": 1.5},
        {"username_limit": "5"},
        {"global_limit": 0},
        {"global_limit": -1},
        {"global_limit": True},
        {"global_limit": 1.5},
        {"global_limit": "30"},
    ],
)
def test_invalid_login_policy_does_not_mutate(
    store: IdentityStore, db_path: Path, overrides: dict[str, Any]
) -> None:
    assert store.consume_login_attempt(DIGEST, 1)
    before = login_buckets(db_path)
    arguments: dict[str, Any] = {"now": 1}
    arguments.update(overrides)
    with pytest.raises(ValueError, match="^Invalid identity data$"):
        store.consume_login_attempt(DIGEST, **arguments)
    assert login_buckets(db_path) == before


def test_login_bucket_update_failure_rolls_back_both_counters(
    store: IdentityStore, db_path: Path
) -> None:
    assert store.consume_login_attempt(DIGEST, 1)
    before = login_buckets(db_path)
    execute(
        db_path,
        """CREATE TRIGGER fail_login_update BEFORE UPDATE ON login_attempts
            WHEN NEW.username_digest != ''
            BEGIN SELECT RAISE(ABORT, 'forced login failure'); END""",
    )
    with pytest.raises(sqlite3.IntegrityError, match="forced login failure"):
        store.consume_login_attempt(DIGEST, 2)
    assert login_buckets(db_path) == before
    execute(db_path, "DROP TRIGGER fail_login_update")
    assert store.consume_login_attempt(DIGEST, 2)
    assert login_buckets(db_path) == [("", 2, 60), (DIGEST, 2, 60)]


def test_login_throttle_requires_explicit_initialization(db_path: Path) -> None:
    with pytest.raises(sqlite3.OperationalError):
        IdentityStore(db_path).consume_login_attempt(DIGEST, 1)
    assert not db_path.exists()


def test_login_schema_failure_rolls_back_initialization(db_path: Path) -> None:
    execute(db_path, "CREATE TABLE login_attempts (existing TEXT)")
    with pytest.raises(sqlite3.OperationalError):
        IdentityStore(db_path).initialize()
    with closing(sqlite3.connect(db_path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (0,)
        assert connection.execute("SELECT name FROM sqlite_master").fetchall() == [
            ("login_attempts",)
        ]


def stored_sessions(db_path: Path) -> list[tuple[str, str, int]]:
    with closing(sqlite3.connect(db_path)) as connection:
        return [
            (row[0], row[1], row[2])
            for row in connection.execute(
                "SELECT token_digest, user_id, expires_at FROM sessions ORDER BY rowid"
            )
        ]


def test_login_attempt_cleans_expired_sessions_even_when_denied(
    store: IdentityStore, db_path: Path
) -> None:
    add_user(store)
    add_user(store, "u2", "Bob")
    live_digest = hashlib.sha256(b"live session").hexdigest()
    assert store.create_session(DIGEST, "u1", 49, expected_password_hash=HASH)
    assert store.create_session(OTHER_DIGEST, "u2", 50, expected_password_hash=HASH)
    assert store.create_session(live_digest, "u1", 51, expected_password_hash=HASH)
    assert store.consume_login_attempt(DIGEST, 49, username_limit=1)
    assert stored_sessions(db_path) == [(OTHER_DIGEST, "u2", 50), (live_digest, "u1", 51)]
    assert not store.consume_login_attempt(DIGEST, 50, username_limit=1)
    assert stored_sessions(db_path) == [(live_digest, "u1", 51)]
    assert store.get_session(live_digest, 50) == Session("u1", 51)
    with closing(sqlite3.connect(db_path)) as connection:
        assert connection.execute("PRAGMA index_info(sessions_expires_at)").fetchall() == [
            (0, 2, "expires_at")
        ]


def test_session_cap_retains_newest_insertions_per_user(
    store: IdentityStore, db_path: Path
) -> None:
    add_user(store)
    add_user(store, "u2", "Bob")
    assert store.create_session(OTHER_DIGEST, "u2", 100, expected_password_hash=HASH)
    digests = [hashlib.sha256(f"session-{index}".encode()).hexdigest() for index in range(15)]
    for index, digest in enumerate(digests):
        assert store.create_session(digest, "u1", 1000 - index, expected_password_hash=HASH)
        assert sum(user_id == "u1" for _, user_id, _ in stored_sessions(db_path)) <= 10
    assert stored_sessions(db_path) == [(OTHER_DIGEST, "u2", 100)] + [
        (digests[index], "u1", 1000 - index) for index in range(5, 15)
    ]
    for digest in digests[:5]:
        assert store.get_session(digest, 0) is None
    assert store.get_session(digests[-1], 0) is not None


@pytest.mark.parametrize("failure", ["password", "missing", "disabled", "duplicate"])
def test_failed_session_insertion_preserves_retained_sessions(
    store: IdentityStore, db_path: Path, failure: str
) -> None:
    add_user(store)
    digests = [hashlib.sha256(f"retained-{index}".encode()).hexdigest() for index in range(10)]
    for digest in digests:
        assert store.create_session(digest, "u1", 100, expected_password_hash=HASH)
    before = stored_sessions(db_path)
    if failure == "disabled":
        execute(db_path, "UPDATE users SET enabled = 0 WHERE user_id = 'u1'")
    if failure == "duplicate":
        with pytest.raises(sqlite3.IntegrityError):
            store.create_session(digests[0], "u1", 200, expected_password_hash=HASH)
    else:
        assert not store.create_session(
            DIGEST,
            "missing" if failure == "missing" else "u1",
            200,
            expected_password_hash=NEW_HASH if failure == "password" else HASH,
        )
    assert stored_sessions(db_path) == before


def test_session_eviction_failure_rolls_back_new_insertion(
    store: IdentityStore, db_path: Path
) -> None:
    add_user(store)
    for index in range(10):
        digest = hashlib.sha256(f"retained-{index}".encode()).hexdigest()
        assert store.create_session(digest, "u1", 100, expected_password_hash=HASH)
    before = stored_sessions(db_path)
    execute(
        db_path,
        """CREATE TRIGGER fail_eviction BEFORE DELETE ON sessions
            BEGIN SELECT RAISE(ABORT, 'forced eviction failure'); END""",
    )
    with pytest.raises(sqlite3.IntegrityError, match="forced eviction failure"):
        store.create_session(DIGEST, "u1", 100, expected_password_hash=HASH)
    assert stored_sessions(db_path) == before


def test_caller_budget_stops_rotating_usernames_without_consuming_global_budget(
    store: IdentityStore, db_path: Path
) -> None:
    for index in range(10):
        username_digest = hashlib.sha256(f"username-{index}".encode()).hexdigest()
        assert store.consume_login_attempt(username_digest, 1, caller_digest=DIGEST)
    before = login_buckets(db_path)
    assert before[0] == ("", 10, 60)
    assert (f"caller:{DIGEST}", 10, 60) in before
    for index in range(40):
        username_digest = hashlib.sha256(f"denied-{index}".encode()).hexdigest()
        assert not store.consume_login_attempt(username_digest, 59, caller_digest=DIGEST)
    assert login_buckets(db_path) == before
    assert store.consume_login_attempt(DIGEST, 59, caller_digest=OTHER_DIGEST)
    assert login_buckets(db_path)[0] == ("", 11, 60)


def test_caller_keys_do_not_collide_with_username_or_global_keys(
    store: IdentityStore, db_path: Path
) -> None:
    assert store.consume_login_attempt(DIGEST, 1, caller_digest=DIGEST)
    assert login_buckets(db_path) == sorted(
        [("", 1, 60), (DIGEST, 1, 60), (f"caller:{DIGEST}", 1, 60)]
    )


def test_caller_budget_persists_and_resets_at_window_boundary(
    store: IdentityStore, db_path: Path
) -> None:
    assert store.consume_login_attempt(DIGEST, 1, caller_digest=DIGEST, caller_limit=2)
    other = IdentityStore(db_path)
    other.initialize()
    assert other.consume_login_attempt(OTHER_DIGEST, 2, caller_digest=DIGEST, caller_limit=2)
    assert not IdentityStore(db_path).consume_login_attempt(
        DIGEST, 59, caller_digest=DIGEST, caller_limit=2
    )
    assert other.consume_login_attempt(DIGEST, 60, caller_digest=DIGEST, caller_limit=2)
    assert login_buckets(db_path) == sorted(
        [("", 1, 120), (DIGEST, 1, 120), (f"caller:{DIGEST}", 1, 120)]
    )


@pytest.mark.parametrize(
    "digest",
    ["", "raw caller", "caller:" + DIGEST, "a" * 63, "a" * 65, "A" * 64, "g" * 64, True, b"a" * 64],
)
def test_invalid_caller_digest_does_not_mutate(
    store: IdentityStore, db_path: Path, digest: Any
) -> None:
    assert store.consume_login_attempt(DIGEST, 1, caller_digest=DIGEST)
    before = login_buckets(db_path)
    with pytest.raises(ValueError, match="^Invalid identity data$"):
        store.consume_login_attempt(DIGEST, 2, caller_digest=digest)
    assert login_buckets(db_path) == before


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "10", None])
@pytest.mark.parametrize("caller", [None, DIGEST])
def test_invalid_caller_limit_does_not_mutate(
    store: IdentityStore, db_path: Path, limit: Any, caller: str | None
) -> None:
    with pytest.raises(ValueError, match="^Invalid identity data$"):
        store.consume_login_attempt(DIGEST, 1, caller_digest=caller, caller_limit=limit)
    assert login_buckets(db_path) == []


def test_caller_and_username_rows_are_bounded_by_global_budget(
    store: IdentityStore, db_path: Path
) -> None:
    for index in range(30):
        digest = hashlib.sha256(str(index).encode()).hexdigest()
        assert store.consume_login_attempt(digest, 1, caller_digest=digest)
    before = login_buckets(db_path)
    assert len(before) == 2 * 30 + 1
    for index in range(100):
        digest = hashlib.sha256(f"denied-{index}".encode()).hexdigest()
        assert not store.consume_login_attempt(digest, 59, caller_digest=digest)
    assert login_buckets(db_path) == before


def test_concurrent_caller_attempts_cannot_exceed_budget(
    store: IdentityStore, db_path: Path
) -> None:
    def attempt(index: int) -> bool:
        username_digest = hashlib.sha256(str(index).encode()).hexdigest()
        return IdentityStore(db_path).consume_login_attempt(
            username_digest, 1, caller_digest=DIGEST
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(40)))
    assert sum(results) == 10
    assert login_buckets(db_path)[0] == ("", 10, 60)
    assert (f"caller:{DIGEST}", 10, 60) in login_buckets(db_path)
    assert store.consume_login_attempt(DIGEST, 1, caller_digest=OTHER_DIGEST)


def test_caller_update_failure_rolls_back_all_counters_and_session_cleanup(
    store: IdentityStore, db_path: Path
) -> None:
    add_user(store)
    assert store.create_session(DIGEST, "u1", 2, expected_password_hash=HASH)
    assert store.consume_login_attempt(DIGEST, 1, caller_digest=DIGEST)
    before = login_buckets(db_path)
    execute(
        db_path,
        """CREATE TRIGGER fail_caller_update BEFORE UPDATE ON login_attempts
            WHEN NEW.username_digest LIKE 'caller:%'
            BEGIN SELECT RAISE(ABORT, 'forced caller failure'); END""",
    )
    with pytest.raises(sqlite3.IntegrityError, match="forced caller failure"):
        store.consume_login_attempt(DIGEST, 2, caller_digest=DIGEST)
    assert login_buckets(db_path) == before
    assert stored_sessions(db_path) == [(DIGEST, "u1", 2)]


def test_create_users_batch_is_atomic_and_validated(store: IdentityStore) -> None:
    from policypilot.app.services.identity_store import NewUser

    first = NewUser("batch-1", "batch.one", HASH)
    second = NewUser("batch-2", "batch.two", NEW_HASH, frozenset({Permission.HR}))
    store.create_users((first, second))
    assert store.get_credentials("batch.one") is not None
    assert store.get_user("batch-2").assigned_permissions == frozenset({Permission.HR})
    with pytest.raises(ValueError):
        store.create_users(())
    with pytest.raises(ValueError):
        store.create_users((NewUser("batch-3", "batch.three", "not-a-hash"),))
    with pytest.raises(ValueError):
        store.create_users((NewUser("batch-4", "Bad Name", HASH),))
    with pytest.raises(sqlite3.IntegrityError):
        store.create_users(
            (
                NewUser("batch-new", "batch.new", HASH),
                NewUser("batch-1", "batch.duplicate", HASH),
            )
        )
    assert store.get_user("batch-4") is None
    assert store.get_user("batch-new") is None
    assert store.get_user("batch-1") is not None
