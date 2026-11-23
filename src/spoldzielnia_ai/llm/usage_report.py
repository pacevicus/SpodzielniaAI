"""Reading the AI usage counters (stage 15, US-09): one member's own totals, and the admin's view
of every member plus the system (15.3, 15.4).

Every function takes a plain session: the counters are not RLS-protected (see
``LlmUsageDaily``), so access control lives in the API. ``user_usage`` only ever filters by the
caller's id; ``admin_usage`` is reached only through the ADMIN router (role + MFA).

Totals are sums of stored integers (tokens, calls, micro-units); nothing is rounded before the
API formats it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID  # noqa: TC003 - used at runtime in dataclass annotations

from sqlalchemy import func, select

from spoldzielnia_ai.db.models import LlmUsageDaily, User

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class Totals:
    calls: int = 0
    unpriced_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_micro: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __add__(self, other: Totals) -> Totals:
        return Totals(
            calls=self.calls + other.calls,
            unpriced_calls=self.unpriced_calls + other.unpriced_calls,
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            cost_micro=self.cost_micro + other.cost_micro,
        )


@dataclass(frozen=True, slots=True)
class UserUsage:
    user_id: UUID
    display_name: str | None
    totals: Totals


@dataclass(frozen=True, slots=True)
class MemberUsage:
    """The caller's own usage: totals and the split by purpose (15.3)."""

    totals: Totals
    by_purpose: dict[str, Totals] = field(default_factory=dict[str, Totals])


@dataclass(frozen=True, slots=True)
class AdminUsage:
    """Everyone's usage for a period (15.4): the system row (work with no owner), the totals of
    all rows, per member, and per purpose across everyone."""

    system: Totals
    total: Totals
    members: list[UserUsage]
    by_purpose: dict[str, Totals]


def _sums() -> list[Any]:
    """The five sums of a period, in the order ``_totals`` reads them."""
    table = LlmUsageDaily.__table__.c
    return [
        func.coalesce(func.sum(table.calls), 0),
        func.coalesce(func.sum(table.unpriced_calls), 0),
        func.coalesce(func.sum(table.prompt_tokens), 0),
        func.coalesce(func.sum(table.completion_tokens), 0),
        func.coalesce(func.sum(table.cost_micro), 0),
    ]


def _totals(values: Sequence[Any]) -> Totals:
    calls, unpriced, prompt, completion, cost = (int(value) for value in values)
    return Totals(calls, unpriced, prompt, completion, cost)


def _grouped(start: date, end: date, *by: Any) -> Any:
    """Sums of the counters for ``start``..``end`` (inclusive), grouped by ``by``."""
    day = LlmUsageDaily.__table__.c.day
    return select(*by, *_sums()).where(day >= start, day <= end).group_by(*by)


async def member_usage(session: AsyncSession, user_id: UUID, start: date, end: date) -> MemberUsage:
    table = LlmUsageDaily.__table__.c
    rows = await session.execute(
        _grouped(start, end, table.purpose).where(table.user_id == user_id)
    )
    by_purpose: dict[str, Totals] = {}
    total = Totals()
    for purpose, *values in rows.all():
        totals = _totals(values)
        by_purpose[str(purpose)] = totals
        total = total + totals
    return MemberUsage(total, by_purpose)


async def admin_usage(session: AsyncSession, start: date, end: date) -> AdminUsage:
    table = LlmUsageDaily.__table__.c
    per_user = await session.execute(_grouped(start, end, table.user_id))
    system = Totals()
    total = Totals()
    members: list[UserUsage] = []
    rows = per_user.all()
    member_ids = [row[0] for row in rows if row[0] is not None]
    names = {
        user_id: name
        for user_id, name in (
            await session.execute(select(User.id, User.display_name).where(User.id.in_(member_ids)))
        ).all()
    }
    for user_id, *values in rows:
        totals = _totals(values)
        total = total + totals
        if user_id is None:
            system = totals
        else:
            members.append(UserUsage(user_id, names.get(user_id), totals))
    members.sort(key=lambda row: (-row.totals.cost_micro, -row.totals.total_tokens))

    purposes = await session.execute(_grouped(start, end, table.purpose))
    by_purpose = {str(purpose): _totals(values) for purpose, *values in purposes.all()}
    return AdminUsage(system, total, members, by_purpose)
