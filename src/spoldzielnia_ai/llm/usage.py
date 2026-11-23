"""AI usage metering (stage 15, US-09): what each LLM call costs, counted per day.

The gateway reports every successful model response to a ``UsageSink``. ``UsageRecorder`` adds
the counts to ``llm_usage_daily`` with one atomic upsert, so concurrent calls never lose an
increment. Rules:

- counters only: no prompt, answer, audio or image is ever passed here (``UsageEvent`` has no
  field for content);
- cost is integer arithmetic in millionths of the usage currency (``cost_micro``); a model
  without a price counts its calls as ``unpriced_calls`` and adds no cost;
- recording never fails the answer: a failed write is logged with its error type only, and the
  call's result is returned as before (R6 of the stage plan: fail open, and a missing row is an
  undercount that the plan names as a risk).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol, cast
from uuid import UUID  # noqa: TC003 - dataclass annotations are resolved at runtime

from sqlalchemy.dialects.postgresql import insert as pg_insert

from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.models import LlmUsageDaily

if TYPE_CHECKING:
    from sqlalchemy import Table

    from spoldzielnia_ai.db.session import SessionFactory

log = get_logger(__name__)

MICRO = 1_000_000  # micro-units per unit, and tokens per million (price is per million tokens)


@dataclass(frozen=True, slots=True)
class UsageEvent:
    """One model response: who it was for, which purpose and model, and the token counts it
    reported (zero where the endpoint reports none, e.g. speech or image embeddings). Prices
    are per million tokens in micro-units; ``None`` means the model had no price."""

    user_id: UUID | None
    purpose: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    price_in_micro: int | None = None
    price_out_micro: int | None = None

    @property
    def priced(self) -> bool:
        return self.price_in_micro is not None and self.price_out_micro is not None

    @property
    def cost_micro(self) -> int:
        """Cost in micro-units, rounded half up to the nearest micro-unit. Zero when unpriced."""
        price_in, price_out = self.price_in_micro, self.price_out_micro
        if price_in is None or price_out is None:
            return 0
        total = self.prompt_tokens * price_in + self.completion_tokens * price_out
        return (total + MICRO // 2) // MICRO


def price_to_micro(per_million: Decimal) -> int:
    """A price per million tokens (at most six decimals, validated by the caller) as the
    integer micro-units stored in the database."""
    return int(per_million * MICRO)


def micro_to_price(micro: int) -> Decimal:
    """The inverse of ``price_to_micro``, for the admin API."""
    return Decimal(micro) / MICRO


class UsageSink(Protocol):
    async def record(self, event: UsageEvent) -> None: ...


class BufferedUsage:
    """A sink for gateways whose calls run on other threads or loops (worker engines): events
    are queued without touching the database, and ``flush`` writes them on the loop that owns
    the database engine. The queue is a ``deque``, safe to append to from another thread."""

    def __init__(self) -> None:
        self._pending: deque[UsageEvent] = deque()

    @property
    def pending(self) -> bool:
        return bool(self._pending)

    async def record(self, event: UsageEvent) -> None:
        self._pending.append(event)

    async def flush(self, recorder: UsageSink) -> None:
        while True:
            try:
                event = self._pending.popleft()
            except IndexError:
                return
            await recorder.record(event)


class UsageRecorder:
    """Adds one event to the daily counters, in its own transaction."""

    def __init__(self, factory: SessionFactory) -> None:
        self._factory = factory

    async def record(self, event: UsageEvent) -> None:
        try:
            await self._upsert(event)
        except Exception as exc:  # R6: the answer is already produced; never fail the call
            log.warning(
                "llm.usage_record_failed",
                purpose=event.purpose,
                model=event.model,
                error=type(exc).__name__,
            )

    async def _upsert(self, event: UsageEvent) -> None:
        values = {
            "day": datetime.now(UTC).date(),
            "user_id": event.user_id,
            "purpose": event.purpose,
            "model": event.model,
            "calls": 1,
            "unpriced_calls": 0 if event.priced else 1,
            "prompt_tokens": event.prompt_tokens,
            "completion_tokens": event.completion_tokens,
            "cost_micro": event.cost_micro,
        }
        table = cast("Table", LlmUsageDaily.__table__)
        stmt = pg_insert(LlmUsageDaily).values(**values)
        # Adds the new counts to the stored ones, in one statement: no lost increments.
        add: dict[str, Any] = {
            name: table.c[name] + stmt.excluded[name]
            for name in (
                "calls",
                "unpriced_calls",
                "prompt_tokens",
                "completion_tokens",
                "cost_micro",
            )
        }
        if event.user_id is None:
            stmt = stmt.on_conflict_do_update(
                index_elements=["day", "purpose", "model"],
                index_where=table.c.user_id.is_(None),
                set_=add,
            )
        else:
            stmt = stmt.on_conflict_do_update(
                index_elements=["day", "user_id", "purpose", "model"],
                index_where=table.c.user_id.is_not(None),
                set_=add,
            )
        async with self._factory() as session, session.begin():
            await session.execute(stmt)
