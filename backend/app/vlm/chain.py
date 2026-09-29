"""The ordered fallback chain across inference tiers.

Tier order is GPU, then CPU, then the hosted Qwen API. Each tier has its own
breaker, so one failing tier does not slow the others, and the tier that
answered is recorded on the task — a batch that spans tiers must never be
presented as though every card took the same path.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from app.core.logging import get_logger
from app.models.enums import ProviderTier
from app.services.budget import DailyBudget
from app.vlm.circuit_breaker import CircuitBreaker
from app.vlm.openai_compat import OpenAICompatProvider
from app.vlm.provider import VLMError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.config import Settings
    from app.vlm.provider import VLMProvider, VLMResult

log = get_logger(__name__)


class AllProvidersFailedError(VLMError):
    """Every configured tier declined or failed for this card."""

    def __init__(self, attempts: dict[str, str]) -> None:
        detail = "; ".join(f"{tier}: {error}" for tier, error in attempts.items()) or "none"
        super().__init__(
            f"all inference tiers failed ({detail})",
            tier=ProviderTier.GPU,
            retryable=True,
        )
        self.attempts = attempts


def budget_guard(budget: DailyBudget) -> Callable[[], Awaitable[None]]:
    """A pre-request hook that refuses once the day's hosted budget is spent.

    Not retryable: waiting minutes will not help when the limit resets at
    midnight, and a retry would only ask the database the same question.
    """

    async def take() -> None:
        if not await budget.try_take():
            raise VLMError(
                f"daily limit of {budget.limit} hosted requests reached; it resets at 00:00 UTC",
                tier=ProviderTier.CLOUD,
                retryable=False,
            )

    return take


class ProviderChain:
    """Tries each enabled tier in order until one returns a valid extraction."""

    def __init__(
        self,
        providers: list[VLMProvider],
        breakers: dict[str, CircuitBreaker],
        *,
        health_ttl_s: float = 0.0,
    ) -> None:
        self._providers = providers
        self._breakers = breakers
        # /ready is public. Without a cache every hit would probe every tier,
        # which for a hosted tier means a request to the provider on our key.
        self._health_ttl_s = health_ttl_s
        self._health_cache: tuple[float, dict[str, bool]] | None = None
        self._health_lock = asyncio.Lock()

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> ProviderChain:
        """Build the enabled tiers.

        ``session_factory`` enables the daily ceiling on the hosted tier. The
        worker passes one because it is the only process that runs inference;
        the API builds a chain only to report health.
        """
        providers: list[VLMProvider] = []
        breakers: dict[str, CircuitBreaker] = {}

        for name, config in settings.provider_chain():
            if not config.enabled:
                log.info("tier_disabled", tier=name)
                continue
            tier = ProviderTier(name)
            before_request = None
            limit = settings.vlm_cloud_daily_request_limit
            if tier is ProviderTier.CLOUD and session_factory is not None and limit > 0:
                before_request = budget_guard(DailyBudget("cloud_requests", limit, session_factory))
            providers.append(
                OpenAICompatProvider(
                    tier=tier,
                    base_url=config.base_url,
                    model=config.model,
                    api_key=config.api_key,
                    timeout_s=config.timeout_s,
                    # llama.cpp compiles the schema into a grammar; hosted
                    # endpoints vary, and a 400 degrades to the next rung.
                    supports_json_schema=config.json_schema,
                    max_retries=config.max_retries,
                    before_request=before_request,
                )
            )
            breakers[name] = CircuitBreaker(
                name,
                failure_threshold=settings.breaker_failure_threshold,
                reset_seconds=settings.breaker_reset_seconds,
            )

        if not providers:
            raise ValueError(
                "no inference tier is enabled; set VLM_GPU_ENABLED, "
                "VLM_CPU_ENABLED or VLM_CLOUD_ENABLED (with an API key)"
            )
        return cls(providers, breakers, health_ttl_s=settings.provider_health_interval_s)

    @property
    def tiers(self) -> list[str]:
        return [p.tier.value for p in self._providers]

    @property
    def models(self) -> dict[str, str]:
        """Each tier's primary model. Public: the UI names what reads the cards."""
        return {p.tier.value: p.model for p in self._providers}

    async def extract(self, image_data_url: str) -> VLMResult:
        """Return the first successful extraction, or raise AllProvidersFailedError."""
        attempts: dict[str, str] = {}

        for provider in self._providers:
            name = provider.tier.value
            breaker = self._breakers[name]
            if not breaker.is_available:
                attempts[name] = "circuit breaker open"
                log.info("tier_skipped", tier=name, reason="breaker_open")
                continue

            try:
                result = await provider.extract(image_data_url)
            except VLMError as exc:
                breaker.record_failure()
                attempts[name] = str(exc)
                log.warning("tier_failed", tier=name, error=str(exc)[:300])
                continue
            # A bug in one tier's client must not abort the chain for this card.
            except Exception as exc:
                breaker.record_failure()
                attempts[name] = f"unexpected {type(exc).__name__}: {exc}"
                log.exception("tier_error", tier=name)
                continue

            breaker.record_success()
            if attempts:
                # Worth a log line: the card succeeded only after a fallback.
                log.info("tier_recovered_card", tier=name, failed_tiers=list(attempts))
            return result

        raise AllProvidersFailedError(attempts)

    async def health(self) -> dict[str, bool]:
        """Per-tier readiness, used by the readiness probe and /stats.

        Cached for ``health_ttl_s``; the lock makes a burst of probes share one
        round of requests instead of each starting its own.
        """
        async with self._health_lock:
            now = time.monotonic()
            if self._health_cache and now - self._health_cache[0] < self._health_ttl_s:
                return self._health_cache[1]
            result = {p.tier.value: await p.health() for p in self._providers}
            self._health_cache = (now, result)
            return result

    def breaker_snapshot(self) -> list[dict[str, object]]:
        return [b.snapshot() for b in self._breakers.values()]

    async def aclose(self) -> None:
        for provider in self._providers:
            await provider.aclose()
