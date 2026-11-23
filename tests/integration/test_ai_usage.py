"""Stage 15 (US-09): AI usage monitoring against a real database.

Covers the counters written by the gateway, a member's own view (15.3), the administrator's view
with the system row and totals (15.4), model prices through the admin API (15.3), and the
authorization of both views.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.db.models import LlmUsageDaily, User
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from spoldzielnia_ai.llm.gateway import LiteLLMGateway, LLMPurpose
from spoldzielnia_ai.llm.usage import UsageEvent, UsageRecorder
from spoldzielnia_ai.llm.usage_context import usage_owner
from tests.fixtures.app import harness
from tests.integration.test_admin_llm_api import LLM, ProbeTransport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from spoldzielnia_ai.core.settings import Settings
    from tests.fixtures.app import Harness, LoggedIn

pytestmark = pytest.mark.integration

MESSAGES: list[Any] = [{"role": "user", "content": "Jaka jest stolica Polski?"}]


@pytest.fixture
async def factory(fresh_db: str) -> AsyncIterator[SessionFactory]:
    engine = create_async_engine(fresh_db)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def h(settings: Settings, factory: SessionFactory) -> AsyncIterator[Harness]:
    llm = LLM.model_copy(update={"chat_model": "bielik-11b"})
    async with harness(settings.model_copy(update={"llm": llm}), factory) as h:
        h.app.state.llm_probe_transport = ProbeTransport()
        yield h


async def _vllm(admin: LoggedIn) -> str:
    created = await admin.client.post(
        "/admin/llm/providers",
        json={"name": "vllm", "base_url": "http://vllm:8000/v1"},
        headers=admin.headers,
    )
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


async def _model(admin: LoggedIn, provider: str, name: str) -> dict[str, Any]:
    created = await admin.client.post(
        "/admin/llm/models",
        json={"provider_id": provider, "model_name": name, "capability": "CHAT"},
        headers=admin.headers,
    )
    assert created.status_code == 201, created.text
    body: dict[str, Any] = created.json()
    return body


async def _record(
    factory: SessionFactory,
    user_id: UUID | None,
    *,
    purpose: str = "RAG",
    prompt: int = 0,
    completion: int = 0,
    priced: bool = True,
) -> None:
    event = UsageEvent(
        user_id=user_id,
        purpose=purpose,
        model="local-chat",
        prompt_tokens=prompt,
        completion_tokens=completion,
        price_in_micro=2_000_000 if priced else None,
        price_out_micro=8_000_000 if priced else None,
    )
    await UsageRecorder(factory).record(event)


async def test_the_gateway_counts_a_call_for_its_owner(h: Harness, factory: SessionFactory) -> None:
    member = await h.login()
    gateway = LiteLLMGateway(
        LLM.model_copy(update={"chat_model": "bielik-11b"}),
        ProbeTransport(),
        usage=UsageRecorder(factory),
    )
    with usage_owner(UUID(member.user_id)):
        await gateway.complete(MESSAGES, purpose=LLMPurpose.RAG)
    async with factory() as session:
        row = (
            await session.execute(
                select(LlmUsageDaily).where(LlmUsageDaily.user_id == UUID(member.user_id))
            )
        ).scalar_one()
    assert (row.calls, row.prompt_tokens, row.completion_tokens) == (1, 5, 3)
    assert row.purpose == "RAG"


async def test_a_member_sees_only_their_own_usage(h: Harness, factory: SessionFactory) -> None:
    alice = await h.login()
    bob = await h.login()
    await _record(factory, UUID(alice.user_id), prompt=100, completion=50)
    await _record(factory, UUID(bob.user_id), prompt=1000, completion=500)

    response = await alice.client.get("/me/usage")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["totals"]["prompt_tokens"] == 100
    assert body["totals"]["completion_tokens"] == 50
    assert body["totals"]["total_tokens"] == 150
    assert [row["purpose"] for row in body["by_purpose"]] == ["RAG"]
    assert body["currency"] == "PLN"


async def test_an_unpriced_call_is_counted_but_costs_nothing(
    h: Harness, factory: SessionFactory
) -> None:
    alice = await h.login()
    await _record(factory, UUID(alice.user_id), prompt=10, priced=False)
    body = (await alice.client.get("/me/usage")).json()
    assert body["totals"]["calls"] == 1
    assert body["totals"]["unpriced_calls"] == 1
    assert body["totals"]["cost_micro"] == 0


async def test_the_administrator_sees_every_member_and_the_system(
    h: Harness, factory: SessionFactory
) -> None:
    admin = await h.login(roles=["ADMIN", "MEMBER"], amr=["pwd", "otp"])
    alice = await h.login()
    await _record(factory, UUID(alice.user_id), prompt=100, completion=50)
    await _record(factory, None, purpose="EMBED", prompt=40)

    response = await admin.client.get("/admin/usage")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["system"]["prompt_tokens"] == 40
    assert body["total"]["total_tokens"] == 190
    assert body["total"]["calls"] == 2
    member_ids = [row["user_id"] for row in body["members"]]
    assert member_ids == [alice.user_id]
    assert sorted(row["purpose"] for row in body["by_purpose"]) == ["EMBED", "RAG"]
    # Cost: 100 * 2 + 50 * 8 for the member; the system row cost 40 * 2.
    assert body["members"][0]["totals"]["cost_micro"] == 600
    assert body["system"]["cost_micro"] == 80


async def test_members_and_experts_cannot_read_the_administrator_view(h: Harness) -> None:
    member = await h.login()
    no_mfa = await h.login(roles=["ADMIN", "MEMBER"], amr=["pwd"])
    assert (await member.client.get("/admin/usage")).status_code == 403
    assert (await no_mfa.client.get("/admin/usage")).status_code == 403


async def test_a_bad_period_is_refused_with_a_code(h: Harness) -> None:
    member = await h.login()
    reversed_ = await member.client.get(
        "/me/usage", params={"from": "2026-02-02", "to": "2026-02-01"}
    )
    assert reversed_.status_code == 422
    assert reversed_.json()["detail"] == {"error": "usage_period_invalid"}
    too_long = await member.client.get(
        "/me/usage", params={"from": "2024-01-01", "to": "2026-01-01"}
    )
    assert too_long.json()["detail"] == {"error": "usage_period_too_long"}


async def test_prices_are_set_and_cleared_through_the_model_api(h: Harness) -> None:
    admin = await h.login(roles=["ADMIN", "MEMBER"], amr=["pwd", "otp"])
    provider = await _vllm(admin)
    model = await _model(admin, provider, "bielik-11b")
    assert model["price_in_per_million"] is None

    priced = await admin.client.patch(
        f"/admin/llm/models/{model['id']}",
        json={"price": {"price_in_per_million": "2.5", "price_out_per_million": "8"}},
        headers=admin.headers,
    )
    assert priced.status_code == 200, priced.text
    assert priced.json()["price_in_per_million"] == "2.5"
    assert priced.json()["price_out_per_million"] == "8"

    too_fine = await admin.client.patch(
        f"/admin/llm/models/{model['id']}",
        json={"price": {"price_in_per_million": "0.0000001", "price_out_per_million": "1"}},
        headers=admin.headers,
    )
    assert too_fine.status_code == 422

    cleared = await admin.client.patch(
        f"/admin/llm/models/{model['id']}", json={"price": None}, headers=admin.headers
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["price_in_per_million"] is None


async def test_the_counters_are_deleted_with_the_user(h: Harness, factory: SessionFactory) -> None:
    alice = await h.login()
    await _record(factory, UUID(alice.user_id), prompt=1)
    async with factory() as session, session.begin():
        await session.execute(delete(User).where(User.id == UUID(alice.user_id)))
    async with factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(LlmUsageDaily)
            .where(LlmUsageDaily.user_id == UUID(alice.user_id))
        )
    assert count == 0
