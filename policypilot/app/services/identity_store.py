"""SQLite identities and opaque-session digests; no authentication or token generation."""

import json
import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from policypilot.app.models.domain import Permission, Principal, SystemRole

_INVALID = "Invalid identity data"
_USERNAME = re.compile(r"[A-Za-z0-9_.-]{1,64}", re.ASCII)
_DIGEST = re.compile(r"[0-9a-f]{64}", re.ASCII)


@dataclass(frozen=True)
class Credential:
    principal: Principal
    password_hash: str = field(repr=False)
    enabled: bool


@dataclass(frozen=True)
class Session:
    user_id: str
    expires_at: int


def _validate_hash(password_hash: str) -> None:
    # Encoding validation only: password verification belongs to the authentication layer.
    if (
        type(password_hash) is not str
        or not password_hash.startswith("$argon2id$")
        or not password_hash.removeprefix("$argon2id$").strip()
    ):
        raise ValueError(_INVALID)


def _validate_digest(token_digest: str) -> None:
    if type(token_digest) is not str or _DIGEST.fullmatch(token_digest) is None:
        raise ValueError(_INVALID)


def _labels(encoded: object) -> list[str]:
    if type(encoded) is not str:
        raise ValueError(_INVALID)
    decoded: object = json.loads(encoded)
    if not isinstance(decoded, list) or any(type(label) is not str for label in decoded):
        raise ValueError(_INVALID)
    return [str(label) for label in decoded]


def _principal(row: sqlite3.Row) -> Principal:
    try:
        return Principal(
            user_id=row["user_id"],
            assigned_permissions=frozenset(
                Permission(label) for label in _labels(row["permissions"])
            ),
            system_roles=frozenset(SystemRole(label) for label in _labels(row["system_roles"])),
            permission_version=row["permission_version"],
        )
    except (TypeError, ValueError):
        raise ValueError(_INVALID) from None


class IdentityStore:
    def __init__(self, path: Path) -> None:
        self._path = path.absolute()

    @contextmanager
    def _connection(
        self, *, write: bool = False, initialize: bool = False
    ) -> Iterator[sqlite3.Connection]:
        # Creation is exclusive and permission-restricted in initialize(), never in SQLite.
        connection = sqlite3.connect(f"{self._path.as_uri()}?mode=rw", uri=True)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                connection.execute("BEGIN IMMEDIATE" if write or initialize else "BEGIN")
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version != 1 and not (initialize and version == 0):
                    raise ValueError("Unsupported identity schema version")
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create a private empty store; failures may leave a new file without secrets."""
        try:
            descriptor = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        with self._connection(initialize=True) as connection:
            if connection.execute("PRAGMA user_version").fetchone()[0] == 1:
                return
            connection.execute(
                """CREATE TABLE users (
                    user_id TEXT PRIMARY KEY NOT NULL,
                    username TEXT NOT NULL UNIQUE COLLATE BINARY,
                    password_hash TEXT NOT NULL,
                    permissions TEXT NOT NULL,
                    system_roles TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                    permission_version INTEGER NOT NULL DEFAULT 0 CHECK (permission_version >= 0)
                )"""
            )
            connection.execute(
                """CREATE TABLE sessions (
                    token_digest TEXT PRIMARY KEY NOT NULL
                        CHECK (length(token_digest) = 64 AND token_digest NOT GLOB '*[^0-9a-f]*'),
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    expires_at INTEGER NOT NULL CHECK (expires_at > 0)
                )"""
            )
            connection.execute("CREATE INDEX sessions_user_id ON sessions(user_id)")
            connection.execute("CREATE INDEX sessions_expires_at ON sessions(expires_at)")
            connection.execute(
                """CREATE TABLE login_attempts (
                    username_digest TEXT PRIMARY KEY NOT NULL
                        CHECK (username_digest = '' OR (length(username_digest) = 64
                            AND username_digest NOT GLOB '*[^0-9a-f]*')
                            OR (length(username_digest) = 71
                                AND substr(username_digest, 1, 7) = 'caller:'
                                AND substr(username_digest, 8) NOT GLOB '*[^0-9a-f]*')),
                    attempts INTEGER NOT NULL CHECK (attempts > 0),
                    expires_at INTEGER NOT NULL CHECK (expires_at > 0)
                )"""
            )
            connection.execute("PRAGMA user_version = 1")

    def get_user(self, user_id: str) -> Principal | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE user_id = ? AND enabled = 1", (user_id,)
            ).fetchone()
            return None if row is None else _principal(row)

    def get_credentials(self, username: str) -> Credential | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
            if row is None:
                return None
            principal = _principal(row)
            _validate_hash(row["password_hash"])
            if type(row["enabled"]) is not int or row["enabled"] not in (0, 1):
                raise ValueError(_INVALID)
            return Credential(principal, row["password_hash"], bool(row["enabled"]))

    def create_user(
        self,
        *,
        user_id: str,
        username: str,
        password_hash: str,
        assigned_permissions: frozenset[Permission] = frozenset(),
        system_roles: frozenset[SystemRole] = frozenset(),
    ) -> None:
        Principal(user_id, assigned_permissions, system_roles)
        if type(username) is not str or _USERNAME.fullmatch(username) is None:
            raise ValueError(_INVALID)
        _validate_hash(password_hash)
        with self._connection(write=True) as connection:
            connection.execute(
                """INSERT INTO users
                    (user_id, username, password_hash, permissions, system_roles)
                    VALUES (?, ?, ?, ?, ?)""",
                (
                    user_id,
                    username,
                    password_hash,
                    json.dumps(sorted(assigned_permissions)),
                    json.dumps(sorted(system_roles)),
                ),
            )

    def set_access(
        self,
        user_id: str,
        *,
        assigned_permissions: frozenset[Permission],
        system_roles: frozenset[SystemRole],
    ) -> None:
        Principal(user_id, assigned_permissions, system_roles)
        with self._connection(write=True) as connection:
            cursor = connection.execute(
                """UPDATE users SET permissions = ?, system_roles = ?,
                    permission_version = permission_version + 1 WHERE user_id = ?""",
                (
                    json.dumps(sorted(assigned_permissions)),
                    json.dumps(sorted(system_roles)),
                    user_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError(_INVALID)

    def set_enabled(self, user_id: str, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise ValueError(_INVALID)
        with self._connection(write=True) as connection:
            cursor = connection.execute(
                """UPDATE users SET enabled = ?, permission_version = permission_version + 1
                    WHERE user_id = ?""",
                (int(enabled), user_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(_INVALID)
            connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    def set_password_hash(self, user_id: str, password_hash: str) -> None:
        _validate_hash(password_hash)
        with self._connection(write=True) as connection:
            cursor = connection.execute(
                """UPDATE users SET password_hash = ?, permission_version = permission_version + 1
                    WHERE user_id = ?""",
                (password_hash, user_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(_INVALID)
            connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    def create_session(
        self,
        token_digest: str,
        user_id: str,
        expires_at: int,
        *,
        expected_password_hash: str,
    ) -> bool:
        _validate_digest(token_digest)
        if type(expires_at) is not int or expires_at <= 0:
            raise ValueError(_INVALID)
        _validate_hash(expected_password_hash)
        with self._connection(write=True) as connection:
            cursor = connection.execute(
                """INSERT INTO sessions (token_digest, user_id, expires_at)
                    SELECT ?, user_id, ? FROM users
                    WHERE user_id = ? AND enabled = 1 AND password_hash = ?""",
                (token_digest, expires_at, user_id, expected_password_hash),
            )
            if cursor.rowcount != 1:
                return False
            connection.execute(
                """DELETE FROM sessions WHERE rowid IN (
                    SELECT rowid FROM sessions WHERE user_id = ?
                    ORDER BY rowid DESC LIMIT -1 OFFSET 10
                )""",
                (user_id,),
            )
            return True

    def get_session(self, token_digest: str, now: int) -> Session | None:
        _validate_digest(token_digest)
        if type(now) is not int:
            raise ValueError(_INVALID)
        with self._connection() as connection:
            row = connection.execute(
                """SELECT sessions.user_id, sessions.expires_at FROM sessions
                    JOIN users ON users.user_id = sessions.user_id
                    WHERE token_digest = ? AND expires_at > ? AND enabled = 1""",
                (token_digest, now),
            ).fetchone()
            return None if row is None else Session(row["user_id"], row["expires_at"])

    def revoke_session(self, token_digest: str) -> None:
        _validate_digest(token_digest)
        with self._connection(write=True) as connection:
            connection.execute("DELETE FROM sessions WHERE token_digest = ?", (token_digest,))

    def consume_login_attempt(
        self,
        username_digest: str,
        now: int,
        *,
        window_seconds: int = 60,
        username_limit: int = 5,
        global_limit: int = 30,
        caller_digest: str | None = None,
        caller_limit: int = 10,
    ) -> bool:
        """Consume budgets before checking credentials, regardless of their validity.

        Windows are epoch-aligned; callers must use a consistent server-side policy
        and clock. The empty key is global; caller keys have a 'caller:' prefix.
        Denials create no rows, bounding active buckets to 2 * global_limit + 1.
        Integration must supply a server-derived caller digest to enforce its budget.
        """
        _validate_digest(username_digest)
        if caller_digest is not None:
            _validate_digest(caller_digest)
        if type(now) is not int or now < 0:
            raise ValueError(_INVALID)
        if any(
            type(value) is not int or value <= 0
            for value in (window_seconds, username_limit, global_limit, caller_limit)
        ):
            raise ValueError(_INVALID)
        expires_at = (now // window_seconds + 1) * window_seconds
        with self._connection(write=True) as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            connection.execute("DELETE FROM login_attempts WHERE expires_at <= ?", (now,))
            budgets = [("", expires_at), (username_digest, expires_at)]
            if caller_digest is not None:
                caller_key = f"caller:{caller_digest}"
                caller_row = connection.execute(
                    "SELECT attempts FROM login_attempts WHERE username_digest = ?", (caller_key,)
                ).fetchone()
                if caller_row is not None and caller_row["attempts"] >= caller_limit:
                    return False
                budgets.append((caller_key, expires_at))
            global_row = connection.execute(
                "SELECT attempts FROM login_attempts WHERE username_digest = ''"
            ).fetchone()
            username_row = connection.execute(
                "SELECT attempts FROM login_attempts WHERE username_digest = ?", (username_digest,)
            ).fetchone()
            if (global_row is not None and global_row["attempts"] >= global_limit) or (
                username_row is not None and username_row["attempts"] >= username_limit
            ):
                return False
            connection.executemany(
                """INSERT INTO login_attempts (username_digest, attempts, expires_at)
                    VALUES (?, 1, ?) ON CONFLICT (username_digest)
                    DO UPDATE SET attempts = attempts + 1""",
                budgets,
            )
            return True
