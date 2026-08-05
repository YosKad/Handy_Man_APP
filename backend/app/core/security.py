"""Password hashing and JWT issuance.

Refresh tokens are issued in *families*: every rotation keeps the family id and
increments the generation. Presenting a superseded generation is evidence of
theft, so the whole family is revoked rather than just the presented token
(``docs/04-api-design.md`` §3).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Final

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.errors import TokenExpired, Unauthenticated

ALGORITHM: Final = "HS256"
ISSUER: Final = "handyai"

# argon2id, tuned for ~100 ms on server hardware.
_hasher = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=4)

MIN_PASSWORD_LENGTH: Final = 10
# No maximum: a passphrase is a good password. Argon2 handles long inputs.


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime
    refresh_family_id: str
    refresh_generation: int

    @property
    def expires_in_seconds(self) -> int:
        return int((self.access_expires_at - datetime.now(UTC)).total_seconds())


@dataclass(frozen=True, slots=True)
class TokenClaims:
    subject: uuid.UUID
    token_type: TokenType
    role: str
    jti: str
    issued_at: datetime
    expires_at: datetime
    family_id: str | None = None
    generation: int | None = None


# ------------------------------------------------------------------ passwords


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the stored hash predates the current cost parameters."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


# ------------------------------------------------------------------ tokens


class TokenService:
    """Issues and verifies JWTs. The only place a secret key is used."""

    def __init__(
        self,
        secret_key: str,
        *,
        access_ttl_minutes: int = 15,
        refresh_ttl_days: int = 30,
    ) -> None:
        self._secret = secret_key
        self._access_ttl = timedelta(minutes=access_ttl_minutes)
        self._refresh_ttl = timedelta(days=refresh_ttl_days)

    def issue_pair(
        self,
        user_id: uuid.UUID,
        *,
        role: str = "user",
        family_id: str | None = None,
        generation: int = 0,
    ) -> TokenPair:
        now = datetime.now(UTC)
        family = family_id or secrets.token_urlsafe(16)

        access_expires = now + self._access_ttl
        refresh_expires = now + self._refresh_ttl

        access = self._encode(
            {
                "sub": str(user_id),
                "typ": TokenType.ACCESS.value,
                "role": role,
                "iat": int(now.timestamp()),
                "exp": int(access_expires.timestamp()),
                "jti": uuid.uuid4().hex,
                "iss": ISSUER,
            }
        )
        refresh = self._encode(
            {
                "sub": str(user_id),
                "typ": TokenType.REFRESH.value,
                "role": role,
                "iat": int(now.timestamp()),
                "exp": int(refresh_expires.timestamp()),
                "jti": uuid.uuid4().hex,
                "fam": family,
                "gen": generation,
                "iss": ISSUER,
            }
        )
        return TokenPair(
            access_token=access,
            refresh_token=refresh,
            access_expires_at=access_expires,
            refresh_expires_at=refresh_expires,
            refresh_family_id=family,
            refresh_generation=generation,
        )

    def rotate(self, claims: TokenClaims) -> TokenPair:
        """Issue the next generation in an existing refresh family."""
        if claims.token_type is not TokenType.REFRESH:
            raise Unauthenticated(log_detail="rotate called with a non-refresh token")
        if claims.family_id is None or claims.generation is None:
            raise Unauthenticated(log_detail="refresh token missing family claims")
        return self.issue_pair(
            claims.subject,
            role=claims.role,
            family_id=claims.family_id,
            generation=claims.generation + 1,
        )

    def verify(self, token: str, expected_type: TokenType) -> TokenClaims:
        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                self._secret,
                algorithms=[ALGORITHM],
                issuer=ISSUER,
                options={"require": ["exp", "iat", "sub", "jti"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenExpired(log_detail=str(exc)) from exc
        except jwt.InvalidTokenError as exc:
            raise Unauthenticated(log_detail=f"invalid token: {exc}") from exc

        if payload.get("typ") != expected_type.value:
            raise Unauthenticated(
                log_detail=f"expected {expected_type.value} token, got {payload.get('typ')!r}"
            )

        try:
            subject = uuid.UUID(str(payload["sub"]))
        except (KeyError, ValueError) as exc:
            raise Unauthenticated(log_detail="token subject is not a uuid") from exc

        return TokenClaims(
            subject=subject,
            token_type=expected_type,
            role=str(payload.get("role", "user")),
            jti=str(payload["jti"]),
            issued_at=datetime.fromtimestamp(int(payload["iat"]), tz=UTC),
            expires_at=datetime.fromtimestamp(int(payload["exp"]), tz=UTC),
            family_id=payload.get("fam"),
            generation=payload.get("gen"),
        )

    def _encode(self, payload: dict[str, Any]) -> str:
        return jwt.encode(payload, self._secret, algorithm=ALGORITHM)


# ------------------------------------------------------------------ misc


def generate_opaque_token(length: int = 32) -> str:
    """For password-reset and email-verification links."""
    return secrets.token_urlsafe(length)


def hash_opaque_token(token: str) -> str:
    """Reset tokens are stored hashed, so a database leak is not a login."""
    return hashlib.sha256(token.encode()).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode(), right.encode())


def hash_ip(ip_address: str, salt: str) -> str:
    """Audit records keep a hashed IP: useful for abuse, not for tracking."""
    return hashlib.sha256(f"{salt}:{ip_address}".encode()).hexdigest()[:32]
