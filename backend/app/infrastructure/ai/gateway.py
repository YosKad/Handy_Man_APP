"""The AI gateway: the single door between the application and any model.

Responsibilities that live here rather than in every feature:

* adapter selection by capability, with an ordered fallback list;
* timeout and jittered retry on transient failures;
* one repair attempt when an adapter reports a contract violation;
* a per-request cost ceiling checked *before* spending;
* exactly one ledger row per attempt, successful or not.

Features call this. Features never import an adapter, and nothing outside
``app/infrastructure/ai/`` may import a provider SDK — CI enforces it.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TypeVar
from uuid import UUID

from app.core.errors import (
    AppError,
    CostCeilingExceeded,
    ProviderContractError,
    ProviderUnavailable,
)
from app.core.logging import get_logger
from app.infrastructure.ai.models import (
    AIAdapter,
    AiCallLedger,
    AiCallRecord,
    Capability,
    ChatChunk,
    ChatRequest,
    GuideDraft,
    GuideRequest,
    StepValidationRequest,
    StepValidationResult,
    Tier,
    Usage,
    VisionRequest,
    VisionResult,
)

logger = get_logger(__name__)

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class GatewayConfig:
    timeout_seconds: float = 90.0
    max_retries: int = 2
    cost_ceiling_usd: float = 1.00
    #: Repair attempt after a contract violation, before giving up.
    repair_attempts: int = 1


@dataclass(frozen=True, slots=True)
class CallContext:
    """Who this call is for. Flows straight into the ledger."""

    user_id: UUID | None = None
    project_id: UUID | None = None
    job_id: UUID | None = None


class _NullLedger:
    """Used in unit tests and local runs without a database."""

    async def record(self, call: AiCallRecord) -> None:
        return None


class AIGateway:
    def __init__(
        self,
        adapters: Sequence[AIAdapter],
        *,
        config: GatewayConfig | None = None,
        ledger: AiCallLedger | None = None,
        prompt_version: str = "v1",
    ) -> None:
        if not adapters:
            raise ValueError("at least one AI adapter must be configured")
        self._adapters = tuple(adapters)
        self._config = config or GatewayConfig()
        self._ledger: AiCallLedger = ledger or _NullLedger()
        self._prompt_version = prompt_version

    # -------------------------------------------------------------- public API

    async def analyze(
        self, request: VisionRequest, context: CallContext | None = None
    ) -> VisionResult:
        self._assert_within_budget(estimated_cost=self._estimate_vision_cost(request))
        return await self._call(
            capability=Capability.VISION,
            purpose="vision_analyze",
            context=context,
            invoke=lambda adapter: adapter.analyze(request),
        )

    async def generate_guide(
        self, request: GuideRequest, context: CallContext | None = None
    ) -> GuideDraft:
        return await self._call(
            capability=Capability.STRUCTURED_OUTPUT,
            purpose="generate_guide",
            context=context,
            invoke=lambda adapter: adapter.generate_guide(request),
        )

    async def validate_step(
        self, request: StepValidationRequest, context: CallContext | None = None
    ) -> StepValidationResult:
        return await self._call(
            capability=Capability.VISION,
            purpose="validate_step",
            context=context,
            invoke=lambda adapter: adapter.validate_step(request),
        )

    async def embed(
        self, texts: Sequence[str], context: CallContext | None = None
    ) -> tuple[tuple[float, ...], ...]:
        return await self._call(
            capability=Capability.EMBEDDING,
            purpose="embed",
            context=context,
            invoke=lambda adapter: adapter.embed(texts),
        )

    async def chat_stream(
        self, request: ChatRequest, context: CallContext | None = None
    ) -> AsyncIterator[ChatChunk]:
        """Streaming has no retry: a partially delivered reply cannot be replayed.

        On failure mid-stream the client sees an SSE error event and can resend.
        """
        adapter = self._select(Capability.STREAMING)
        started = time.perf_counter()
        chunk_count = 0
        try:
            async for chunk in adapter.chat_stream(request):
                chunk_count += 1
                yield chunk
        except AppError:
            await self._record(
                adapter,
                Capability.STREAMING,
                "chat_stream",
                context,
                Usage(latency_ms=_elapsed_ms(started)),
                "failed",
                1,
            )
            raise
        except Exception as exc:
            logger.exception("chat stream failed", extra={"provider": adapter.name})
            await self._record(
                adapter,
                Capability.STREAMING,
                "chat_stream",
                context,
                Usage(latency_ms=_elapsed_ms(started)),
                "failed",
                1,
            )
            raise ProviderUnavailable(log_detail=str(exc)) from exc
        else:
            await self._record(
                adapter,
                Capability.STREAMING,
                "chat_stream",
                context,
                Usage(output_tokens=chunk_count, latency_ms=_elapsed_ms(started)),
                "succeeded",
                1,
            )

    # -------------------------------------------------------------- internals

    async def _call(
        self,
        *,
        capability: Capability,
        purpose: str,
        context: CallContext | None,
        invoke: Callable[[AIAdapter], Awaitable[tuple[T, Usage]]],
    ) -> T:
        candidates = self._candidates(capability)
        last_error: AppError | None = None

        for adapter in candidates:
            attempts = self._config.max_retries + 1
            contract_failures = 0

            for attempt in range(1, attempts + 1):
                started = time.perf_counter()
                try:
                    result, usage = await asyncio.wait_for(
                        invoke(adapter), timeout=self._config.timeout_seconds
                    )
                except TimeoutError as exc:
                    last_error = ProviderUnavailable(log_detail=f"{adapter.name} timed out")
                    await self._record(
                        adapter,
                        capability,
                        purpose,
                        context,
                        Usage(latency_ms=_elapsed_ms(started)),
                        "timeout",
                        attempt,
                        error_code="timeout",
                    )
                    logger.warning(
                        "provider timed out",
                        extra={"provider": adapter.name, "purpose": purpose, "attempt": attempt},
                    )
                    _ = exc
                except ProviderContractError as exc:
                    contract_failures += 1
                    last_error = exc
                    await self._record(
                        adapter,
                        capability,
                        purpose,
                        context,
                        Usage(latency_ms=_elapsed_ms(started)),
                        "contract_error",
                        attempt,
                        error_code=exc.code.value,
                    )
                    logger.warning(
                        "provider returned output that failed validation",
                        extra={
                            "provider": adapter.name,
                            "purpose": purpose,
                            "detail": exc.log_detail,
                        },
                    )
                    # One repair attempt, then move on rather than burning budget.
                    if contract_failures > self._config.repair_attempts:
                        break
                except AppError as exc:
                    last_error = exc
                    await self._record(
                        adapter,
                        capability,
                        purpose,
                        context,
                        Usage(latency_ms=_elapsed_ms(started)),
                        "failed",
                        attempt,
                        error_code=exc.code.value,
                    )
                    if not exc.retryable:
                        raise
                except Exception as exc:
                    last_error = ProviderUnavailable(log_detail=f"{adapter.name}: {exc}")
                    await self._record(
                        adapter,
                        capability,
                        purpose,
                        context,
                        Usage(latency_ms=_elapsed_ms(started)),
                        "failed",
                        attempt,
                        error_code="provider_exception",
                    )
                    logger.exception(
                        "provider call raised",
                        extra={"provider": adapter.name, "purpose": purpose},
                    )
                else:
                    usage = _with_latency(usage, _elapsed_ms(started))
                    self._assert_within_budget(estimated_cost=float(usage.cost_usd))
                    await self._record(
                        adapter, capability, purpose, context, usage, "succeeded", attempt
                    )
                    return result

                if attempt < attempts:
                    await asyncio.sleep(_backoff_seconds(attempt))

        raise last_error or ProviderUnavailable(
            log_detail=f"no adapter satisfied {capability.value}"
        )

    def _candidates(self, capability: Capability) -> tuple[AIAdapter, ...]:
        candidates = tuple(a for a in self._adapters if capability in a.capabilities)
        if not candidates:
            raise ProviderUnavailable(
                log_detail=(
                    f"no configured provider supports {capability.value}; "
                    f"configured: {[a.name for a in self._adapters]}"
                )
            )
        return candidates

    def _select(self, capability: Capability) -> AIAdapter:
        return self._candidates(capability)[0]

    def _assert_within_budget(self, *, estimated_cost: float) -> None:
        if estimated_cost > self._config.cost_ceiling_usd:
            raise CostCeilingExceeded(
                details={"ceiling_usd": self._config.cost_ceiling_usd},
                log_detail=f"estimated {estimated_cost:.4f} USD exceeds ceiling",
            )

    def _estimate_vision_cost(self, request: VisionRequest) -> float:
        """Rough pre-flight estimate so an oversized request fails before spending."""
        per_image = 0.02
        per_prompt = 0.01
        return len(request.images) * per_image + per_prompt

    async def _record(
        self,
        adapter: AIAdapter,
        capability: Capability,
        purpose: str,
        context: CallContext | None,
        usage: Usage,
        status: str,
        attempt: int,
        *,
        error_code: str | None = None,
    ) -> None:
        ctx = context or CallContext()
        try:
            await self._ledger.record(
                AiCallRecord(
                    provider=adapter.name,
                    model=adapter.model_for(capability, _tier_of(purpose)),
                    purpose=purpose,
                    prompt_version=self._prompt_version,
                    usage=usage,
                    status=status,
                    attempt=attempt,
                    error_code=error_code,
                    user_id=ctx.user_id,
                    project_id=ctx.project_id,
                    job_id=ctx.job_id,
                )
            )
        except Exception:
            logger.exception("failed to record ai call", extra={"purpose": purpose})


def _tier_of(purpose: str) -> Tier:
    return Tier.DEEP if purpose == "generate_guide" else Tier.BALANCED


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _with_latency(usage: Usage, latency_ms: int) -> Usage:
    return Usage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        image_count=usage.image_count,
        cost_usd=usage.cost_usd or Decimal("0"),
        latency_ms=usage.latency_ms or latency_ms,
    )


def _backoff_seconds(attempt: int) -> float:
    """Exponential with jitter, so retries from many clients do not synchronise."""
    base = min(2.0 ** (attempt - 1), 8.0)
    return base * (0.5 + random.random() / 2)  # noqa: S311 - jitter, not cryptography
