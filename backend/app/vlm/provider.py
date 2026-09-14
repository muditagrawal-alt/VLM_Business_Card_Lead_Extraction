"""Provider abstraction shared by every inference tier."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from app.models.enums import OutputMode, ProviderTier
    from app.schemas.extraction import CardExtraction


class VLMError(RuntimeError):
    """A provider could not produce a valid extraction."""

    def __init__(self, message: str, *, tier: ProviderTier, retryable: bool = True) -> None:
        super().__init__(message)
        self.tier = tier
        self.retryable = retryable


@dataclass(slots=True)
class VLMResult:
    """One successful extraction, with the provenance needed to judge it."""

    extraction: CardExtraction
    tier: ProviderTier
    model: str
    output_mode: OutputMode
    latency_ms: int
    raw_response: dict[str, Any] = field(default_factory=dict)


class VLMProvider(Protocol):
    """One inference tier.

    Implementations must raise `VLMError` rather than returning a partial
    result, so the chain can move to the next tier on any failure.
    """

    tier: ProviderTier
    model: str

    async def extract(self, image_data_url: str) -> VLMResult: ...

    async def health(self) -> bool:
        """Cheap readiness probe, used to skip a dead tier before sending work."""
        ...

    async def aclose(self) -> None: ...
