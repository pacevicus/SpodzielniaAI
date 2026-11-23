"""Stage 15 (US-09): AI usage metering, the cost arithmetic and the period rules."""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from spoldzielnia_ai.api.usage import period
from spoldzielnia_ai.core.settings import LlmSettings
from spoldzielnia_ai.llm.config_service import PriceIn
from spoldzielnia_ai.llm.gateway import (
    Deployment,
    LiteLLMGateway,
    LLMPurpose,
    ModelUnavailable,
    ResolvedLlmConfig,
    StaticConfig,
)
from spoldzielnia_ai.llm.usage import (
    BufferedUsage,
    UsageEvent,
    UsageRecorder,
    micro_to_price,
    price_to_micro,
)
from spoldzielnia_ai.llm.usage_context import current_usage_owner, set_usage_owner, usage_owner

MESSAGES: list[Any] = [{"role": "user", "content": "x"}]


# --- Cost arithmetic (15.2) -----------------------------------------------------------------


def test_cost_is_tokens_times_price_per_million_in_micro_units() -> None:
    # 2.00 PLN per million prompt tokens and 8.00 per million completion tokens.
    event = UsageEvent(
        user_id=None,
        purpose="RAG",
        model="cloud-chat",
        prompt_tokens=500,
        completion_tokens=250,
        price_in_micro=price_to_micro(Decimal("2.00")),
        price_out_micro=price_to_micro(Decimal("8.00")),
    )
    # 500 * 2 + 250 * 8 = 3000 millionths of a unit per million tokens: 0.003 PLN.
    assert event.cost_micro == 3000


def test_cost_rounds_half_up_to_the_micro_unit() -> None:
    event = UsageEvent(
        user_id=None,
        purpose="RAG",
        model="m",
        prompt_tokens=1,
        completion_tokens=0,
        price_in_micro=500_000,  # 0.50 per million
        price_out_micro=500_000,
    )
    # 1 * 500000 / 1000000 = 0.5 micro-units -> rounds up to 1.
    assert event.cost_micro == 1


def test_unpriced_model_has_no_cost() -> None:
    event = UsageEvent(user_id=None, purpose="EMBED", model="m", prompt_tokens=10**6)
    assert not event.priced
    assert event.cost_micro == 0


def test_price_conversion_round_trips() -> None:
    assert price_to_micro(Decimal("2.5")) == 2_500_000
    assert price_to_micro(Decimal("0.000001")) == 1
    assert micro_to_price(2_500_000) == Decimal("2.5")


# --- Price input (15.3) ---------------------------------------------------------------------


def test_price_accepts_up_to_six_decimals() -> None:
    price = PriceIn(price_in_per_million=Decimal("0.123456"), price_out_per_million=Decimal(0))
    assert price_to_micro(price.price_in_per_million) == 123_456


@pytest.mark.parametrize(
    ("price_in", "price_out"),
    [
        (Decimal("0.1234567"), Decimal("1")),  # finer than a micro-unit
        (Decimal("-1"), Decimal("1")),  # negative
        (Decimal("1"), Decimal("1000001")),  # above the ceiling
    ],
)
def test_price_rejects_invalid_values(price_in: Decimal, price_out: Decimal) -> None:
    with pytest.raises(ValidationError):
        PriceIn(price_in_per_million=price_in, price_out_per_million=price_out)


# --- Metering in the gateway (15.2) ---------------------------------------------------------


class _Recording:
    """A usage sink that keeps the events it was given."""

    def __init__(self) -> None:
        self.events: list[UsageEvent] = []

    async def record(self, event: UsageEvent) -> None:
        self.events.append(event)


class _Transport:
    """Answers the way LiteLLM does: the content, and the counts the endpoint reports."""

    def __init__(self, usage: dict[str, int] | None, *, fail: bool = False) -> None:
        self._usage = usage
        self._fail = fail

    async def acompletion(self, model: str, messages: list[Any], **kwargs: Any) -> Any:
        if self._fail:
            raise RuntimeError("connection refused")
        usage = None if self._usage is None else SimpleNamespace(**self._usage)
        message = SimpleNamespace(content="odpowiedź")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)

    async def aembedding(self, model: str, input: list[str], **kwargs: Any) -> Any:
        usage = SimpleNamespace(prompt_tokens=7, total_tokens=7)
        return SimpleNamespace(data=[{"embedding": [1.0, 0.0]} for _ in input], usage=usage)


def _priced_gateway(sink: _Recording, transport: _Transport) -> LiteLLMGateway:
    deployment = Deployment(
        name="cloud-chat",
        model="gpt-x",
        base_url="http://llm:11434/v1",
        api_key=None,
        local=True,
        private_data=True,
        price_in_micro=2_000_000,  # 2.00 per million prompt tokens
        price_out_micro=8_000_000,  # 8.00 per million completion tokens
    )
    config = ResolvedLlmConfig(1, {deployment.name: deployment}, {LLMPurpose.RAG: deployment.name})
    return LiteLLMGateway(LlmSettings(), transport, source=StaticConfig(config), usage=sink)


async def test_a_call_is_counted_for_its_owner_with_the_reported_tokens() -> None:
    sink = _Recording()
    owner = uuid4()
    gateway = _priced_gateway(
        sink, _Transport({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
    )
    with usage_owner(owner):
        await gateway.complete(MESSAGES, purpose=LLMPurpose.RAG)
    (event,) = sink.events
    assert event.user_id == owner
    assert (event.purpose, event.model) == ("RAG", "cloud-chat")
    assert (event.prompt_tokens, event.completion_tokens) == (10, 5)
    assert event.cost_micro == 10 * 2 + 5 * 8


async def test_work_without_an_owner_is_counted_as_system() -> None:
    sink = _Recording()
    gateway = _priced_gateway(sink, _Transport({"prompt_tokens": 1, "completion_tokens": 1}))
    await gateway.complete(MESSAGES, purpose=LLMPurpose.RAG)
    assert [event.user_id for event in sink.events] == [None]


async def test_an_embedding_call_is_counted() -> None:
    sink = _Recording()
    owner = uuid4()
    gateway = _priced_gateway(sink, _Transport(None))
    with usage_owner(owner):
        await gateway.embed(["tekst"], purpose=LLMPurpose.RAG, private_data=True)
    (event,) = sink.events
    assert (event.user_id, event.prompt_tokens) == (owner, 7)


async def test_a_failed_call_is_not_counted() -> None:
    sink = _Recording()
    gateway = _priced_gateway(sink, _Transport(None, fail=True))
    with pytest.raises(ModelUnavailable):
        await gateway.complete(MESSAGES, purpose=LLMPurpose.RAG)
    assert sink.events == []


async def test_a_failed_usage_write_never_fails_the_call() -> None:
    def broken_factory() -> Any:
        raise RuntimeError("database unavailable")

    recorder = UsageRecorder(broken_factory)  # type: ignore[arg-type]  # the factory is the fault
    await recorder.record(UsageEvent(user_id=None, purpose="RAG", model="m", prompt_tokens=1))


async def test_buffered_events_are_written_in_order_on_flush() -> None:
    buffered = BufferedUsage()
    first = UsageEvent(user_id=None, purpose="OCR", model="a", prompt_tokens=1)
    second = UsageEvent(user_id=None, purpose="OCR", model="b", prompt_tokens=2)
    assert not buffered.pending
    await buffered.record(first)
    await buffered.record(second)
    assert buffered.pending
    sink = _Recording()
    await buffered.flush(sink)
    assert sink.events == [first, second]
    assert not buffered.pending


async def test_a_request_owner_does_not_leak_into_another_request() -> None:
    seen: list[object] = []
    first, second = uuid4(), uuid4()

    async def request(user: object) -> None:
        set_usage_owner(user)  # type: ignore[arg-type]  # UUIDs in this test
        await asyncio.sleep(0)
        seen.append(current_usage_owner())

    await asyncio.gather(request(first), request(second))
    assert seen == [first, second]
    assert current_usage_owner() is None


def test_a_job_owner_is_restored_after_the_job() -> None:
    outer, inner = uuid4(), uuid4()
    with usage_owner(outer):
        with usage_owner(inner):
            assert current_usage_owner() == inner
        assert current_usage_owner() == outer
    assert current_usage_owner() is None


def test_period_defaults_to_the_last_thirty_days() -> None:
    start, end = period(None, None)
    assert (end - start).days == 29


def test_period_rejects_reversed_dates() -> None:
    with pytest.raises(HTTPException) as caught:
        period(date(2026, 2, 2), date(2026, 2, 1))
    assert caught.value.detail == {"error": "usage_period_invalid"}


def test_period_accepts_a_year_and_rejects_longer() -> None:
    assert period(date(2025, 1, 1), date(2026, 1, 1)) == (date(2025, 1, 1), date(2026, 1, 1))
    with pytest.raises(HTTPException) as caught:
        period(date(2025, 1, 1), date(2026, 1, 2))
    assert caught.value.detail == {"error": "usage_period_too_long"}
