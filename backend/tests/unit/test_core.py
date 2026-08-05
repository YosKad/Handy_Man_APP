"""Config invariants, error envelope, logging redaction, security, rate limiting."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import AIProvider, Environment, Settings, StorageBackend
from app.core.errors import (
    AppError,
    ConfidenceTooLow,
    ErrorCode,
    ProviderContractError,
    RateLimited,
)
from app.core.logging import JsonFormatter, redact
from app.core.ratelimit import LIMITS, Bucket, InMemoryRateLimiter
from app.core.security import (
    MIN_PASSWORD_LENGTH,
    TokenService,
    TokenType,
    hash_opaque_token,
    hash_password,
    verify_password,
)
from app.shared.values import BoundingBox, Confidence, Cost, LoadClass, Measurement, UnitSystem

# ------------------------------------------------------------------ config


def test_local_environment_accepts_development_defaults() -> None:
    settings = Settings(environment=Environment.LOCAL)
    assert settings.is_production is False
    assert settings.docs_url == "/docs"


def test_production_rejects_the_development_secret() -> None:
    with pytest.raises(ValueError, match="SECRET_KEY"):
        Settings(
            environment=Environment.PRODUCTION,
            ai_provider=AIProvider.OPENAI,
            openai_api_key="key",
            storage_backend=StorageBackend.S3,
        )


def test_production_rejects_the_fake_provider() -> None:
    """Fixtures must never be served to real users."""
    with pytest.raises(ValueError, match="fake"):
        Settings(
            environment=Environment.PRODUCTION,
            secret_key="a" * 48,
            ai_provider=AIProvider.FAKE,
            storage_backend=StorageBackend.S3,
        )


def test_production_rejects_local_storage_and_wildcard_cors() -> None:
    with pytest.raises(ValueError, match="Invalid production configuration") as excinfo:
        Settings(
            environment=Environment.PRODUCTION,
            secret_key="a" * 48,
            ai_provider=AIProvider.OPENAI,
            openai_api_key="key",
            storage_backend=StorageBackend.LOCAL,
            cors_origins=["*"],
        )
    message = str(excinfo.value)
    assert "STORAGE_BACKEND=local" in message
    assert "wildcard" in message


def test_production_requires_a_key_for_every_configured_provider() -> None:
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        Settings(
            environment=Environment.PRODUCTION,
            secret_key="a" * 48,
            ai_provider=AIProvider.OPENAI,
            openai_api_key="key",
            ai_fallback_providers=[AIProvider.ANTHROPIC],
            storage_backend=StorageBackend.S3,
        )


def test_production_hides_the_docs() -> None:
    settings = Settings(
        environment=Environment.PRODUCTION,
        secret_key="a" * 48,
        ai_provider=AIProvider.OPENAI,
        openai_api_key="key",
        storage_backend=StorageBackend.S3,
        cors_origins=["https://handyai.app"],
    )
    assert settings.docs_url is None
    assert settings.openapi_url is None


def test_csv_environment_values_are_split() -> None:
    settings = Settings(
        cors_origins="https://a.test, https://b.test",
        ai_fallback_providers="anthropic,gemini",
    )
    assert settings.cors_origins == ["https://a.test", "https://b.test"]
    assert settings.ai_fallback_providers == [AIProvider.ANTHROPIC, AIProvider.GEMINI]


def test_enabled_providers_deduplicates_and_keeps_order() -> None:
    settings = Settings(
        ai_provider=AIProvider.OPENAI,
        ai_fallback_providers=[AIProvider.OPENAI, AIProvider.ANTHROPIC],
    )
    assert settings.enabled_providers == (AIProvider.OPENAI, AIProvider.ANTHROPIC)


def test_invalid_log_level_is_rejected() -> None:
    with pytest.raises(ValueError, match="log_level"):
        Settings(log_level="chatty")


# ------------------------------------------------------------------ errors


def test_envelope_shape_matches_the_api_contract() -> None:
    error = ConfidenceTooLow(details={"missing_fields": ["wall.material"]})
    envelope = error.to_envelope("req-123")["error"]

    assert envelope == {
        "code": "confidence_too_low",
        "message": error.message,
        "details": {"missing_fields": ["wall.material"]},
        "request_id": "req-123",
        "retryable": True,
    }


def test_diagnostics_never_reach_the_envelope() -> None:
    error = ProviderContractError(log_detail="openai returned: 500 internal, trace abc")
    serialised = json.dumps(error.to_envelope("req-1"))

    assert "openai" not in serialised
    assert "trace" not in serialised


def test_rate_limited_carries_retry_after() -> None:
    error = RateLimited(retry_after_seconds=42)
    assert error.details["retry_after_seconds"] == 42
    assert error.retry_after_seconds == 42


def test_every_error_code_is_used_by_exactly_one_class() -> None:
    """A duplicated code would make client-side handling ambiguous."""
    subclasses: list[type[AppError]] = []
    stack = [AppError]
    while stack:
        current = stack.pop()
        for subclass in current.__subclasses__():
            subclasses.append(subclass)
            stack.append(subclass)

    codes = [subclass.code for subclass in subclasses]
    assert len(codes) == len(set(codes)), "duplicate ErrorCode across AppError subclasses"
    assert ErrorCode.INTERNAL_ERROR in set(codes)


def test_user_messages_are_not_technical() -> None:
    forbidden = ("traceback", "exception", "null", "sql", "500")
    stack = [AppError]
    while stack:
        current = stack.pop()
        for subclass in current.__subclasses__():
            stack.append(subclass)
            lowered = subclass.message.lower()
            assert not any(word in lowered for word in forbidden), subclass.__name__


# ------------------------------------------------------------------ logging


@pytest.mark.parametrize(
    ("raw", "must_not_contain"),
    [
        ("Authorization: Bearer abc.def.ghi", "abc.def.ghi"),
        ("key sk-abcdefghijklmnopqrst", "sk-abcdefghijklmnopqrst"),
        ("user someone@example.com signed in", "someone@example.com"),
        ("data:image/jpeg;base64,AAAABBBBCCCC", "AAAABBBBCCCC"),
    ],
)
def test_redaction_removes_sensitive_substrings(raw: str, must_not_contain: str) -> None:
    assert must_not_contain not in redact(raw)


def test_formatter_redacts_sensitive_extra_keys() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="calling provider",
        args=(),
        exc_info=None,
    )
    record.prompt = "the user's kitchen photo description"
    record.access_token = "eyJhbGciOi.payload.signature"
    record.image_bytes = b"\x00" * 2048
    record.provider = "openai"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["prompt"] == "[redacted]"
    assert payload["access_token"] == "[redacted]"
    assert payload["image_bytes"] == "[redacted]"
    assert payload["provider"] == "openai"
    assert payload["level"] == "INFO"


def test_formatter_summarises_raw_bytes() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="stored",
        args=(),
        exc_info=None,
    )
    record.payload = b"\x00" * 10
    payload = json.loads(JsonFormatter().format(record))
    assert payload["payload"] == "[10 bytes]"


# ------------------------------------------------------------------ security


def test_password_round_trip() -> None:
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed) is True
    assert verify_password("wrong horse battery staple", hashed) is False


def test_short_passwords_are_rejected() -> None:
    with pytest.raises(ValueError, match=str(MIN_PASSWORD_LENGTH)):
        hash_password("short")


def test_long_passphrases_are_accepted() -> None:
    """No maximum length: a passphrase is a good password."""
    assert verify_password("x" * 200, hash_password("x" * 200)) is True


def test_verify_password_rejects_a_malformed_hash() -> None:
    assert verify_password("anything", "not-a-hash") is False


def test_access_token_round_trip() -> None:
    service = TokenService("secret" * 8)
    user_id = uuid.uuid4()

    pair = service.issue_pair(user_id, role="pro")
    claims = service.verify(pair.access_token, TokenType.ACCESS)

    assert claims.subject == user_id
    assert claims.role == "pro"
    assert claims.expires_at > datetime.now(UTC)


def test_refresh_token_cannot_be_used_as_an_access_token() -> None:
    service = TokenService("secret" * 8)
    pair = service.issue_pair(uuid.uuid4())

    with pytest.raises(Exception, match="sign in"):
        service.verify(pair.refresh_token, TokenType.ACCESS)


def test_rotation_keeps_the_family_and_increments_the_generation() -> None:
    service = TokenService("secret" * 8)
    first = service.issue_pair(uuid.uuid4())

    claims = service.verify(first.refresh_token, TokenType.REFRESH)
    second = service.rotate(claims)

    assert second.refresh_family_id == first.refresh_family_id
    assert second.refresh_generation == first.refresh_generation + 1


def test_expired_token_reports_token_expired() -> None:
    service = TokenService("secret" * 8, access_ttl_minutes=1)
    pair = service.issue_pair(uuid.uuid4())
    # Re-verify with a service whose clock is effectively ahead by expiring the TTL.
    expired = TokenService("secret" * 8)
    expired._access_ttl = timedelta(minutes=-5)
    stale = expired.issue_pair(uuid.uuid4())

    from app.core.errors import TokenExpired

    with pytest.raises(TokenExpired):
        service.verify(stale.access_token, TokenType.ACCESS)
    assert service.verify(pair.access_token, TokenType.ACCESS) is not None


def test_token_signed_with_another_secret_is_rejected() -> None:
    issued = TokenService("secret-a" * 6).issue_pair(uuid.uuid4())

    from app.core.errors import Unauthenticated

    with pytest.raises(Unauthenticated):
        TokenService("secret-b" * 6).verify(issued.access_token, TokenType.ACCESS)


def test_opaque_tokens_are_stored_hashed() -> None:
    assert hash_opaque_token("reset-token") != "reset-token"
    assert hash_opaque_token("reset-token") == hash_opaque_token("reset-token")


# ------------------------------------------------------------------ rate limiting


async def test_bucket_allows_up_to_capacity_then_refuses() -> None:
    limiter = InMemoryRateLimiter()
    capacity = LIMITS[Bucket.AUTH].capacity

    for _ in range(capacity):
        assert (await limiter.check(Bucket.AUTH, "1.2.3.4")).allowed is True

    refused = await limiter.check(Bucket.AUTH, "1.2.3.4")
    assert refused.allowed is False
    assert refused.retry_after_seconds > 0


async def test_buckets_and_identities_are_independent() -> None:
    limiter = InMemoryRateLimiter()
    for _ in range(LIMITS[Bucket.AUTH].capacity):
        await limiter.check(Bucket.AUTH, "same")

    assert (await limiter.check(Bucket.AUTH, "different")).allowed is True
    assert (await limiter.check(Bucket.CHAT, "same")).allowed is True


async def test_tokens_refill_over_time() -> None:
    now = [1000.0]
    limiter = InMemoryRateLimiter(clock=lambda: now[0])
    for _ in range(LIMITS[Bucket.CHAT].capacity):
        await limiter.check(Bucket.CHAT, "user")
    assert (await limiter.check(Bucket.CHAT, "user")).allowed is False

    now[0] += LIMITS[Bucket.CHAT].window_seconds

    assert (await limiter.check(Bucket.CHAT, "user")).allowed is True


def test_every_bucket_has_a_declared_limit() -> None:
    assert set(LIMITS) == set(Bucket)


# ------------------------------------------------------------------ value objects


def test_confidence_rejects_out_of_range_values() -> None:
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        Confidence(1.5)


def test_bounding_box_rejects_geometry_outside_the_image() -> None:
    with pytest.raises(ValueError, match="beyond the image"):
        BoundingBox(0.8, 0.1, 0.5, 0.2)


def test_bounding_box_converts_to_pixels() -> None:
    box = BoundingBox(0.25, 0.5, 0.25, 0.25)
    assert box.to_pixels(400, 200) == (100, 100, 200, 150)


def test_measurement_converts_and_formats_per_unit_system() -> None:
    assert Measurement(2.0, "in").to_si() == Measurement(50.8, "mm")
    assert Measurement(15.0, "kg").format_for(UnitSystem.IMPERIAL).endswith("lb")
    assert Measurement(12.5, "mm").format_for(UnitSystem.METRIC) == "12.5 mm"


def test_measurement_rejects_unknown_units() -> None:
    with pytest.raises(ValueError, match="unsupported unit"):
        Measurement(1.0, "cubits")


def test_load_class_bands() -> None:
    assert LoadClass.for_kilograms(4) is LoadClass.LIGHT
    assert LoadClass.for_kilograms(15) is LoadClass.MEDIUM
    assert LoadClass.for_kilograms(40) is LoadClass.HEAVY
    assert LoadClass.for_kilograms(41) is LoadClass.VERY_HEAVY


def test_cost_is_decimal_and_never_negative() -> None:
    from decimal import Decimal

    assert (Cost(Decimal("0.10")) + Cost(Decimal("0.20"))).amount_usd == Decimal("0.30")
    assert Cost(Decimal("1.01")).exceeds(1.00) is True
    with pytest.raises(ValueError, match="negative"):
        Cost(Decimal("-1"))
