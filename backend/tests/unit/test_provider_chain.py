"""Fallback behaviour across inference tiers.

These tests inject failures rather than calling a model, because the property
that matters is the order and bookkeeping of the chain: which tier answered,
what happens when one is down, and whether a dead tier keeps costing every
card a full timeout.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.models.enums import OutputMode, ProviderTier
from app.schemas.extraction import CardExtraction
from app.vlm.chain import AllProvidersFailedError, ProviderChain
from app.vlm.circuit_breaker import BreakerState, CircuitBreaker
from app.vlm.provider import VLMError, VLMResult


class FakeProvider:
    """A tier that succeeds, fails, or explodes, and counts its calls."""

    def __init__(
        self,
        tier: ProviderTier,
        *,
        fail_with: Exception | None = None,
        healthy: bool = True,
    ) -> None:
        self.tier = tier
        self.model = f"fake-{tier.value}"
        self._fail_with = fail_with
        self._healthy = healthy
        self.calls = 0
        self.closed = False

    async def extract(self, image_data_url: str) -> VLMResult:  # noqa: ARG002
        self.calls += 1
        if self._fail_with is not None:
            raise self._fail_with
        return VLMResult(
            extraction=CardExtraction(raw_text="card", company="Acme"),
            tier=self.tier,
            model=self.model,
            output_mode=OutputMode.JSON_SCHEMA,
            latency_ms=5,
        )

    async def health(self) -> bool:
        return self._healthy

    async def aclose(self) -> None:
        self.closed = True


def build_chain(*providers: FakeProvider, threshold: int = 3) -> tuple[ProviderChain, dict]:
    breakers = {
        p.tier.value: CircuitBreaker(p.tier.value, failure_threshold=threshold)
        for p in providers
    }
    return ProviderChain(list(providers), breakers), breakers


class TestFallbackOrder:
    async def test_first_healthy_tier_answers_and_others_are_untouched(self) -> None:
        gpu = FakeProvider(ProviderTier.GPU)
        cpu = FakeProvider(ProviderTier.CPU)
        chain, _ = build_chain(gpu, cpu)

        result = await chain.extract("data:image/jpeg;base64,x")

        assert result.tier is ProviderTier.GPU
        assert (gpu.calls, cpu.calls) == (1, 0)

    async def test_failure_falls_through_to_the_next_tier(self) -> None:
        gpu = FakeProvider(
            ProviderTier.GPU, fail_with=VLMError("gpu down", tier=ProviderTier.GPU)
        )
        cpu = FakeProvider(ProviderTier.CPU)
        cloud = FakeProvider(ProviderTier.CLOUD)
        chain, _ = build_chain(gpu, cpu, cloud)

        result = await chain.extract("data:image/jpeg;base64,x")

        assert result.tier is ProviderTier.CPU
        assert (gpu.calls, cpu.calls, cloud.calls) == (1, 1, 0)

    async def test_chain_reaches_the_hosted_tier_when_both_local_tiers_fail(self) -> None:
        gpu = FakeProvider(
            ProviderTier.GPU, fail_with=VLMError("gpu down", tier=ProviderTier.GPU)
        )
        cpu = FakeProvider(
            ProviderTier.CPU, fail_with=VLMError("cpu down", tier=ProviderTier.CPU)
        )
        cloud = FakeProvider(ProviderTier.CLOUD)
        chain, _ = build_chain(gpu, cpu, cloud)

        result = await chain.extract("data:image/jpeg;base64,x")

        assert result.tier is ProviderTier.CLOUD

    async def test_an_unexpected_exception_does_not_abort_the_chain(self) -> None:
        """A bug in one tier's client must not cost the card."""
        gpu = FakeProvider(ProviderTier.GPU, fail_with=RuntimeError("boom"))
        cpu = FakeProvider(ProviderTier.CPU)
        chain, _ = build_chain(gpu, cpu)

        result = await chain.extract("data:image/jpeg;base64,x")

        assert result.tier is ProviderTier.CPU

    async def test_every_tier_failing_reports_each_reason(self) -> None:
        gpu = FakeProvider(
            ProviderTier.GPU, fail_with=VLMError("gpu timeout", tier=ProviderTier.GPU)
        )
        cpu = FakeProvider(
            ProviderTier.CPU, fail_with=VLMError("cpu oom", tier=ProviderTier.CPU)
        )
        chain, _ = build_chain(gpu, cpu)

        with pytest.raises(AllProvidersFailedError) as exc_info:
            await chain.extract("data:image/jpeg;base64,x")

        # The per-tier reasons are what makes a failed card diagnosable.
        assert set(exc_info.value.attempts) == {"gpu", "cpu"}
        assert "gpu timeout" in exc_info.value.attempts["gpu"]
        assert "cpu oom" in exc_info.value.attempts["cpu"]


class TestBreakerIntegration:
    async def test_a_dead_tier_stops_being_called(self) -> None:
        """This is the point of the breaker.

        Without it, a dead GPU container costs every card in the batch a full
        timeout before falling through — fifty cards would rediscover the same
        failure fifty times.
        """
        gpu = FakeProvider(
            ProviderTier.GPU, fail_with=VLMError("gpu down", tier=ProviderTier.GPU)
        )
        cpu = FakeProvider(ProviderTier.CPU)
        chain, breakers = build_chain(gpu, cpu, threshold=3)

        for _ in range(10):
            await chain.extract("data:image/jpeg;base64,x")

        assert breakers["gpu"].state is BreakerState.OPEN
        # Called only until the breaker tripped, not once per card.
        assert gpu.calls == 3
        assert cpu.calls == 10

    async def test_a_recovered_tier_is_used_again(self) -> None:
        gpu = FakeProvider(ProviderTier.GPU)
        chain, breakers = build_chain(gpu, threshold=2)
        breakers["gpu"].record_failure()
        breakers["gpu"].record_failure()
        assert breakers["gpu"].state is BreakerState.OPEN

        # A successful probe closes the breaker again.
        breakers["gpu"].record_success()
        result = await chain.extract("data:image/jpeg;base64,x")

        assert result.tier is ProviderTier.GPU
        assert breakers["gpu"].state is BreakerState.CLOSED

    async def test_an_open_breaker_is_reported_as_the_reason(self) -> None:
        gpu = FakeProvider(ProviderTier.GPU)
        chain, breakers = build_chain(gpu, threshold=1)
        breakers["gpu"].record_failure()

        with pytest.raises(AllProvidersFailedError) as exc_info:
            await chain.extract("data:image/jpeg;base64,x")

        assert "circuit breaker open" in exc_info.value.attempts["gpu"]
        assert gpu.calls == 0


class TestChainConstruction:
    def test_cloud_tier_without_a_key_is_not_enabled(self) -> None:
        """A missing secret must degrade, not fail every card."""
        chain = ProviderChain.from_settings(Settings(vlm_cloud_api_key=""))
        assert chain.tiers == ["gpu", "cpu"]

    def test_cloud_tier_with_a_key_joins_the_chain_last(self) -> None:
        chain = ProviderChain.from_settings(Settings(vlm_cloud_api_key="test-key"))
        assert chain.tiers == ["gpu", "cpu", "cloud"]

    def test_disabling_every_tier_fails_loudly_at_startup(self) -> None:
        """Better to refuse to boot than to accept uploads nothing can process."""
        with pytest.raises(ValueError, match="no inference tier is enabled"):
            ProviderChain.from_settings(
                Settings(
                    vlm_gpu_enabled=False,
                    vlm_cpu_enabled=False,
                    vlm_cloud_enabled=False,
                )
            )

    async def test_closing_the_chain_closes_every_provider(self) -> None:
        gpu, cpu = FakeProvider(ProviderTier.GPU), FakeProvider(ProviderTier.CPU)
        chain, _ = build_chain(gpu, cpu)
        await chain.aclose()
        assert gpu.closed and cpu.closed


class TestHealth:
    async def test_health_reports_each_tier(self) -> None:
        gpu = FakeProvider(ProviderTier.GPU, healthy=False)
        cpu = FakeProvider(ProviderTier.CPU, healthy=True)
        chain, _ = build_chain(gpu, cpu)
        assert await chain.health() == {"gpu": False, "cpu": True}
