"""A shared daily ceiling on a metered action.

The hosted tier spends a provider's quota — and, on a paid plan, money — with
every request. The public URL means anyone can trigger requests, so per-IP rate
limits alone are not enough: a handful of addresses could still drain the day's
quota for everyone. This is the backstop that holds regardless of who is asking.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy.dialects import postgresql, sqlite

from app.core.logging import get_logger
from app.models.usage import UsageCounter

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = get_logger(__name__)


class DailyBudget:
    """Counts one named action per UTC day and refuses past a limit."""

    def __init__(
        self,
        name: str,
        limit: int,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self.name = name
        self.limit = limit
        self._session_factory = session_factory

    async def try_take(self) -> bool:
        """Record one use; False when that use would exceed today's limit.

        The increment and the read happen in one atomic upsert, so two workers
        racing for the last unit cannot both be granted it. A refused attempt
        still counts, which is correct: it is the demand being measured.
        """
        if self.limit <= 0:
            return True
        today = datetime.now(UTC).date()
        async with self._session_factory() as session, session.begin():
            used = await self._increment(session, today)
        if used > self.limit:
            if used == self.limit + 1:
                # Once per day is enough to notice; every refusal would be noise.
                log.warning("daily_budget_exhausted", budget=self.name, limit=self.limit)
            return False
        return True

    async def used_today(self) -> int:
        today = datetime.now(UTC).date()
        async with self._session_factory() as session:
            row = await session.get(UsageCounter, (today, self.name))
            return row.count if row else 0

    async def _increment(self, session: AsyncSession, day: object) -> int:
        dialect = session.get_bind().dialect.name
        insert = postgresql.insert if dialect == "postgresql" else sqlite.insert
        statement = (
            insert(UsageCounter)
            .values(day=day, name=self.name, count=1)
            .on_conflict_do_update(
                index_elements=[UsageCounter.day, UsageCounter.name],
                set_={"count": UsageCounter.count + 1},
            )
            .returning(UsageCounter.count)
        )
        return int((await session.execute(statement)).scalar_one())
