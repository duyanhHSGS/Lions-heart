"""Single-owner authentication for local and deployed Lion interfaces."""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from secrets import token_urlsafe

from cor_being import Being, Life, World
from cor_beings.storage import StorageBeing

SESSION_SECONDS = 12 * 60 * 60
SCRYPT_N = 2**14


@dataclass(frozen=True, slots=True)
class AuthSession:
    token: str
    csrf_token: str
    expires_at: int


class AuthBeing(Being):
    """Own the one owner account and revocable authenticated sessions."""

    name = "auth"
    needs = (StorageBeing,)

    def __init__(self) -> None:
        self._storage: StorageBeing | None = None
        self._failures: dict[str, tuple[int, float]] = {}

    def birth(self, world: World, life: Life) -> None:
        self._storage = world.need(StorageBeing)
        self._purge_expired()
        life.on_death(self._forget)
        # TODO: Add administrator-controlled recovery codes for lost passwords.

    @property
    def setup_required(self) -> bool:
        return (
            self._require_storage().fetchone("SELECT id FROM owner WHERE id=1") is None
        )

    def setup(self, username: str, password: str) -> AuthSession:
        username = self._validate_username(username)
        self._validate_password(password)
        storage = self._require_storage()
        if not self.setup_required:
            raise RuntimeError("owner account already exists")
        salt = hashlib.sha256(token_urlsafe(32).encode("ascii")).digest()[:16]
        digest = self._password_hash(password, salt)
        now = int(time.time())
        try:
            storage.execute(
                "INSERT INTO owner(id, username, password_hash, password_salt, created_at) VALUES (1, ?, ?, ?, ?)",
                (username, digest, salt, now),
            )
        except Exception:
            if not self.setup_required:
                raise RuntimeError("owner account already exists") from None
            raise
        return self._create_session()

    def login(
        self, username: str, password: str, *, remote: str = "local"
    ) -> AuthSession:
        self._check_rate_limit(remote)
        row = self._require_storage().fetchone(
            "SELECT username, password_hash, password_salt FROM owner WHERE id=1"
        )
        supplied = self._password_hash(
            password if isinstance(password, str) else "",
            row["password_salt"] if row else b"0" * 16,
        )
        valid = bool(
            row
            and isinstance(username, str)
            and hmac.compare_digest(username, row["username"])
            and hmac.compare_digest(supplied, row["password_hash"])
        )
        if not valid:
            count, _ = self._failures.get(remote, (0, 0.0))
            self._failures[remote] = (count + 1, time.monotonic())
            raise PermissionError("invalid credentials")
        self._failures.pop(remote, None)
        return self._create_session()

    def authenticate(self, token: str | None) -> bool:
        if not token:
            return False
        now = int(time.time())
        row = self._require_storage().fetchone(
            "SELECT expires_at FROM auth_sessions WHERE token_hash=?",
            (self._token_hash(token),),
        )
        return bool(row and int(row["expires_at"]) > now)

    def validate_csrf(self, token: str | None, csrf_token: str | None) -> bool:
        if not token or not csrf_token:
            return False
        row = self._require_storage().fetchone(
            "SELECT csrf_hash, expires_at FROM auth_sessions WHERE token_hash=?",
            (self._token_hash(token),),
        )
        return bool(
            row
            and int(row["expires_at"]) > int(time.time())
            and hmac.compare_digest(row["csrf_hash"], self._token_hash(csrf_token))
        )

    def logout(self, token: str | None) -> None:
        if token:
            self._require_storage().execute(
                "DELETE FROM auth_sessions WHERE token_hash=?",
                (self._token_hash(token),),
            )

    def refresh_csrf(self, token: str | None) -> str | None:
        """Rotate the CSRF secret for one still-authenticated HttpOnly session."""
        if not token or not self.authenticate(token):
            return None
        csrf = token_urlsafe(24)
        cursor = self._require_storage().execute(
            "UPDATE auth_sessions SET csrf_hash=? WHERE token_hash=? AND expires_at>?",
            (self._token_hash(csrf), self._token_hash(token), int(time.time())),
        )
        return csrf if cursor.rowcount == 1 else None

    def _create_session(self) -> AuthSession:
        token = token_urlsafe(32)
        csrf = token_urlsafe(24)
        now = int(time.time())
        expires = now + SESSION_SECONDS
        self._require_storage().execute(
            "INSERT INTO auth_sessions(token_hash, csrf_hash, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (self._token_hash(token), self._token_hash(csrf), expires, now),
        )
        return AuthSession(token, csrf, expires)

    def _check_rate_limit(self, remote: str) -> None:
        count, last = self._failures.get(remote, (0, 0.0))
        if time.monotonic() - last > 60:
            self._failures.pop(remote, None)
            return
        if count >= 5:
            raise PermissionError("too many login attempts")

    def _purge_expired(self) -> None:
        self._require_storage().execute(
            "DELETE FROM auth_sessions WHERE expires_at <= ?", (int(time.time()),)
        )

    @staticmethod
    def _validate_username(username: str) -> str:
        if not isinstance(username, str) or not 1 <= len(username.strip()) <= 64:
            raise ValueError("username must contain 1 through 64 characters")
        return username.strip()

    @staticmethod
    def _validate_password(password: str) -> None:
        if not isinstance(password, str) or len(password) < 10:
            raise ValueError("password must contain at least 10 characters")
        if len(password) > 1024:
            raise ValueError("password is too long")

    @staticmethod
    def _password_hash(password: str, salt: bytes) -> bytes:
        return hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=8, p=1, dklen=32
        )

    @staticmethod
    def _token_hash(token: str) -> bytes:
        return hashlib.sha256(token.encode("utf-8")).digest()

    def _require_storage(self) -> StorageBeing:
        if self._storage is None:
            raise RuntimeError("auth is not alive")
        return self._storage

    def _forget(self) -> None:
        self._storage = None
        self._failures.clear()


__all__ = ["SESSION_SECONDS", "AuthBeing", "AuthSession"]
