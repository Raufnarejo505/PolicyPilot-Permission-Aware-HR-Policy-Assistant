"""Password and opaque-token primitives; no client-controlled grant claims."""

import hashlib
import re
import secrets

from argon2 import PasswordHasher, Type, extract_parameters
from argon2.exceptions import InvalidHashError, VerificationError

_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}", re.ASCII)


class PasswordSecurity:
    def __init__(self) -> None:
        # RFC 9106 low-memory profile; keep verification cost bounded even for corrupt records.
        self._hasher = PasswordHasher(
            time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16, type=Type.ID
        )
        self._dummy = self._hasher.hash(secrets.token_urlsafe(32))

    def hash(self, password: str) -> str:
        if type(password) is not str or not 12 <= len(password.encode("utf-8")) <= 1024:
            raise ValueError("Password must contain 12 to 1024 UTF-8 bytes")
        return self._hasher.hash(password)

    def verify(self, password_hash: str | None, password: str) -> bool:
        if type(password) is not str:
            return False
        encoded = password_hash if password_hash is not None else self._dummy
        try:
            if not 1 <= len(password.encode("utf-8")) <= 1024:
                return False
            parameters = extract_parameters(encoded)
            if parameters != extract_parameters(self._dummy):
                # Still perform normal-cost work for invalid records.
                self._hasher.verify(self._dummy, password)
                return False
            verified = self._hasher.verify(encoded, password)
            return verified and password_hash is not None
        except (InvalidHashError, VerificationError, ValueError):
            return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    if type(token) is not str or _TOKEN.fullmatch(token) is None:
        raise ValueError("Invalid session")
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def username_digest(username: str) -> str:
    return hashlib.sha256(username.encode("utf-8")).hexdigest()
