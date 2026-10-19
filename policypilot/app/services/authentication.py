"""Authenticate using current server records and revocable, claim-free sessions."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from policypilot.app.core.security import PasswordSecurity, new_token, token_digest, username_digest
from policypilot.app.models.domain import Principal
from policypilot.app.services.authorization import AuthorizationService
from policypilot.app.services.identity_store import IdentityStore


class AuthenticationError(Exception):
    """Deliberately indistinguishable credential/session/store failure."""


class LoginThrottled(Exception):
    """The persisted login budget is exhausted."""


@dataclass(frozen=True)
class IssuedSession:
    access_token: str = field(repr=False)
    expires_in: int


class AuthenticationService:
    def __init__(
        self,
        store: IdentityStore,
        *,
        session_ttl_seconds: int = 3600,
        passwords: PasswordSecurity | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if type(session_ttl_seconds) is not int or not 60 <= session_ttl_seconds <= 86400:
            raise ValueError("Invalid session lifetime")
        self._store = store
        self._ttl = session_ttl_seconds
        self._passwords = passwords if passwords is not None else PasswordSecurity()
        self._clock = clock

    def login(self, username: str, password: str, *, caller_id: str = "local") -> IssuedSession:
        try:
            now = int(self._clock())
            if not self._store.consume_login_attempt(
                username_digest(username), now, caller_digest=username_digest(caller_id)
            ):
                raise LoginThrottled("Login temporarily unavailable")
            credential = self._store.get_credentials(username)
            verified = self._passwords.verify(
                None if credential is None else credential.password_hash, password
            )
            if not verified or credential is None or not credential.enabled:
                raise AuthenticationError("Authentication required")
            token = new_token()
            if not self._store.create_session(
                token_digest(token),
                credential.principal.user_id,
                now + self._ttl,
                expected_password_hash=credential.password_hash,
            ):
                raise AuthenticationError("Authentication required")
            return IssuedSession(token, self._ttl)
        except LoginThrottled:
            raise
        except Exception:
            raise AuthenticationError("Authentication required") from None

    def authenticate(self, token: str) -> Principal:
        try:
            session = self._store.get_session(token_digest(token), int(self._clock()))
            if session is None:
                raise AuthenticationError("Authentication required")
            return AuthorizationService(self._store).resolve(session.user_id)
        except Exception:
            raise AuthenticationError("Authentication required") from None

    def logout(self, token: str) -> None:
        try:
            self._store.revoke_session(token_digest(token))
        except Exception:
            raise AuthenticationError("Authentication required") from None
