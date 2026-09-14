"""A circuit breaker per inference tier.

Without one, a dead GPU container costs every card a full timeout before the
chain falls through — a 50-card batch would spend fifty minutes discovering the
same failure fifty times. The breaker learns once and routes around it, then
probes periodically so recovery is automatic rather than manual.
"""

from __future__ import annotations

import time
from enum import StrEnum

from app.core.logging import get_logger

log = get_logger(__name__)


class BreakerState(StrEnum):
    CLOSED = "closed"  # healthy, traffic flows
    OPEN = "open"  # failing, traffic skipped
    HALF_OPEN = "half_open"  # one trial request allowed


class CircuitBreaker:
    """Trips after consecutive failures, then retries on a timer.

    Only *consecutive* failures count. An occasional failed card among
    successes is normal — a hard-to-read photo, a one-off timeout — and should
    not take a working tier out of service.
    """

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 3,
        reset_seconds: float = 60.0,
        now: float | None = None,
    ) -> None:
        self.name = name
        self._threshold = max(1, failure_threshold)
        self._reset_seconds = reset_seconds
        self._failures = 0
        self._opened_at: float | None = None
        self._clock = time.monotonic if now is None else (lambda: now)

    @property
    def state(self) -> BreakerState:
        if self._opened_at is None:
            return BreakerState.CLOSED
        if self._clock() - self._opened_at >= self._reset_seconds:
            return BreakerState.HALF_OPEN
        return BreakerState.OPEN

    @property
    def is_available(self) -> bool:
        """True when this tier may be given work."""
        return self.state is not BreakerState.OPEN

    def record_success(self) -> None:
        was_open = self._opened_at is not None
        self._failures = 0
        self._opened_at = None
        if was_open:
            log.info("breaker_closed", tier=self.name)

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._threshold and self._opened_at is None:
            self._opened_at = self._clock()
            log.warning(
                "breaker_opened",
                tier=self.name,
                failures=self._failures,
                reset_in_s=self._reset_seconds,
            )
        elif self._opened_at is not None:
            # A failed half-open probe restarts the cooldown rather than
            # letting every subsequent card retry a tier that is still down.
            self._opened_at = self._clock()

    def snapshot(self) -> dict[str, object]:
        return {
            "tier": self.name,
            "state": self.state.value,
            "consecutive_failures": self._failures,
        }
