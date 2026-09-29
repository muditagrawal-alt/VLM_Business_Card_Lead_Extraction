"""Guardrails for the hosted tier: retries, model failover and the daily ceiling.

Free hosted tiers fail in ways a local model server does not. Measured against
the Gemini free tier while this was written: roughly one request in seven came
back 429 or 503, and the first model name tried had been retired outright
(404). These tests pin the behaviour that keeps the tier usable anyway, and the
ceiling that stops a stranger with the public URL from spending the key's quota.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.models.enums import OutputMode, ProviderTier
from app.services.budget import DailyBudget
from app.vlm.chain import budget_guard
from app.vlm.openai_compat import OpenAICompatProvider
from app.vlm.provider import VLMError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession

CARD = {"raw_text": "Priya Raghavan Meridian Logistics", "first_name": "Priya"}

Scripted = tuple[int, dict[str, object], dict[str, str] | None]


def ok() -> Scripted:
    return 200, {"choices": [{"message": {"content": json.dumps(CARD)}}]}, None


def fail(status: int, headers: dict[str, str] | None = None) -> Scripted:
    return status, {"error": {"code": status}}, headers


class Script:
    """Answers each request with the next scripted response, recording models."""

    def __init__(self, *responses: Scripted) -> None:
        self._responses = list(responses)
        self.models: list[str] = []
        self.formats: list[str | None] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.models.append(body["model"])
        self.formats.append((body.get("response_format") or {}).get("type"))
        status, payload, headers = self._responses.pop(0)
        return httpx.Response(status, json=payload, headers=headers or {})


def provider(
    script: Script,
    *,
    model: str = "primary",
    retries: int = 2,
    json_schema: bool = False,
    before_request: Callable[[], object] | None = None,
) -> OpenAICompatProvider:
    p = OpenAICompatProvider(
        tier=ProviderTier.CLOUD,
        base_url="https://api.test/v1",
        model=model,
        api_key="k",
        supports_json_schema=json_schema,
        max_retries=retries,
        retry_base_s=0,
        before_request=before_request,  # type: ignore[arg-type]
    )
    p._client = httpx.AsyncClient(
        base_url="https://api.test/v1", transport=httpx.MockTransport(script)
    )
    return p


@pytest.fixture
def slept(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record backoff delays instead of waiting them out."""
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr("app.vlm.openai_compat.asyncio.sleep", fake_sleep)
    return delays


class TestRetryAndFailover:
    async def test_a_shed_request_is_retried_until_it_succeeds(self, slept: list[float]) -> None:
        script = Script(fail(503), fail(429), ok())
        p = provider(script)

        result = await p.extract("data:image/jpeg;base64,x")

        assert result.extraction.first_name == "Priya"
        assert script.models == ["primary", "primary", "primary"]
        assert len(slept) == 2
        await p.aclose()

    async def test_retry_after_is_honoured_and_capped(self, slept: list[float]) -> None:
        """A provider's own advice beats a guess, but a minute-long wait does not."""
        script = Script(fail(429, {"Retry-After": "3"}), fail(429, {"Retry-After": "120"}), ok())
        p = provider(script)

        await p.extract("data:image/jpeg;base64,x")

        assert slept == [3.0, 10.0]
        await p.aclose()

    async def test_a_retired_model_fails_over_without_waiting(self, slept: list[float]) -> None:
        script = Script(fail(404), ok())
        p = provider(script, model="gemini-old, gemini-new")

        result = await p.extract("data:image/jpeg;base64,x")

        assert script.models == ["gemini-old", "gemini-new"]
        # Provenance records the model that answered, not the one configured first.
        assert result.model == "gemini-new"
        assert slept == []
        await p.aclose()

    async def test_exhausted_retries_fail_over_to_the_next_model(self, slept: list[float]) -> None:
        script = Script(fail(503), fail(503), ok())
        p = provider(script, model="a,b", retries=1)

        result = await p.extract("data:image/jpeg;base64,x")

        assert script.models == ["a", "a", "b"]
        assert result.model == "b"
        await p.aclose()

    async def test_a_rejected_format_degrades_on_the_same_model(self, slept: list[float]) -> None:
        """A 400 is about the request, so another model would reject it too."""
        script = Script(fail(400), ok())
        p = provider(script, model="a,b", json_schema=True)

        result = await p.extract("data:image/jpeg;base64,x")

        assert script.models == ["a", "a"]
        assert script.formats == ["json_schema", "json_object"]
        assert result.output_mode is OutputMode.JSON_OBJECT
        await p.aclose()

    async def test_the_last_model_failing_is_reported_as_retryable(
        self, slept: list[float]
    ) -> None:
        script = Script(fail(503), fail(503))
        p = provider(script, retries=1)

        with pytest.raises(VLMError) as exc_info:
            await p.extract("data:image/jpeg;base64,x")

        assert "HTTP 503" in str(exc_info.value)
        assert exc_info.value.retryable
        await p.aclose()

    async def test_a_refusing_hook_stops_the_request_being_sent(self) -> None:
        script = Script(ok())

        async def refuse() -> None:
            raise VLMError("over budget", tier=ProviderTier.CLOUD, retryable=False)

        p = provider(script, before_request=refuse)

        with pytest.raises(VLMError, match="over budget"):
            await p.extract("data:image/jpeg;base64,x")

        assert script.models == []
        await p.aclose()

    def test_an_empty_model_list_is_a_configuration_error(self) -> None:
        with pytest.raises(ValueError, match="no model configured"):
            OpenAICompatProvider(tier=ProviderTier.CLOUD, base_url="https://x/v1", model=" , ")


@pytest.fixture
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A file-backed database, so separate sessions see one another's writes."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'budget.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(bind=engine, expire_on_commit=False)
    await engine.dispose()


class TestDailyBudget:
    async def test_grants_up_to_the_limit_then_refuses(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        budget = DailyBudget("cloud_requests", 3, factory)

        grants = [await budget.try_take() for _ in range(5)]

        assert grants == [True, True, True, False, False]
        assert await budget.used_today() == 5

    async def test_the_ceiling_is_shared_across_workers(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Two processes each with their own counter would double the limit."""
        first = DailyBudget("cloud_requests", 2, factory)
        second = DailyBudget("cloud_requests", 2, factory)

        assert await first.try_take()
        assert await second.try_take()
        assert not await first.try_take()
        assert not await second.try_take()

    async def test_budgets_with_different_names_are_independent(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        assert await DailyBudget("a", 1, factory).try_take()
        assert await DailyBudget("b", 1, factory).try_take()

    async def test_a_zero_limit_means_no_ceiling(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        budget = DailyBudget("cloud_requests", 0, factory)

        assert all([await budget.try_take() for _ in range(10)])
        # Nothing is written when there is nothing to enforce.
        assert await budget.used_today() == 0

    async def test_the_guard_refuses_without_offering_a_retry(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """The limit resets at midnight; retrying in a minute cannot help."""
        guard = budget_guard(DailyBudget("cloud_requests", 1, factory))

        await guard()
        with pytest.raises(VLMError, match="daily limit of 1 hosted requests") as exc_info:
            await guard()

        assert exc_info.value.retryable is False
