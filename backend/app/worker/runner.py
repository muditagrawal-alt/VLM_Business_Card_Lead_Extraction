"""The extraction worker.

A separate process from the API, so a long CPU-tier inference cannot make the
web tier unresponsive, and so the two can be scaled or restarted apart.

The loop is deliberately dull: claim a few tasks, process them, settle them,
sleep if there was nothing to do. All the interesting behaviour — retries,
crash recovery, tier fallback — lives in the queue and the provider chain,
which are testable without a running worker.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import signal
from typing import TYPE_CHECKING

from sqlalchemy import or_, select

from app.config import get_settings
from app.core.db import dispose_engine, get_session_factory
from app.core.logging import configure_logging, get_logger
from app.models import Image, Lead, Task
from app.services import queue
from app.services.normalisation import normalise
from app.services.storage import LocalDiskStorage
from app.vlm.chain import ProviderChain
from app.vlm.provider import VLMError

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.config import Settings
    from app.services.normalisation import NormalisedLead
    from app.services.storage import Storage

log = get_logger(__name__)

# How long to wait before polling again when the queue is empty. Short enough
# that an upload starts processing promptly, long enough not to hammer the
# database while idle.
IDLE_SLEEP_S = 1.5
# Refresh a lease at roughly a third of its length, so a slow card keeps its
# claim even if one heartbeat is missed.
HEARTBEAT_DIVISOR = 3


class Worker:
    """Polls the task queue and runs extraction until asked to stop."""

    def __init__(
        self,
        settings: Settings,
        chain: ProviderChain,
        storage: Storage,
    ) -> None:
        self._settings = settings
        self._chain = chain
        self._storage = storage
        self._session_factory = get_session_factory()
        self._stopping = asyncio.Event()
        self._in_flight = 0

    def request_stop(self) -> None:
        """Signal the loop to finish the current cards and exit."""
        if not self._stopping.is_set():
            log.info("worker_stopping", in_flight=self._in_flight)
            self._stopping.set()

    async def run(self) -> None:
        log.info(
            "worker_started",
            tiers=self._chain.tiers,
            concurrency=self._settings.worker_concurrency,
        )
        try:
            while not self._stopping.is_set():
                claimed = await self._claim_batch()
                if not claimed:
                    # Wait on the stop event rather than sleeping, so shutdown
                    # is immediate instead of up to IDLE_SLEEP_S late.
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._stopping.wait(), IDLE_SLEEP_S)
                    continue

                await asyncio.gather(
                    *(self._run_task(task_id) for task_id in claimed),
                    return_exceptions=True,
                )
        finally:
            log.info("worker_stopped")

    async def _claim_batch(self) -> list[UUID]:
        """Claim up to the configured concurrency, in its own transaction.

        Only the ids are carried out: each card then runs in its own session,
        so one slow card does not hold a transaction open for the others.
        """
        async with self._session_factory() as session:
            tasks = await queue.claim_tasks(
                session,
                limit=self._settings.worker_concurrency,
                lease_seconds=self._settings.task_lease_seconds,
                max_attempts=self._settings.task_max_attempts,
            )
            task_ids = [task.id for task in tasks]
            await session.commit()
        if task_ids:
            log.info("tasks_claimed", count=len(task_ids))
        return task_ids

    async def _run_task(self, task_id: UUID) -> None:
        self._in_flight += 1
        heartbeat = asyncio.create_task(self._heartbeat(task_id))
        try:
            await self._process(task_id)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            self._in_flight -= 1

    async def _heartbeat(self, task_id: UUID) -> None:
        """Keep a long-running card's lease alive.

        Without this a card slower than its lease would be re-claimed by
        another worker while still in flight, and paid for twice.
        """
        interval = max(5.0, self._settings.task_lease_seconds / HEARTBEAT_DIVISOR)
        while True:
            await asyncio.sleep(interval)
            try:
                async with self._session_factory() as session:
                    await queue.heartbeat(
                        session,
                        task_id,
                        lease_seconds=self._settings.task_lease_seconds,
                    )
                    await session.commit()
            except Exception:
                # A failed heartbeat must not kill the card it is protecting.
                log.warning("heartbeat_failed", task_id=str(task_id), exc_info=True)

    async def _fail(self, task_id: UUID, error: str) -> None:
        """Record a failure for a task in its own transaction."""
        async with self._session_factory() as session:
            task = await session.scalar(select(Task).where(Task.id == task_id))
            if task is not None:
                retrying = await queue.mark_failed(
                    session,
                    task,
                    error=error,
                    max_attempts=self._settings.task_max_attempts,
                )
                log.warning(
                    "task_failed",
                    task_id=str(task_id),
                    retrying=retrying,
                    error=error[:300],
                )
            await session.commit()

    async def _process(self, task_id: UUID) -> None:
        async with self._session_factory() as session:
            task = await session.scalar(select(Task).where(Task.id == task_id))
            if task is None:
                # Deleted mid-flight by a purge or the retention sweep.
                log.warning("task_vanished", task_id=str(task_id))
                return
            image = await session.scalar(select(Image).where(Image.id == task.image_id))
            storage_key = image.storage_key if image is not None else None
            job_id = task.job_id

        if storage_key is None:
            await self._fail(task_id, "the stored image is missing")
            return

        try:
            data = await self._storage.read(storage_key)
        except OSError as exc:
            await self._fail(task_id, f"could not read the stored image: {exc}")
            return

        data_url = "data:image/jpeg;base64," + base64.b64encode(data).decode()

        try:
            result = await self._chain.extract(data_url)
        except VLMError as exc:
            await self._fail(task_id, str(exc))
            return

        lead = normalise(result.extraction)

        async with self._session_factory() as session:
            task = await session.scalar(select(Task).where(Task.id == task_id))
            if task is None:
                log.warning("task_vanished_after_extraction", task_id=str(task_id))
                return

            task.provider = result.tier
            task.model = result.model
            task.output_mode = result.output_mode
            task.latency_ms = result.latency_ms
            task.raw_response = result.raw_response

            session.add(
                Lead(
                    job_id=job_id,
                    task_id=task.id,
                    image_id=task.image_id,
                    first_name=lead.first_name,
                    last_name=lead.last_name,
                    position=lead.position,
                    company=lead.company,
                    location=lead.location,
                    phone=lead.phone,
                    email=lead.email,
                    website=lead.website,
                    address=lead.address,
                    extra_phones=lead.extra_phones,
                    extra_emails=lead.extra_emails,
                    raw_text=lead.raw_text,
                    notes=lead.notes,
                    confidence=lead.confidence,
                    is_duplicate_of=await _find_duplicate(session, job_id, lead),
                )
            )
            await queue.mark_done(session, task)
            await session.commit()

        log.info(
            "card_extracted",
            task_id=str(task_id),
            tier=result.tier.value,
            output_mode=result.output_mode.value,
            latency_ms=result.latency_ms,
        )


async def _find_duplicate(session: AsyncSession, job_id: UUID, lead: NormalisedLead) -> UUID | None:
    """Point at an earlier lead in the same batch with the same contact.

    Bulk uploads routinely contain both sides of a card or the same card
    photographed twice. The duplicate is flagged rather than dropped, because
    only the user can say whether two cards are really the same person.
    """
    if not (lead.email or lead.phone):
        return None
    conditions = []
    if lead.email:
        conditions.append(Lead.email == lead.email)
    if lead.phone:
        conditions.append(Lead.phone == lead.phone)

    return await session.scalar(
        select(Lead.id)
        .where(Lead.job_id == job_id, or_(*conditions))
        .order_by(Lead.created_at)
        .limit(1)
    )


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.is_production)

    chain = ProviderChain.from_settings(settings)
    storage = LocalDiskStorage(settings.storage_local_path)
    worker = Worker(settings, chain, storage)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # Finish the cards in flight instead of abandoning their leases, so a
        # deploy does not leave a batch stalled until the leases lapse.
        loop.add_signal_handler(sig, worker.request_stop)

    try:
        await worker.run()
    finally:
        await chain.aclose()
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
