"""The ordered fallback chain across inference tiers.

Tier order is GPU, then CPU, then the hosted Qwen API. Each tier has its own
breaker, so one failing tier does not slow the others, and the tier that
answered is recorded on the task — a batch that spans tiers must never be
presented as though every card took the same path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.logging import get_logger
from app.models.enums import ProviderTier
from app.vlm.circuit_breaker import CircuitBreaker
from app.vlm.openai_compat import OpenAICompatProvider
from app.vlm.provider import VLMError

if TYPE_CHECKING:
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


class ProviderChain:
    """Tries each enabled tier in order until one returns a valid extraction."""

    def __init__(self, providers: list[VLMProvider], breakers: dict[str, CircuitBreaker]) -> None:
        self._providers = providers
        self._breakers = breakers

    @classmethod
    def from_settings(cls, settings: Settings) -> ProviderChain:
        providers: list[VLMProvider] = []
        breakers: dict[str, CircuitBreaker] = {}

        for name, config in settings.provider_chain():
            if not config.enabled:
                log.info("tier_disabled", tier=name)
                continue
            tier = ProviderTier(name)
            providers.append(
                OpenAICompatProvider(
                    tier=tier,
                    base_url=config.base_url,
                    model=config.model,
                    api_key=config.api_key,
                    timeout_s=config.timeout_s,
                    # Only the self-hosted tiers compile the schema into a
                    # grammar; hosted endpoints vary, so the client degrades.
                    supports_json_schema=tier is not ProviderTier.CLOUD,
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
        return cls(providers, breakers)

    @property
    def tiers(self) -> list[str]:
        return [p.tier.value for p in self._providers]

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
        """Per-tier readiness, used by the readiness probe and /stats."""
        return {p.tier.value: await p.health() for p in self._providers}

    def breaker_snapshot(self) -> list[dict[str, object]]:
        return [b.snapshot() for b in self._breakers.values()]

    async def aclose(self) -> None:
        for provider in self._providers:
            await provider.aclose()
