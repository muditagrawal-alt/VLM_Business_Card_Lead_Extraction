"""OpenAI-compatible vision provider.

One client serves all three tiers. llama.cpp's server, Alibaba Model Studio and
OpenRouter all speak the same chat-completions dialect with `image_url` content
parts, so switching tiers is configuration rather than code.

Structured output is requested through a ladder of decreasing strictness:

1. `json_schema` — llama.cpp compiles the schema into a grammar, making invalid
   output impossible. Hosted providers vary in support.
2. `json_object` — valid JSON guaranteed, shape merely requested.
3. no format — the response is mined for its first JSON object.

If the parsed payload still fails validation, one repair attempt is made with
the validation error fed back. The rung that succeeded is recorded on the task,
so accuracy comparisons between tiers stay honest.

Hosted free tiers add two failure modes local servers do not have: a model is
retired from under you (404), or sheds load (429, 503). The client retries the
second with backoff and fails over to the next configured model for both,
recording the model that actually answered.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import ValidationError

from app.core.logging import get_logger
from app.models.enums import OutputMode, ProviderTier
from app.schemas.extraction import CardExtraction
from app.vlm.prompts import build_messages
from app.vlm.provider import VLMError, VLMResult

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

log = get_logger(__name__)

# Matches the outermost brace-delimited block, used only on the final rung.
_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)

# Deterministic decoding: transcription has one correct answer, so sampling
# variety is pure downside.
_TEMPERATURE = 0.0
# Budget enough for a full verbatim transcription plus every structured field.
# At 1536 the model ran out mid-object on dense cards; grammar-constrained
# decoding then closes the JSON with nulls, so the cards that needed the most
# reading silently lost their contact details instead of failing loudly.
_MAX_TOKENS = 3072

# Statuses that mean "try again shortly" rather than "this request is wrong".
_TRANSIENT = frozenset({429, 500, 502, 503, 504})
# A provider asking for a minute-long pause is better failed over than waited on.
_MAX_BACKOFF_S = 10.0


class OpenAICompatProvider:
    """A single tier backed by an OpenAI-compatible chat completions endpoint."""

    def __init__(
        self,
        *,
        tier: ProviderTier,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout_s: float = 60.0,
        supports_json_schema: bool = True,
        max_retries: int = 0,
        retry_base_s: float = 1.5,
        before_request: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.tier = tier
        self._models = [name.strip() for name in model.split(",") if name.strip()]
        if not self._models:
            raise ValueError(f"no model configured for the {tier.value} tier")
        # The primary model, for logs and readiness; results record the one
        # that actually answered.
        self.model = self._models[0]
        self._timeout_s = timeout_s
        self._base_url = base_url.rstrip("/")
        self._supports_json_schema = supports_json_schema
        self._max_retries = max_retries
        self._retry_base_s = retry_base_s
        # Called before every request: the hook that enforces a daily ceiling
        # on a metered provider. It raises to refuse.
        self._before_request = before_request
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=httpx.Timeout(timeout_s, connect=10.0),
        )

    # ---------------- public API ----------------

    async def extract(self, image_data_url: str) -> VLMResult:
        started = time.perf_counter()
        messages = build_messages(image_data_url)
        schema = CardExtraction.grammar_schema()

        attempts: list[tuple[OutputMode, dict[str, Any] | None]] = []
        if self._supports_json_schema:
            attempts.append(
                (
                    OutputMode.JSON_SCHEMA,
                    {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "card_extraction",
                            "strict": True,
                            "schema": schema,
                        },
                    },
                )
            )
        attempts.append((OutputMode.JSON_OBJECT, {"type": "json_object"}))
        attempts.append((OutputMode.PROMPT_ONLY, None))

        last_error: Exception | None = None
        for mode, response_format in attempts:
            try:
                payload, model = await self._complete(messages, response_format)
                text = self._content(payload)
                extraction = self._parse(text)
                return VLMResult(
                    extraction=extraction,
                    tier=self.tier,
                    model=model,
                    output_mode=mode,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    raw_response={"mode": mode.value, "content": text},
                )
            except httpx.TimeoutException as exc:
                # httpx timeout exceptions stringify to an empty message, so
                # this previously surfaced as "transport failure: " and told
                # nobody anything. The timeout is the actionable detail: it
                # says whether to wait longer or make the model faster.
                raise VLMError(f"timed out after {self._timeout_s:.0f}s", tier=self.tier) from exc
            except httpx.ConnectError as exc:
                # Naming the address distinguishes a stopped model server from
                # a misconfigured one, which look identical from the outside.
                raise VLMError(f"could not connect to {self._base_url}", tier=self.tier) from exc
            # TransportError, not HTTPError: HTTPStatusError is a subclass of
            # HTTPError, so catching the parent here would swallow 400, 429 and
            # 5xx responses and defeat the degradation ladder below.
            except httpx.TransportError as exc:
                detail = str(exc) or type(exc).__name__
                raise VLMError(f"transport failure: {detail}", tier=self.tier) from exc
            except (ValidationError, ValueError, KeyError) as exc:
                last_error = exc
                log.warning(
                    "structured_output_rung_failed",
                    tier=self.tier.value,
                    mode=mode.value,
                    error=str(exc)[:200],
                )
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                # 400 commonly means "this provider rejects that response
                # format", which the next, weaker rung may accept.
                if status == httpx.codes.BAD_REQUEST:
                    last_error = exc
                    log.warning("response_format_rejected", tier=self.tier.value, mode=mode.value)
                    continue
                raise VLMError(
                    f"HTTP {status}",
                    tier=self.tier,
                    retryable=status >= httpx.codes.INTERNAL_SERVER_ERROR
                    or status == httpx.codes.TOO_MANY_REQUESTS,
                ) from exc

        # Every rung produced output that failed validation; try once more with
        # the error fed back before giving up on this tier.
        repaired = await self._repair(messages, last_error)
        if repaired is not None:
            extraction, model = repaired
            return VLMResult(
                extraction=extraction,
                tier=self.tier,
                model=model,
                output_mode=OutputMode.REPAIRED,
                latency_ms=int((time.perf_counter() - started) * 1000),
                raw_response={"mode": OutputMode.REPAIRED.value},
            )

        raise VLMError(
            f"no valid extraction ({type(last_error).__name__}: {last_error})",
            tier=self.tier,
        )

    async def health(self) -> bool:
        """True when the tier is ready to accept a card.

        llama.cpp exposes /health; hosted APIs do not, so a model listing is
        used as the equivalent signal.
        """
        for path in ("/health", "/models"):
            try:
                response = await self._client.get(path, timeout=5.0)
            except httpx.HTTPError:
                continue
            if response.status_code == httpx.codes.OK:
                return True
        return False

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---------------- internals ----------------

    async def _complete(
        self, messages: list[dict[str, object]], response_format: dict[str, Any] | None
    ) -> tuple[dict[str, Any], str]:
        """POST one completion; return the payload and the model that answered.

        Each configured model is tried in order. Within a model, 429 and 5xx
        are retried with backoff. A 404 (the model was retired) or exhausted
        retries move on to the next model. Anything else is raised for the
        caller's degradation ladder to judge: a 400 there means the response
        format was rejected, which a different model would reject as well.
        """
        last_error: httpx.HTTPStatusError | None = None
        for index, model in enumerate(self._models):
            has_next_model = index + 1 < len(self._models)
            for attempt in range(self._max_retries + 1):
                if self._before_request is not None:
                    await self._before_request()

                body: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "temperature": _TEMPERATURE,
                    "max_tokens": _MAX_TOKENS,
                }
                if response_format is not None:
                    body["response_format"] = response_format

                response = await self._client.post("/chat/completions", json=body)
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    last_error = exc
                else:
                    return response.json(), model

                status = response.status_code
                if status == httpx.codes.NOT_FOUND and has_next_model:
                    log.warning("model_unavailable", tier=self.tier.value, model=model)
                    break
                if status in _TRANSIENT:
                    if attempt < self._max_retries:
                        delay = self._backoff(response, attempt)
                        log.info(
                            "provider_backoff",
                            tier=self.tier.value,
                            model=model,
                            status=status,
                            delay_s=round(delay, 1),
                        )
                        await asyncio.sleep(delay)
                        continue
                    if has_next_model:
                        log.warning("model_exhausted", tier=self.tier.value, model=model)
                        break
                raise last_error

        # Only reachable when every model ended on a failover condition.
        assert last_error is not None
        raise last_error

    def _backoff(self, response: httpx.Response, attempt: int) -> float:
        """Seconds to wait before retrying, preferring the provider's own advice."""
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), _MAX_BACKOFF_S)
            except ValueError:
                pass  # An HTTP-date; exponential backoff is close enough.
        base = min(self._retry_base_s * 2**attempt, _MAX_BACKOFF_S)
        # Jitter, so two workers that failed together do not retry together.
        return base + random.uniform(0, self._retry_base_s / 3)  # noqa: S311

    @staticmethod
    def _content(payload: dict[str, Any]) -> str:
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"unexpected response shape: {str(payload)[:200]}") from exc
        if isinstance(content, list):
            # Some providers return content as a list of typed parts.
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        if not isinstance(content, str) or not content.strip():
            raise ValueError("empty completion content")
        return content

    @staticmethod
    def _parse(text: str) -> CardExtraction:
        raw = text.strip()
        # Models sometimes wrap JSON in a fenced code block even when asked not to.
        if raw.startswith("```"):
            raw = raw.strip("`")
            raw = raw[raw.index("\n") + 1 :] if "\n" in raw else raw
            if raw.lstrip().startswith("json"):
                raw = raw.lstrip()[4:]
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            match = _JSON_BLOCK.search(raw)
            if match is None:
                raise
            data = json.loads(match.group(0))
        if not isinstance(data, dict):
            raise ValueError(f"expected a JSON object, got {type(data).__name__}")
        return CardExtraction.model_validate(data)

    async def _repair(
        self, messages: list[dict[str, object]], error: Exception | None
    ) -> tuple[CardExtraction, str] | None:
        if error is None:
            return None
        repair_messages = [
            *messages,
            {
                "role": "user",
                "content": (
                    "Your previous response could not be parsed: "
                    f"{str(error)[:300]}. Reply with a single valid JSON object "
                    "matching the required fields and nothing else."
                ),
            },
        ]
        try:
            payload, model = await self._complete(repair_messages, {"type": "json_object"})
            return self._parse(self._content(payload)), model
        except (httpx.HTTPError, ValidationError, ValueError, KeyError) as exc:
            log.warning("repair_attempt_failed", tier=self.tier.value, error=str(exc)[:200])
            return None
