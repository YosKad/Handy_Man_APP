"""Composition root.

The one place adapters are bound to ports. Everything else receives its
dependencies; nothing constructs its own infrastructure, and there are no
import-time singletons or service locators.

Built once during application startup and closed on shutdown.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self

from app.core.config import AIProvider, Settings
from app.core.errors import InternalError
from app.core.logging import get_logger
from app.core.ratelimit import InMemoryRateLimiter, RateLimiterPort, RedisRateLimiter
from app.core.security import TokenService
from app.features.safety.domain.rules import SafetyClassifier
from app.infrastructure.ai.adapters.fake import FakeAdapter
from app.infrastructure.ai.gateway import AIGateway, GatewayConfig
from app.infrastructure.ai.models import AIAdapter, AiCallLedger

logger = get_logger(__name__)


@dataclass(slots=True)
class Container:
    settings: Settings
    tokens: TokenService
    rate_limiter: RateLimiterPort
    ai: AIGateway
    safety: SafetyClassifier

    @classmethod
    def build(
        cls,
        settings: Settings,
        *,
        rate_limiter: RateLimiterPort | None = None,
        ai_ledger: AiCallLedger | None = None,
    ) -> Self:
        return cls(
            settings=settings,
            tokens=TokenService(
                settings.secret_key,
                access_ttl_minutes=settings.access_token_ttl_minutes,
                refresh_ttl_days=settings.refresh_token_ttl_days,
            ),
            rate_limiter=rate_limiter or _build_rate_limiter(settings),
            ai=AIGateway(
                adapters=_build_ai_adapters(settings),
                config=GatewayConfig(
                    timeout_seconds=settings.ai_request_timeout_seconds,
                    max_retries=settings.ai_max_retries,
                    cost_ceiling_usd=settings.ai_cost_ceiling_usd,
                ),
                ledger=ai_ledger,
            ),
            safety=SafetyClassifier(),
        )

    async def aclose(self) -> None:
        """Release infrastructure held by the container."""
        closer = getattr(self.rate_limiter, "aclose", None)
        if callable(closer):
            await closer()


def _build_rate_limiter(settings: Settings) -> RateLimiterPort:
    if settings.is_testing:
        return InMemoryRateLimiter()
    # Imported lazily so unit tests never require a Redis client at import time.
    from redis.asyncio import Redis

    return RedisRateLimiter(Redis.from_url(settings.redis_url, decode_responses=False))


def _build_ai_adapters(settings: Settings) -> tuple[AIAdapter, ...]:
    """Instantiate the configured provider and its fallbacks, in order.

    Adapters are imported inside the branch so a deployment only needs the SDK
    for the providers it actually uses — and so an unused SDK cannot break
    startup.
    """
    adapters: list[AIAdapter] = []
    for provider in settings.enabled_providers:
        if provider is AIProvider.FAKE:
            adapters.append(FakeAdapter())
        elif provider is AIProvider.OPENAI:
            from app.infrastructure.ai.adapters.openai_adapter import OpenAIAdapter

            adapters.append(
                OpenAIAdapter(
                    api_key=_require(settings.openai_api_key, "OPENAI_API_KEY"),
                    vision_model=settings.openai_vision_model,
                    text_model=settings.openai_text_model,
                    embedding_model=settings.openai_embedding_model,
                    max_edge_px=settings.ai_vision_max_edge_px,
                )
            )
        else:
            # Anthropic and Gemini adapters land in Phase 3; the gateway already
            # routes to them by capability once they exist.
            raise InternalError(
                log_detail=f"AI provider {provider.value} is configured but not yet implemented"
            )

    logger.info(
        "ai adapters configured",
        extra={"providers": [adapter.name for adapter in adapters]},
    )
    return tuple(adapters)


def _require(value: str | None, name: str) -> str:
    if not value:
        raise InternalError(log_detail=f"{name} is required for the configured AI provider")
    return value
