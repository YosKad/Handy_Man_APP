"""Application error hierarchy.

Every failure the client can see is an ``AppError``. It carries a stable
machine-readable ``code``, a user-presentable ``message``, and structured
``details`` — exactly the envelope specified in ``docs/04-api-design.md`` §2.

Two rules that must not erode:

1. ``message`` is written for a person holding a drill, not for an engineer.
2. Provider text, SQL, stack traces and internal hostnames never reach the
   client. Diagnostics go to ``log_detail``, which is logged and dropped.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    # 400
    VALIDATION_ERROR = "validation_error"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    IMAGE_TOO_LARGE = "image_too_large"
    # 401
    UNAUTHENTICATED = "unauthenticated"
    TOKEN_EXPIRED = "token_expired"  # noqa: S105 - an error code, not a token
    # 403
    FORBIDDEN = "forbidden"
    CONSENT_REQUIRED = "consent_required"
    ENTITLEMENT_REQUIRED = "entitlement_required"
    # 404
    NOT_FOUND = "not_found"
    # 409
    CONFLICT = "conflict"
    GUIDE_IMMUTABLE = "guide_immutable"
    # 422
    CONFIDENCE_TOO_LOW = "confidence_too_low"
    CLARIFICATION_REQUIRED = "clarification_required"
    SAFETY_BLOCKED = "safety_blocked"
    # 429
    RATE_LIMITED = "rate_limited"
    QUOTA_EXCEEDED = "quota_exceeded"
    # 500
    INTERNAL_ERROR = "internal_error"
    # 502 / 503
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_CONTRACT_ERROR = "provider_contract_error"
    COST_CEILING_EXCEEDED = "cost_ceiling_exceeded"


class AppError(Exception):
    """Base class for all expected failures."""

    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    status_code: int = 500
    message: str = "Something went wrong on our side. Please try again."
    retryable: bool = False

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        log_detail: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        self.message = message or self.message
        self.details = details or {}
        # Diagnostics: logged, never serialised to the client.
        self.log_detail = log_detail
        if retryable is not None:
            self.retryable = retryable
        super().__init__(self.message)

    def to_envelope(self, request_id: str | None = None) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code.value,
                "message": self.message,
                "details": self.details,
                "request_id": request_id,
                "retryable": self.retryable,
            }
        }


# ------------------------------------------------------------------ 4xx


class ValidationError(AppError):
    code = ErrorCode.VALIDATION_ERROR
    status_code = 400
    message = "Some of that information didn't look right. Please check and try again."


class UnsupportedMediaType(AppError):
    code = ErrorCode.UNSUPPORTED_MEDIA_TYPE
    status_code = 400
    message = "We can't read that file type. Please use a JPEG, PNG or HEIC photo."


class ImageTooLarge(AppError):
    code = ErrorCode.IMAGE_TOO_LARGE
    status_code = 400
    message = "That photo is too large. Please try a smaller one."


class Unauthenticated(AppError):
    code = ErrorCode.UNAUTHENTICATED
    status_code = 401
    message = "Please sign in to continue."


class TokenExpired(AppError):
    code = ErrorCode.TOKEN_EXPIRED
    status_code = 401
    message = "Your session expired. Please sign in again."
    retryable = True


class Forbidden(AppError):
    code = ErrorCode.FORBIDDEN
    status_code = 403
    message = "You don't have access to that."


class ConsentRequired(AppError):
    code = ErrorCode.CONSENT_REQUIRED
    status_code = 403
    message = "We need your permission to process photos before we can continue."


class EntitlementRequired(AppError):
    code = ErrorCode.ENTITLEMENT_REQUIRED
    status_code = 403
    message = "You've used your included guides for now."


class NotFound(AppError):
    code = ErrorCode.NOT_FOUND
    status_code = 404
    message = "We couldn't find that."


class Conflict(AppError):
    code = ErrorCode.CONFLICT
    status_code = 409
    message = "That's already been done."


class GuideImmutable(AppError):
    code = ErrorCode.GUIDE_IMMUTABLE
    status_code = 409
    message = "You've already started this guide, so we kept it as it was."


class ConfidenceTooLow(AppError):
    """A safety-critical field could not be determined with enough confidence.

    ``details["missing_fields"]`` names them so the app can render a photo
    request with the reason attached.
    """

    code = ErrorCode.CONFIDENCE_TOO_LOW
    status_code = 422
    message = "We need one more photo to be sure before we give you instructions."
    retryable = True


class ClarificationRequired(AppError):
    code = ErrorCode.CLARIFICATION_REQUIRED
    status_code = 422
    message = "A couple of quick questions first."
    retryable = True


class SafetyBlocked(AppError):
    """The task is on a hard red line — step-by-step execution is withheld."""

    code = ErrorCode.SAFETY_BLOCKED
    status_code = 422
    message = "This job needs a qualified professional. Here's what to ask them."


class RateLimited(AppError):
    code = ErrorCode.RATE_LIMITED
    status_code = 429
    message = "That's a lot of requests. Please wait a moment and try again."
    retryable = True

    def __init__(self, retry_after_seconds: int, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.retry_after_seconds = retry_after_seconds
        self.details.setdefault("retry_after_seconds", retry_after_seconds)


class QuotaExceeded(AppError):
    code = ErrorCode.QUOTA_EXCEEDED
    status_code = 429
    message = "You've reached today's limit. It resets tomorrow."


# ------------------------------------------------------------------ 5xx


class InternalError(AppError):
    code = ErrorCode.INTERNAL_ERROR
    status_code = 500
    retryable = True


class ProviderUnavailable(AppError):
    """Every configured AI provider failed or timed out."""

    code = ErrorCode.PROVIDER_UNAVAILABLE
    status_code = 503
    message = "Our assistant is briefly unavailable. We'll keep your photos and retry."
    retryable = True


class ProviderContractError(AppError):
    """A provider returned output that does not satisfy the domain schema.

    Raised by adapters. Never surfaced as-is: the gateway retries once with the
    validation errors fed back before giving up.
    """

    code = ErrorCode.PROVIDER_CONTRACT_ERROR
    status_code = 502
    message = "We couldn't put together a reliable guide this time. Please try again."
    retryable = True


class CostCeilingExceeded(AppError):
    code = ErrorCode.COST_CEILING_EXCEEDED
    status_code = 503
    message = "This request was larger than we can process. Try fewer photos."
