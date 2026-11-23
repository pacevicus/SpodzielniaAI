"""AI usage monitoring (stage 15, US-09): a member's own usage, and the administrator's view of
everyone (15.3, 15.4).

Read-only, so no audit entry (the audit rule covers changes; prices are changed through the
``/admin/llm`` model endpoints, which audit). Periods are inclusive calendar days in UTC; the
default is the last 30 days, and a period may span at most 366 days. Errors are machine codes
(``usage_period_invalid``, ``usage_period_too_long``), per the API rules.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal  # noqa: TC003 - pydantic resolves annotations at runtime
from typing import Annotated
from uuid import UUID  # noqa: TC003 - pydantic resolves annotations at runtime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002 - FastAPI resolves the annotation

from spoldzielnia_ai.auth.deps import CurrentUser, elevated_router
from spoldzielnia_ai.db.enums import Role
from spoldzielnia_ai.db.session import get_session
from spoldzielnia_ai.llm.usage import micro_to_price
from spoldzielnia_ai.llm.usage_report import Totals, admin_usage, member_usage

DEFAULT_DAYS = 30
MAX_SPAN_DAYS = 366

member_router = APIRouter(tags=["me"])
admin_router = elevated_router(Role.ADMIN, prefix="/admin/usage", tags=["admin-usage"])


class UsageTotalsOut(BaseModel):
    calls: int
    unpriced_calls: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_micro: int
    # The same cost in the usage currency, as an estimate (see ``llm.usage_currency``).
    cost: Decimal

    @classmethod
    def of(cls, totals: Totals) -> UsageTotalsOut:
        return cls(
            calls=totals.calls,
            unpriced_calls=totals.unpriced_calls,
            prompt_tokens=totals.prompt_tokens,
            completion_tokens=totals.completion_tokens,
            total_tokens=totals.total_tokens,
            cost_micro=totals.cost_micro,
            cost=micro_to_price(totals.cost_micro),
        )


class PurposeUsageOut(BaseModel):
    purpose: str
    totals: UsageTotalsOut


class MyUsageOut(BaseModel):
    period_from: date
    period_to: date
    currency: str
    totals: UsageTotalsOut
    by_purpose: list[PurposeUsageOut]


class MemberUsageOut(BaseModel):
    user_id: UUID
    display_name: str | None
    totals: UsageTotalsOut


class AdminUsageOut(BaseModel):
    period_from: date
    period_to: date
    currency: str
    # Work with no owner (scheduled jobs, maintenance), kept apart from the members.
    system: UsageTotalsOut
    total: UsageTotalsOut
    members: list[MemberUsageOut]
    by_purpose: list[PurposeUsageOut]


def period(
    start: Annotated[date | None, Query(alias="from")] = None,
    end: Annotated[date | None, Query(alias="to")] = None,
) -> tuple[date, date]:
    """The requested period, or the last 30 days up to today (UTC). Both ends inclusive."""
    today = datetime.now(UTC).date()
    end = end or today
    start = start or end - timedelta(days=DEFAULT_DAYS - 1)
    if start > end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "usage_period_invalid"},
        )
    if (end - start).days + 1 > MAX_SPAN_DAYS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "usage_period_too_long"},
        )
    return start, end


Period = Annotated[tuple[date, date], Depends(period)]


@member_router.get("/me/usage", response_model=MyUsageOut)
async def my_usage(
    request: Request,
    principal: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    window: Period,
) -> MyUsageOut:
    start, end = window
    usage = await member_usage(session, principal.id, start, end)
    return MyUsageOut(
        period_from=start,
        period_to=end,
        currency=request.app.state.settings.llm.usage_currency,
        totals=UsageTotalsOut.of(usage.totals),
        by_purpose=[
            PurposeUsageOut(purpose=purpose, totals=UsageTotalsOut.of(totals))
            for purpose, totals in sorted(usage.by_purpose.items())
        ],
    )


@admin_router.get("", response_model=AdminUsageOut)
async def all_usage(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    window: Period,
) -> AdminUsageOut:
    start, end = window
    usage = await admin_usage(session, start, end)
    return AdminUsageOut(
        period_from=start,
        period_to=end,
        currency=request.app.state.settings.llm.usage_currency,
        system=UsageTotalsOut.of(usage.system),
        total=UsageTotalsOut.of(usage.total),
        members=[
            MemberUsageOut(
                user_id=row.user_id,
                display_name=row.display_name,
                totals=UsageTotalsOut.of(row.totals),
            )
            for row in usage.members
        ],
        by_purpose=[
            PurposeUsageOut(purpose=purpose, totals=UsageTotalsOut.of(totals))
            for purpose, totals in sorted(usage.by_purpose.items())
        ],
    )
