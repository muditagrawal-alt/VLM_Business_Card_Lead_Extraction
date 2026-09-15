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
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx
from pydantic import ValidationError

from app.core.logging import get_logger
from app.models.enums import OutputMode, ProviderTier
from app.schemas.extraction import CardExtraction
from app.vlm.prompts import build_messages
from app.vlm.provider import VLMError, VLMResult

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
    ) -> None:
        self.tier = tier
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._supports_json_schema = supports_json_schema
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
                payload = await self._complete(messages, response_format)
                text = self._content(payload)
                extraction = self._parse(text)
                return VLMResult(
                    extraction=extraction,
                    tier=self.tier,
                    model=self.model,
                    output_mode=mode,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    raw_response={"mode": mode.value, "content": text},
                )
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                # A transport failure will not be fixed by a weaker output
                # format, so stop and let the chain fall to the next tier.
                # The tier is carried on the exception and applied as a label
                # by the chain, so including it here would read "cpu: cpu: ...".
                raise VLMError(f"transport failure: {exc}", tier=self.tier) from exc
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
            return VLMResult(
                extraction=repaired,
                tier=self.tier,
                model=self.model,
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
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": _TEMPERATURE,
            "max_tokens": _MAX_TOKENS,
        }
        if response_format is not None:
            body["response_format"] = response_format

        response = await self._client.post("/chat/completions", json=body)
        response.raise_for_status()
        return response.json()

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
    ) -> CardExtraction | None:
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
            payload = await self._complete(repair_messages, {"type": "json_object"})
            return self._parse(self._content(payload))
        except (httpx.HTTPError, ValidationError, ValueError, KeyError) as exc:
            log.warning("repair_attempt_failed", tier=self.tier.value, error=str(exc)[:200])
            return None
