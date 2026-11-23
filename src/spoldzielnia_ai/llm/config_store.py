"""The ACTIVE LLM configuration revision as a ``ConfigSource`` (7.2): hot reload, no restart.

Each process (api, workers, CLI jobs) keeps the resolved configuration in memory and looks again
when either

- ``llm.config_ttl_seconds`` passed since the last look (safety net), or
- the Redis key ``llm:config:revision`` names a newer revision (set by activation and rollback,
  so a change reaches running processes on their next call), or
- the Redis key ``llm:config:clearance`` changed (set when a provider is cleared or un-cleared,
  10.1: un-clearing must take effect at once, without a new revision).

A look is one indexed ``SELECT`` of the ACTIVE row and one of its providers, and decrypting
keys only happens when the number or a provider's clearance changed. Without an ACTIVE
revision (nothing seeded yet) the process uses ``.env``. If the database cannot be read, the
last good configuration stays in use.

Loading re-derives every endpoint's locality from the allowlists (S4) and its clearance from the
live provider row and ``llm.private_data_endpoints`` (P1/P2) instead of trusting the snapshot,
and drops deployments that are no longer allowed: a tampered snapshot can make a purpose
unavailable, but never send it somewhere it may not go.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.core.metrics import LLM_CONFIG_REVISION
from spoldzielnia_ai.db.enums import LlmCapability, LlmProviderKind, LlmRevisionStatus
from spoldzielnia_ai.db.models import LlmConfigRevision, LlmProvider
from spoldzielnia_ai.llm.config_service import (
    CLEARANCE_KEY,
    REVISION_KEY,
    provider_api_key,
    provider_cleared,
)
from spoldzielnia_ai.llm.endpoints import EndpointNotAllowed, classify
from spoldzielnia_ai.llm.gateway import Deployment, LLMPurpose, ResolvedLlmConfig, env_config
from spoldzielnia_ai.llm.snapshot import ConfigSnapshot

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from uuid import UUID

    from redis.asyncio import Redis

    from spoldzielnia_ai.core.crypto import EnvelopeCipher
    from spoldzielnia_ai.core.settings import LlmSettings
    from spoldzielnia_ai.db.session import SessionFactory

log = get_logger(__name__)


def _fingerprint(providers: Mapping[UUID, LlmProvider]) -> frozenset[tuple[UUID, bool, str]]:
    return frozenset((p.id, p.private_data_cleared, p.base_url) for p in providers.values())


def resolve(
    number: int,
    snapshot: ConfigSnapshot,
    providers: Mapping[UUID, LlmProvider],
    cipher: EnvelopeCipher,
    settings: LlmSettings,
) -> ResolvedLlmConfig:
    deployments: dict[str, Deployment] = {}
    names: dict[str, str] = {}  # snapshot key -> deployment name
    for key, item in snapshot.deployments.items():
        provider = providers.get(item.provider_id)
        if provider is None or provider.deleted_at is not None:  # removed (11.1)
            log.error(
                "llm.config_provider_missing", revision=number, provider=str(item.provider_id)
            )
            continue
        try:
            kind = classify(item.base_url, settings)
        except EndpointNotAllowed as exc:
            log.error("llm.config_endpoint_refused", revision=number, reason=exc.reason)
            continue
        try:
            api_key = provider_api_key(provider, kind, cipher, settings)
        except Exception as exc:  # InvalidTag: wrong KEK or a copied ciphertext
            log.error("llm.config_key_unreadable", revision=number, error=type(exc).__name__)
            continue
        name = item.display_name
        if name in deployments:  # the same model name under another capability
            name = f"{name}@{item.capability.value.lower()}"
        names[key] = name
        deployments[name] = Deployment(
            name=name,
            model=item.model,
            base_url=item.base_url,
            api_key=api_key,
            local=kind == LlmProviderKind.LOCAL,
            private_data=provider_cleared(provider, settings),
            temperature=item.temperature if item.capability != LlmCapability.EMBED else None,
            max_tokens=item.max_tokens,
            structured_output=item.structured_output,
            timeout_seconds=item.timeout_seconds,
            provider=item.litellm_provider,
            price_in_micro=item.price_in_micro,
            price_out_micro=item.price_out_micro,
        )
    routes = {
        LLMPurpose(purpose.value): names[key]
        for purpose, key in snapshot.routes.items()
        if key in names
    }
    return ResolvedLlmConfig(number, deployments, routes)


class LlmConfigStore:
    def __init__(
        self,
        factory: SessionFactory,
        cipher: EnvelopeCipher,
        settings: LlmSettings,
        redis: Redis | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._factory = factory
        self._cipher = cipher
        self._settings = settings
        self._redis = redis
        self._clock = clock
        self._env = env_config(settings)
        self._config = self._env
        self._checked_at: float | None = None
        self._providers: frozenset[tuple[UUID, bool, str]] = frozenset()
        self._clearance_seen: Any = None
        self._lock = asyncio.Lock()

    @property
    def cached(self) -> ResolvedLlmConfig:
        """The last loaded configuration, without looking (for code on another event loop)."""
        return self._env if self._settings.config_source == "env" else self._config

    async def current(self) -> ResolvedLlmConfig:
        if self._settings.config_source == "env":
            return self._env
        if await self._stale():
            async with self._lock:
                if await self._stale():
                    await self._reload()
        return self._config

    async def _stale(self) -> bool:
        if (
            self._checked_at is None
            or self._clock() - self._checked_at >= self._settings.config_ttl_seconds
        ):
            return True
        if self._redis is None:
            return False
        try:
            published, clearance = await self._redis.mget([REVISION_KEY, CLEARANCE_KEY])
        except Exception:
            return False  # Redis down: the TTL still bounds staleness
        if clearance is not None and clearance != self._clearance_seen:
            return True
        try:
            return published is not None and int(published) > self._config.revision
        except ValueError:
            return False

    async def _reload(self) -> None:
        if self._redis is not None:
            with contextlib.suppress(Exception):  # Redis down: the TTL still bounds staleness
                self._clearance_seen = await self._redis.get(CLEARANCE_KEY)
        try:
            async with self._factory() as session:
                row = (
                    await session.execute(
                        select(LlmConfigRevision.number, LlmConfigRevision.snapshot).where(
                            LlmConfigRevision.status == LlmRevisionStatus.ACTIVE
                        )
                    )
                ).first()
                if row is None or row.snapshot is None:
                    config = self._env
                else:
                    snapshot = ConfigSnapshot.model_validate(row.snapshot)
                    ids = {d.provider_id for d in snapshot.deployments.values()}
                    providers = {
                        p.id: p
                        for p in await session.scalars(
                            select(LlmProvider).where(LlmProvider.id.in_(ids))
                        )
                    }
                    fingerprint = _fingerprint(providers)
                    if row.number == self._config.revision and fingerprint == self._providers:
                        config = self._config
                    else:
                        config = resolve(
                            row.number, snapshot, providers, self._cipher, self._settings
                        )
                        self._providers = fingerprint
        except Exception as exc:
            log.error("llm.config_load_failed", error=type(exc).__name__)
            self._checked_at = self._clock()  # keep the last good one, retry after the TTL
            return
        if config is not self._config:
            log.info(
                "llm.config_loaded",
                revision=config.revision,
                routes=len(config.routes),
                cleared=sorted(d.name for d in config.deployments.values() if d.cleared),
            )
        self._config = config
        self._checked_at = self._clock()
        LLM_CONFIG_REVISION.set(config.revision)
