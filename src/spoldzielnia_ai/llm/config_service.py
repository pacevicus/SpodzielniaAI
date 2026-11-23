"""Admin-managed LLM configuration (7.1): providers, models, revisions, activation, rollback.

Every write is one transaction together with its audit entry. The rules enforced here are
enforced again by the database (migration 0012) and by the gateway at call time (7.2):

- P1 (10.1, was S1): purposes in ``PRIVATE_PURPOSES`` only ever use providers cleared for
  private data. Clearing needs an endpoint on ``llm.private_data_endpoints`` (P2) and records
  region, retention and contract (P3); un-clearing is never blocked and reaches every process
  at once (``llm:config:clearance``).
- S2: cloud providers can be stored, but not activated unless ``llm.allow_cloud``.
- S3/S4: base URLs must be on the endpoint allowlists; ``kind`` follows from the match.
- S5: API keys are envelope-encrypted (AAD ``llm_provider:<id>``), write-only, never audited.
- R1 (11.1): a provider is removed only when nothing running needs it (the ACTIVE revision, a
  SERVING or BUILDING index generation, an open draft). One that no activated revision ever
  used is deleted with its models; one that history names is archived (``deleted_at``): key
  erased, clearance revoked, models disabled, hidden, and never routed to again (migration 0026).

Revisions: a DRAFT holds ``llm_routing`` rows. Activation freezes the routes into a snapshot
(``llm.snapshot``) and requires a passing probe, at most ``llm.probe_max_age_hours`` old, of
every model that takes over a purpose (routes that stay as they are need none). Rollback
re-activates an older snapshot as a new revision, so the history stays linear. When no revision
exists yet, ``ensure_seeded`` imports ``.env`` as revision 1, so the runtime keeps behaving
exactly as before.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, SecretStr, field_validator
from sqlalchemy import func, select, text

from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.audit.service import AuditService, AuditTarget
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.enums import (
    IndexGenerationStatus,
    LlmCapability,
    LlmProviderKind,
    LlmRevisionStatus,
    LlmRoutePurpose,
)
from spoldzielnia_ai.db.models import (
    IndexGeneration,
    LlmConfigRevision,
    LlmModel,
    LlmProvider,
    LlmRouting,
)
from spoldzielnia_ai.llm.endpoints import (
    EndpointNotAllowed,
    classify,
    may_receive_private_data,
    normalize_base_url,
)
from spoldzielnia_ai.llm.gateway import PRIVATE_PURPOSES, LLMPurpose
from spoldzielnia_ai.llm.snapshot import ConfigSnapshot, SnapshotDeployment
from spoldzielnia_ai.llm.usage import price_to_micro

if TYPE_CHECKING:
    from pydantic import JsonValue
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession

    from spoldzielnia_ai.core.crypto import EnvelopeCipher
    from spoldzielnia_ai.core.settings import LlmSettings
    from spoldzielnia_ai.db.session import SessionFactory

log = get_logger(__name__)

PROVIDER_TABLE = "llm_provider"
REVISION_KEY = "llm:config:revision"  # Redis: the active revision number (7.2 reload signal)
CLEARANCE_KEY = "llm:config:clearance"  # Redis: changes when a provider's clearance does (10.1)
_LOCK = "llm_config"
_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
LITELLM_PROVIDERS = frozenset({"openai"})  # OpenAI-compatible APIs (gap Q1 of 07)

PRIVATE_ROUTE_PURPOSES = frozenset(
    p for p in LlmRoutePurpose if LLMPurpose(p.value) in PRIVATE_PURPOSES
)

StructuredOutput = Literal["json_schema", "prompt"]
# Models that generate text from a prompt (temperature and token limit apply).
_GENERATES = frozenset({LlmCapability.CHAT, LlmCapability.VLM})
# Models that return vectors: their probe records the dimension.
_EMBEDS = frozenset({LlmCapability.EMBED, LlmCapability.IMAGE_EMBED})


def capability_for(purpose: LlmRoutePurpose) -> LlmCapability:
    if purpose in (LlmRoutePurpose.VLM, LlmRoutePurpose.OCR):
        return LlmCapability.VLM
    if purpose == LlmRoutePurpose.EMBED:
        return LlmCapability.EMBED
    if purpose == LlmRoutePurpose.TRANSCRIBE:
        return LlmCapability.TRANSCRIBE
    if purpose == LlmRoutePurpose.IMAGE_EMBED:
        return LlmCapability.IMAGE_EMBED
    return LlmCapability.CHAT


class ConfigError(Exception):
    """``code`` names the rule; ``status`` is the HTTP status the API answers with."""

    def __init__(self, code: str, status: int = 422, **detail: Any) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.detail = detail


# --- Inputs (also the API request bodies) -------------------------------------------------------


class ProviderIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=1, max_length=512)
    litellm_provider: str = "openai"
    api_key: SecretStr | None = Field(default=None, max_length=512)
    structured_output: StructuredOutput | None = None
    timeout_seconds: float | None = Field(default=None, gt=0, le=600)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not _NAME.fullmatch(value):
            raise ValueError("lowercase letters, digits, '.', '_' and '-' only")
        return value


class ProviderPatch(BaseModel):
    """Only the fields that are set change. ``clear_api_key`` removes the stored key."""

    base_url: str | None = Field(default=None, min_length=1, max_length=512)
    api_key: SecretStr | None = Field(default=None, max_length=512)
    clear_api_key: bool = False
    structured_output: StructuredOutput | None = None
    timeout_seconds: float | None = Field(default=None, gt=0, le=600)


class PriceIn(BaseModel):
    """What one million prompt and completion tokens cost, in the usage currency (15.3).
    Both prices or none; at most six decimals (micro-units in the database)."""

    price_in_per_million: Decimal = Field(ge=0, le=1_000_000)
    price_out_per_million: Decimal = Field(ge=0, le=1_000_000)

    @field_validator("price_in_per_million", "price_out_per_million")
    @classmethod
    def _decimals(cls, value: Decimal) -> Decimal:
        if value != value.quantize(Decimal("0.000001")):
            raise ValueError("at most six decimal places")
        return value


class ModelIn(BaseModel):
    provider_id: UUID
    model_name: str = Field(min_length=1, max_length=256)
    capability: LlmCapability
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=131072)
    embed_dimension: int | None = Field(default=None, ge=1, le=16384)
    notes: str | None = Field(default=None, max_length=500)
    price: PriceIn | None = None

    @field_validator("model_name")
    @classmethod
    def _model_name(cls, value: str) -> str:
        value = value.strip()
        if not value or _CONTROL.search(value):
            raise ValueError("invalid model name")
        return value


class ClearanceIn(BaseModel):
    """What members are shown about a provider that receives their private data (P3, P4)."""

    region: str = Field(min_length=2, max_length=64)
    retention: str = Field(min_length=2, max_length=64)
    contract_ref: str = Field(min_length=2, max_length=200)

    @field_validator("region", "retention", "contract_ref")
    @classmethod
    def _text(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2 or _CONTROL.search(value):
            raise ValueError("invalid text")
        return value


class ModelPatch(BaseModel):
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=131072)
    embed_dimension: int | None = Field(default=None, ge=1, le=16384)
    notes: str | None = Field(default=None, max_length=500)
    # Sent as ``null`` (explicitly) the model becomes unpriced again.
    price: PriceIn | None = None


@dataclass(frozen=True, slots=True)
class Removal:
    """Whether a provider can be removed now (R1), and how. ``blocked`` is the error code
    ``delete_provider`` would answer with; ``archive`` means history names it, so it is
    archived instead of deleted; ``draft`` is the open draft that uses it."""

    blocked: str | None = None
    archive: bool = False
    draft: int | None = None


@dataclass(frozen=True, slots=True)
class RevisionView:
    revision: LlmConfigRevision
    routing: dict[LlmRoutePurpose, UUID]


# --- Keys ---------------------------------------------------------------------------------------


def _aad(provider_id: UUID) -> str:
    return f"{PROVIDER_TABLE}:{provider_id}"


def _hint(key: str) -> str | None:
    return key[-4:] if len(key) >= 12 else None


def provider_api_key(
    provider: LlmProvider, kind: LlmProviderKind, cipher: EnvelopeCipher, settings: LlmSettings
) -> str | None:
    """The plaintext key for a call (never logged). ``kind`` is the freshly derived one."""
    if provider.inherit_env_key:
        secret = settings.api_key if kind == LlmProviderKind.LOCAL else settings.cloud_api_key
        return (secret.get_secret_value().strip() or None) if secret else None
    if provider.api_key_wrapped is None or provider.api_key_ciphertext is None:
        return None
    aad = _aad(provider.id)
    dek = cipher.unwrap(provider.api_key_wrapped, aad)
    return cipher.decrypt_field(dek, provider.api_key_ciphertext, aad, "api_key").decode()


def provider_cleared(provider: LlmProvider, settings: LlmSettings) -> bool:
    """Cleared by an admin, and still within the operator's ceiling (P1/P2)."""
    return bool(provider.private_data_cleared) and may_receive_private_data(
        provider.base_url, settings
    )


def _set_key(provider: LlmProvider, key: str | None, cipher: EnvelopeCipher) -> None:
    key = (key or "").strip() or None
    if key is None:
        provider.api_key_wrapped = provider.api_key_ciphertext = provider.api_key_hint = None
        return
    aad = _aad(provider.id)
    data_key = cipher.new_key(aad)
    provider.api_key_wrapped = data_key.wrapped
    provider.api_key_ciphertext = cipher.encrypt_field(data_key.key, key.encode(), aad, "api_key")
    provider.api_key_hint = _hint(key)
    provider.inherit_env_key = False


# --- Service ------------------------------------------------------------------------------------


class LlmConfigService:
    def __init__(
        self,
        factory: SessionFactory,
        cipher: EnvelopeCipher,
        settings: LlmSettings,
        *,
        redis: Redis | None = None,
        now: Any = None,
    ) -> None:
        self._factory = factory
        self._cipher = cipher
        self._settings = settings
        self._redis = redis
        self._now = now or (lambda: datetime.now(UTC))

    # --- helpers --------------------------------------------------------------------------------

    @property
    def settings(self) -> LlmSettings:
        return self._settings

    def cleared(self, provider: LlmProvider) -> bool:
        return provider_cleared(provider, self._settings)

    def kind_of(self, base_url: str) -> LlmProviderKind:
        try:
            return classify(base_url, self._settings)
        except EndpointNotAllowed as exc:
            raise ConfigError(exc.reason) from None

    @staticmethod
    async def _lock(session: AsyncSession) -> None:
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": _LOCK})

    @staticmethod
    async def _get[T](session: AsyncSession, cls: type[T], id_: UUID, what: str) -> T:
        row = await session.get(cls, id_)
        if row is None:
            raise ConfigError(f"{what}_not_found", 404)
        return row

    @classmethod
    async def _provider(cls, session: AsyncSession, provider_id: UUID) -> LlmProvider:
        """A provider that was not removed (an archived one is gone for the admin, R1)."""
        provider = await cls._get(session, LlmProvider, provider_id, "provider")
        if provider.deleted_at is not None:
            raise ConfigError("provider_not_found", 404)
        return provider

    @classmethod
    async def _model(cls, session: AsyncSession, model_id: UUID) -> LlmModel:
        model = await cls._get(session, LlmModel, model_id, "model")
        provider = await session.get(LlmProvider, model.provider_id)
        if provider is None or provider.deleted_at is not None:
            raise ConfigError("model_not_found", 404)
        return model

    @staticmethod
    async def _revision(session: AsyncSession, number: int) -> LlmConfigRevision:
        row = await session.scalar(
            select(LlmConfigRevision).where(LlmConfigRevision.number == number)
        )
        if row is None:
            raise ConfigError("revision_not_found", 404)
        return row

    @staticmethod
    async def _active(session: AsyncSession) -> LlmConfigRevision | None:
        return await session.scalar(
            select(LlmConfigRevision).where(LlmConfigRevision.status == LlmRevisionStatus.ACTIVE)
        )

    @staticmethod
    async def _routing(session: AsyncSession, revision_id: UUID) -> dict[LlmRoutePurpose, UUID]:
        rows = await session.scalars(
            select(LlmRouting).where(LlmRouting.revision_id == revision_id)
        )
        return {r.purpose: r.model_id for r in rows}

    async def _active_snapshot(self, session: AsyncSession) -> ConfigSnapshot | None:
        active = await self._active(session)
        if active is None or active.snapshot is None:
            return None
        return ConfigSnapshot.model_validate(active.snapshot)

    async def _publish_clearance(self) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.incr(CLEARANCE_KEY)
        except Exception as exc:
            log.warning("llm.config_publish_failed", error=type(exc).__name__)

    async def _publish(self, number: int) -> None:
        """Tell every process to reload now (they also re-check on their own, 7.2)."""
        if self._redis is None:
            return
        try:
            await self._redis.set(REVISION_KEY, str(number))
        except Exception as exc:
            log.warning("llm.config_publish_failed", error=type(exc).__name__)

    # --- reads ----------------------------------------------------------------------------------

    async def providers(self) -> list[LlmProvider]:
        async with self._factory() as session:
            return list(
                await session.scalars(
                    select(LlmProvider)
                    .where(LlmProvider.deleted_at.is_(None))
                    .order_by(LlmProvider.name)
                )
            )

    async def models(self) -> list[LlmModel]:
        async with self._factory() as session:
            return list(
                await session.scalars(
                    select(LlmModel)
                    .join(LlmProvider, LlmProvider.id == LlmModel.provider_id)
                    .where(LlmProvider.deleted_at.is_(None))
                    .order_by(LlmModel.provider_id, LlmModel.model_name)
                )
            )

    async def model(self, model_id: UUID) -> tuple[LlmModel, LlmProvider]:
        async with self._factory() as session:
            model = await self._model(session, model_id)
            provider = await self._provider(session, model.provider_id)
            return model, provider

    async def provider(self, provider_id: UUID) -> LlmProvider:
        async with self._factory() as session:
            return await self._provider(session, provider_id)

    def api_key(self, provider: LlmProvider) -> str | None:
        return provider_api_key(
            provider, self.kind_of(provider.base_url), self._cipher, self._settings
        )

    async def revisions(self, limit: int = 100) -> list[LlmConfigRevision]:
        async with self._factory() as session:
            return list(
                await session.scalars(
                    select(LlmConfigRevision).order_by(LlmConfigRevision.number.desc()).limit(limit)
                )
            )

    async def revision(self, number: int) -> RevisionView:
        async with self._factory() as session:
            revision = await self._revision(session, number)
            return RevisionView(revision, await self._routing(session, revision.id))

    async def active(self) -> RevisionView | None:
        async with self._factory() as session:
            revision = await self._active(session)
            if revision is None:
                return None
            return RevisionView(revision, await self._routing(session, revision.id))

    async def models_in_active(self) -> set[UUID]:
        async with self._factory() as session:
            snapshot = await self._active_snapshot(session)
        return {d.model_id for d in snapshot.deployments.values()} if snapshot else set()

    # --- providers ------------------------------------------------------------------------------

    async def create_provider(self, actor: UUID | None, data: ProviderIn) -> LlmProvider:
        base_url = self._url(data.base_url)
        kind = self.kind_of(base_url)
        if data.litellm_provider not in LITELLM_PROVIDERS:
            raise ConfigError("provider_type_not_allowed")
        async with self._factory() as session, session.begin():
            taken = select(LlmProvider.id).where(
                LlmProvider.name == data.name, LlmProvider.deleted_at.is_(None)
            )
            if await session.scalar(taken):
                raise ConfigError("name_taken", 409)
            provider = LlmProvider(
                name=data.name,
                kind=kind,
                base_url=base_url,
                litellm_provider=data.litellm_provider,
                structured_output=data.structured_output,
                timeout_seconds=data.timeout_seconds,
                created_by=actor,
            )
            session.add(provider)
            await session.flush()  # the id is the key's AAD
            _set_key(
                provider, data.api_key.get_secret_value() if data.api_key else None, self._cipher
            )
            await AuditService(session).record(
                EventType.LLM_PROVIDER_CHANGED,
                actor,
                AuditTarget(PROVIDER_TABLE, str(provider.id)),
                {
                    "action": "created",
                    "name": provider.name,
                    "kind": kind.value,
                    "base_url": base_url,
                    "key_set": provider.api_key_wrapped is not None,
                },
            )
        return provider

    def _url(self, url: str) -> str:
        try:
            return normalize_base_url(url)
        except EndpointNotAllowed as exc:
            raise ConfigError(exc.reason) from None

    async def update_provider(
        self, actor: UUID | None, provider_id: UUID, patch: ProviderPatch
    ) -> LlmProvider:
        fields = patch.model_fields_set
        async with self._factory() as session, session.begin():
            await self._lock(session)
            provider = await self._provider(session, provider_id)
            changes: dict[str, JsonValue] = {}
            if "base_url" in fields and patch.base_url is not None:
                base_url = self._url(patch.base_url)
                kind = self.kind_of(base_url)
                if provider.private_data_cleared and base_url != provider.base_url:
                    raise ConfigError("provider_cleared", 409)  # un-clear first (P3)
                provider.base_url, provider.kind = base_url, kind
                changes |= {"base_url": base_url, "kind": kind.value}
            if "structured_output" in fields:
                provider.structured_output = patch.structured_output
                changes["structured_output"] = patch.structured_output
            if "timeout_seconds" in fields:
                provider.timeout_seconds = patch.timeout_seconds
                changes["timeout_seconds"] = patch.timeout_seconds
            if patch.clear_api_key or (fields & {"api_key"} and patch.api_key is not None):
                key = None if patch.clear_api_key else patch.api_key
                _set_key(provider, key.get_secret_value() if key else None, self._cipher)
                if patch.clear_api_key:
                    provider.inherit_env_key = False
                changes["key_changed"] = True
            await AuditService(session).record(
                EventType.LLM_PROVIDER_CHANGED,
                actor,
                AuditTarget(PROVIDER_TABLE, str(provider.id)),
                {"action": "updated", "name": provider.name, **changes},
            )
        return provider

    async def clear_provider(
        self, actor: UUID | None, provider_id: UUID, data: ClearanceIn
    ) -> LlmProvider:
        """Allow private purposes to use the provider (P2/P3). The API checks step-up."""
        async with self._factory() as session, session.begin():
            await self._lock(session)
            provider = await self._provider(session, provider_id)
            if provider.disabled_at is not None:
                raise ConfigError("provider_disabled", 409)
            kind = self.kind_of(provider.base_url)
            if not may_receive_private_data(provider.base_url, self._settings):
                raise ConfigError("endpoint_not_clearable", 409)
            if kind == LlmProviderKind.CLOUD and not self._settings.allow_cloud:
                raise ConfigError("cloud_not_allowed", 409)
            provider.private_data_cleared = True
            provider.cleared_region = data.region
            provider.cleared_retention = data.retention
            provider.cleared_contract_ref = data.contract_ref
            provider.cleared_by = actor
            provider.cleared_at = self._now()
            await AuditService(session).record(
                EventType.LLM_PROVIDER_CLEARED,
                actor,
                AuditTarget(PROVIDER_TABLE, str(provider.id)),
                {
                    "name": provider.name,
                    "kind": kind.value,
                    "base_url": provider.base_url,
                    "region": data.region,
                    "retention": data.retention,
                    "contract_ref": data.contract_ref,
                },
            )
        await self._publish_clearance()
        return provider

    async def unclear_provider(self, actor: UUID | None, provider_id: UUID) -> LlmProvider:
        """Never blocked by routes: the purposes it served stop at once (no fallback, P5)."""
        async with self._factory() as session, session.begin():
            await self._lock(session)
            provider = await self._provider(session, provider_id)
            if not provider.private_data_cleared:
                return provider
            provider.private_data_cleared = False
            provider.cleared_by = actor
            provider.cleared_at = self._now()
            snapshot = await self._active_snapshot(session)
            affected = sorted(
                p.value
                for p in PRIVATE_ROUTE_PURPOSES
                if snapshot is not None
                and (d := snapshot.deployment_for(p)) is not None
                and d.provider_id == provider.id
            )
            await AuditService(session).record(
                EventType.LLM_PROVIDER_UNCLEARED,
                actor,
                AuditTarget(PROVIDER_TABLE, str(provider.id)),
                {"name": provider.name, "purposes_stopped": affected},  # pyright: ignore[reportArgumentType]
            )
        await self._publish_clearance()
        return provider

    async def set_provider_disabled(
        self, actor: UUID | None, provider_id: UUID, disabled: bool
    ) -> LlmProvider:
        async with self._factory() as session, session.begin():
            await self._lock(session)
            provider = await self._provider(session, provider_id)
            snapshot = await self._active_snapshot(session)
            if (
                disabled
                and snapshot
                and any(d.provider_id == provider_id for d in snapshot.deployments.values())
            ):
                raise ConfigError("provider_in_use", 409)
            provider.disabled_at = self._now() if disabled else None
            await AuditService(session).record(
                EventType.LLM_PROVIDER_CHANGED,
                actor,
                AuditTarget(PROVIDER_TABLE, str(provider.id)),
                {"action": "disabled" if disabled else "enabled", "name": provider.name},
            )
        return provider

    async def removals(self) -> dict[UUID, Removal]:
        """For the provider list: which providers can be removed now, and how (R1)."""
        async with self._factory() as session:
            return await self._removals(session)

    async def _removals(self, session: AsyncSession) -> dict[UUID, Removal]:
        snapshot = await self._active_snapshot(session)
        in_use: set[UUID] = (
            {d.provider_id for d in snapshot.deployments.values()} if snapshot else set()
        )
        live = IndexGeneration.status.in_(
            (IndexGenerationStatus.SERVING, IndexGenerationStatus.BUILDING)
        )
        in_index: set[UUID] = set()
        for deployment in await session.scalars(select(IndexGeneration.deployment).where(live)):
            if deployment is not None and deployment.get("provider_id"):
                in_index.add(UUID(str(deployment["provider_id"])))
        drafts: dict[UUID, int] = {}
        history: set[UUID] = set()
        rows = await session.execute(
            select(LlmModel.provider_id, LlmConfigRevision.number, LlmConfigRevision.status)
            .join(LlmRouting, LlmRouting.model_id == LlmModel.id)
            .join(LlmConfigRevision, LlmConfigRevision.id == LlmRouting.revision_id)
        )
        for provider_id, number, revision_status in rows:
            if revision_status == LlmRevisionStatus.DRAFT:
                drafts.setdefault(provider_id, number)
            else:
                history.add(provider_id)
        # Revisions seeded from ``.env`` name their providers only in the snapshot.
        for item in await session.scalars(
            select(LlmConfigRevision.snapshot).where(LlmConfigRevision.snapshot.is_not(None))
        ):
            if item is not None:
                old = ConfigSnapshot.model_validate(item)
                history |= {d.provider_id for d in old.deployments.values()}
        result: dict[UUID, Removal] = {}
        for provider_id in await session.scalars(
            select(LlmProvider.id).where(LlmProvider.deleted_at.is_(None))
        ):
            blocked = (
                "provider_in_use"
                if provider_id in in_use
                else "provider_in_index"
                if provider_id in in_index
                else "provider_in_draft"
                if provider_id in drafts
                else None
            )
            result[provider_id] = Removal(blocked, provider_id in history, drafts.get(provider_id))
        return result

    async def delete_provider(
        self, actor: UUID | None, provider_id: UUID
    ) -> Literal["deleted", "archived"]:
        """Remove a provider nothing running needs (R1): deleted with its models, or archived
        when a past revision used it. No step-up: removing only narrows where data can go."""
        async with self._factory() as session, session.begin():
            await self._lock(session)
            provider = await self._provider(session, provider_id)
            removal = (await self._removals(session))[provider.id]
            if removal.blocked is not None:
                detail = {"draft": removal.draft} if removal.draft is not None else {}
                raise ConfigError(removal.blocked, 409, **detail)
            models = list(
                await session.scalars(select(LlmModel).where(LlmModel.provider_id == provider.id))
            )
            was_cleared = provider.private_data_cleared
            now = self._now()
            mode: Literal["deleted", "archived"] = "archived" if removal.archive else "deleted"
            await AuditService(session).record(
                EventType.LLM_PROVIDER_DELETED,
                actor,
                AuditTarget(PROVIDER_TABLE, str(provider.id)),
                {
                    "name": provider.name,
                    "kind": provider.kind.value,
                    "base_url": provider.base_url,
                    "mode": mode,
                    "models": len(models),
                    "was_cleared": was_cleared,
                },
            )
            if mode == "deleted":
                for model in models:
                    await session.delete(model)
                await session.flush()
                await session.delete(provider)
            else:
                _set_key(provider, None, self._cipher)
                provider.inherit_env_key = False
                if was_cleared:
                    provider.private_data_cleared = False
                    provider.cleared_by, provider.cleared_at = actor, now
                provider.disabled_at = provider.disabled_at or now
                provider.deleted_at = now
                for model in models:
                    model.disabled_at = model.disabled_at or now
        if was_cleared:
            await self._publish_clearance()
        return mode

    # --- models ---------------------------------------------------------------------------------

    async def create_model(self, actor: UUID | None, data: ModelIn) -> LlmModel:
        if data.embed_dimension is not None and data.capability not in _EMBEDS:
            raise ConfigError("embed_dimension_only_for_embed")
        async with self._factory() as session, session.begin():
            provider = await self._provider(session, data.provider_id)
            if provider.disabled_at is not None:
                raise ConfigError("provider_disabled", 409)
            duplicate = await session.scalar(
                select(LlmModel.id).where(
                    LlmModel.provider_id == provider.id,
                    LlmModel.model_name == data.model_name,
                    LlmModel.capability == data.capability,
                )
            )
            if duplicate:
                raise ConfigError("model_exists", 409)
            model = LlmModel(
                provider_id=provider.id,
                model_name=data.model_name,
                capability=data.capability,
                temperature=data.temperature,
                max_tokens=data.max_tokens,
                embed_dimension=data.embed_dimension,
                notes=data.notes,
                created_by=actor,
                price_in_micro=None
                if data.price is None
                else price_to_micro(data.price.price_in_per_million),
                price_out_micro=None
                if data.price is None
                else price_to_micro(data.price.price_out_per_million),
            )
            session.add(model)
            await session.flush()
            await AuditService(session).record(
                EventType.LLM_MODEL_CHANGED,
                actor,
                AuditTarget("llm_model", str(model.id)),
                {
                    "action": "created",
                    "provider": provider.name,
                    "model": model.model_name,
                    "capability": model.capability.value,
                },
            )
        return model

    async def update_model(self, actor: UUID | None, model_id: UUID, patch: ModelPatch) -> LlmModel:
        fields = patch.model_fields_set
        async with self._factory() as session, session.begin():
            model = await self._model(session, model_id)
            if (
                "embed_dimension" in fields
                and patch.embed_dimension is not None
                and model.capability not in _EMBEDS
            ):
                raise ConfigError("embed_dimension_only_for_embed")
            changes: dict[str, JsonValue] = {}
            for name in ("temperature", "max_tokens", "embed_dimension", "notes"):
                if name in fields:
                    value = getattr(patch, name)
                    setattr(model, name, value)
                    changes[name] = value if name != "notes" else bool(value)
            if "price" in fields:
                model.price_in_micro = (
                    None
                    if patch.price is None
                    else price_to_micro(patch.price.price_in_per_million)
                )
                model.price_out_micro = (
                    None
                    if patch.price is None
                    else price_to_micro(patch.price.price_out_per_million)
                )
                changes["priced"] = patch.price is not None
            # New parameters, new behaviour: the last probe no longer vouches for it.
            if fields - {"notes", "price"}:
                model.last_probe_ok = None
            await AuditService(session).record(
                EventType.LLM_MODEL_CHANGED,
                actor,
                AuditTarget("llm_model", str(model.id)),
                {"action": "updated", "model": model.model_name, **changes},
            )
        return model

    async def set_model_disabled(
        self, actor: UUID | None, model_id: UUID, disabled: bool
    ) -> LlmModel:
        async with self._factory() as session, session.begin():
            await self._lock(session)
            model = await self._model(session, model_id)
            snapshot = await self._active_snapshot(session)
            if (
                disabled
                and snapshot
                and any(d.model_id == model_id for d in snapshot.deployments.values())
            ):
                raise ConfigError("model_in_use", 409)
            model.disabled_at = self._now() if disabled else None
            await AuditService(session).record(
                EventType.LLM_MODEL_CHANGED,
                actor,
                AuditTarget("llm_model", str(model.id)),
                {"action": "disabled" if disabled else "enabled", "model": model.model_name},
            )
        return model

    async def record_probe(
        self, actor: UUID | None, model_id: UUID, ok: bool, report: dict[str, JsonValue]
    ) -> LlmModel:
        async with self._factory() as session, session.begin():
            model = await self._model(session, model_id)
            model.last_probe_at = self._now()
            model.last_probe_ok = ok
            model.last_probe_report = dict(report)
            if model.capability in _EMBEDS and isinstance(report.get("dimension"), int):
                model.embed_dimension = int(report["dimension"])  # pyright: ignore[reportArgumentType]
            await AuditService(session).record(
                EventType.LLM_MODEL_PROBED,
                actor,
                AuditTarget("llm_model", str(model.id)),
                {"model": model.model_name, "ok": ok},
            )
        return model

    # --- revisions ------------------------------------------------------------------------------

    async def create_draft(
        self, actor: UUID | None, based_on: int | None, comment: str | None
    ) -> RevisionView:
        async with self._factory() as session, session.begin():
            await self._lock(session)
            source = (
                await self._revision(session, based_on)
                if based_on is not None
                else await self._active(session)
            )
            draft = LlmConfigRevision(
                status=LlmRevisionStatus.DRAFT,
                comment=comment,
                based_on=source.number if source else None,
                created_by=actor,
            )
            session.add(draft)
            await session.flush()
            routing = await self._routing(session, source.id) if source else {}
            for purpose, model_id in routing.items():
                session.add(LlmRouting(revision_id=draft.id, purpose=purpose, model_id=model_id))
            await session.flush()
            await session.refresh(draft)
            await AuditService(session).record(
                EventType.LLM_CONFIG_DRAFT_CHANGED,
                actor,
                AuditTarget("llm_config_revision", str(draft.number)),
                {"action": "created", "based_on": draft.based_on},
            )
            return RevisionView(draft, routing)

    async def set_routing(
        self,
        actor: UUID | None,
        number: int,
        routing: dict[LlmRoutePurpose, UUID | None],
        comment: str | None = None,
    ) -> RevisionView:
        """Replace the given purposes' models (``None`` removes the route). Validated here with
        readable errors; the database trigger repeats the local-only and capability checks."""
        async with self._factory() as session, session.begin():
            await self._lock(session)
            revision = await self._revision(session, number)
            if revision.status != LlmRevisionStatus.DRAFT:
                raise ConfigError("not_draft", 409)
            current = await self._routing(session, revision.id)
            for purpose, model_id in routing.items():
                if model_id is None:
                    if purpose in current:
                        row = await session.get(LlmRouting, (revision.id, purpose))
                        if row is not None:
                            await session.delete(row)
                    continue
                model = await self._get(session, LlmModel, model_id, "model")
                provider = await self._get(session, LlmProvider, model.provider_id, "provider")
                self._check_route(purpose, model, provider)
                row = await session.get(LlmRouting, (revision.id, purpose))
                if row is None:
                    session.add(
                        LlmRouting(revision_id=revision.id, purpose=purpose, model_id=model_id)
                    )
                else:
                    row.model_id = model_id
            if comment is not None:
                revision.comment = comment
            await session.flush()
            result = await self._routing(session, revision.id)
            await AuditService(session).record(
                EventType.LLM_CONFIG_DRAFT_CHANGED,
                actor,
                AuditTarget("llm_config_revision", str(number)),
                {
                    "action": "routing",
                    "routes": {p.value: str(m) for p, m in sorted(result.items())},
                },
            )
            return RevisionView(revision, result)

    def _check_route(
        self, purpose: LlmRoutePurpose, model: LlmModel, provider: LlmProvider
    ) -> LlmProviderKind:
        if provider.deleted_at is not None:
            raise ConfigError("provider_removed", 409, purpose=purpose.value)
        if model.capability != capability_for(purpose):
            raise ConfigError("capability_mismatch", purpose=purpose.value)
        if model.disabled_at is not None or provider.disabled_at is not None:
            raise ConfigError("model_disabled", purpose=purpose.value)
        kind = self.kind_of(provider.base_url)  # S4: derived again, not read from the row
        if provider.kind != kind:
            provider.kind = kind  # the allowlists changed since
        if purpose in PRIVATE_ROUTE_PURPOSES and not self.cleared(provider):
            raise ConfigError("provider_not_cleared", purpose=purpose.value)
        return kind

    async def delete_draft(self, actor: UUID | None, number: int) -> None:
        async with self._factory() as session, session.begin():
            revision = await self._revision(session, number)
            if revision.status != LlmRevisionStatus.DRAFT:
                raise ConfigError("not_draft", 409)
            await session.delete(revision)
            await AuditService(session).record(
                EventType.LLM_CONFIG_DRAFT_CHANGED,
                actor,
                AuditTarget("llm_config_revision", str(number)),
                {"action": "deleted"},
            )

    async def _snapshot(
        self,
        session: AsyncSession,
        routing: dict[LlmRoutePurpose, UUID],
        *,
        require_probe: bool,
        running: ConfigSnapshot | None = None,
    ) -> ConfigSnapshot:
        """Policy first (S1-S4, for every route), then probe freshness: the more useful error.
        Only changed routes need a fresh probe: a model that keeps its purpose from ``running``
        (the ACTIVE snapshot) is already in service, and a weak one elsewhere (e.g. an embedding
        model failing the separation check) must not block every other change."""
        deployments: dict[str, SnapshotDeployment] = {}
        routes: dict[LlmRoutePurpose, str] = {}
        unprobed: list[tuple[LlmRoutePurpose, LlmModel]] = []
        oldest = self._now() - timedelta(hours=self._settings.probe_max_age_hours)
        for purpose, model_id in sorted(routing.items()):
            model = await self._get(session, LlmModel, model_id, "model")
            provider = await self._get(session, LlmProvider, model.provider_id, "provider")
            kind = self._check_route(purpose, model, provider)
            if kind == LlmProviderKind.CLOUD and not self._settings.allow_cloud:
                raise ConfigError("cloud_not_allowed", purpose=purpose.value)
            current = running.deployment_for(purpose) if running is not None else None
            unchanged = current is not None and current.model_id == model.id
            if not unchanged and not (
                model.last_probe_ok
                and model.last_probe_at is not None
                and model.last_probe_at >= oldest
            ):
                unprobed.append((purpose, model))
            key = str(model.id)
            deployments[key] = SnapshotDeployment(
                provider_id=provider.id,
                provider_name=provider.name,
                model_id=model.id,
                model=model.model_name,
                capability=model.capability,
                base_url=provider.base_url,
                litellm_provider=provider.litellm_provider,
                kind=kind,
                private_data=self.cleared(provider),
                inherit_env_key=provider.inherit_env_key,
                temperature=model.temperature,
                max_tokens=model.max_tokens,
                structured_output=provider.structured_output,  # pyright: ignore[reportArgumentType]
                timeout_seconds=provider.timeout_seconds,
                embed_dimension=model.embed_dimension,
                price_in_micro=model.price_in_micro,
                price_out_micro=model.price_out_micro,
            )
            routes[purpose] = key
        if require_probe and unprobed:
            purpose, model = unprobed[0]
            raise ConfigError("probe_required", 409, purpose=purpose.value, model=model.model_name)
        return ConfigSnapshot(routes=routes, deployments=deployments)

    async def _make_active(
        self,
        session: AsyncSession,
        revision: LlmConfigRevision,
        snapshot: ConfigSnapshot,
        actor: UUID | None,
    ) -> int | None:
        previous = await self._active(session)
        if previous is not None:
            previous.status = LlmRevisionStatus.ARCHIVED
            await session.flush()
        revision.snapshot = snapshot.model_dump(mode="json")
        revision.status = LlmRevisionStatus.ACTIVE
        revision.activated_by = actor
        revision.activated_at = self._now()
        await session.flush()
        return previous.number if previous else None

    @staticmethod
    def _summary(snapshot: ConfigSnapshot) -> dict[str, JsonValue]:
        return {
            p.value: f"{d.display_name} ({d.kind.value}{', cleared' if d.private_data else ''})"
            for p in sorted(snapshot.routes)
            if (d := snapshot.deployment_for(p)) is not None
        }

    async def activate(self, actor: UUID | None, number: int) -> RevisionView:
        async with self._factory() as session, session.begin():
            await self._lock(session)
            revision = await self._revision(session, number)
            if revision.status != LlmRevisionStatus.DRAFT:
                raise ConfigError("not_draft", 409)
            routing = await self._routing(session, revision.id)
            running = await self._active_snapshot(session)
            # Another embedding model is fine since 10.3: the worker builds a new index
            # generation with it and swaps when complete (``vector.reembed``).
            snapshot = await self._snapshot(session, routing, require_probe=True, running=running)
            previous = await self._make_active(session, revision, snapshot, actor)
            await AuditService(session).record(
                EventType.LLM_CONFIG_ACTIVATED,
                actor,
                AuditTarget("llm_config_revision", str(number)),
                {"previous": previous, "routes": self._summary(snapshot)},
            )
        await self._publish(number)
        return RevisionView(revision, routing)

    async def rollback(self, actor: UUID | None, number: int, comment: str | None) -> RevisionView:
        """Re-activate revision ``number``'s snapshot as a new revision (no probe needed: it
        ran before). The current policy still applies: S1-S4 are checked again."""
        async with self._factory() as session, session.begin():
            await self._lock(session)
            target = await self._revision(session, number)
            if target.status != LlmRevisionStatus.ARCHIVED or target.snapshot is None:
                raise ConfigError("not_archived", 409)
            old = ConfigSnapshot.model_validate(target.snapshot)
            routing = await self._routing(session, target.id)
            for purpose, key in old.routes.items():  # revisions seeded before routing rows
                routing.setdefault(purpose, old.deployments[key].model_id)
            snapshot = await self._snapshot(session, routing, require_probe=False)
            revision = LlmConfigRevision(
                status=LlmRevisionStatus.DRAFT,
                comment=comment or f"Przywrócenie rewizji {number}",
                based_on=number,
                rolled_back_from=number,
                created_by=actor,
            )
            session.add(revision)
            await session.flush()
            for purpose, model_id in routing.items():
                session.add(LlmRouting(revision_id=revision.id, purpose=purpose, model_id=model_id))
            await session.flush()
            previous = await self._make_active(session, revision, snapshot, actor)
            await session.refresh(revision)
            await AuditService(session).record(
                EventType.LLM_CONFIG_ROLLED_BACK,
                actor,
                AuditTarget("llm_config_revision", str(revision.number)),
                {"restored": number, "previous": previous, "routes": self._summary(snapshot)},
            )
        await self._publish(revision.number)
        return RevisionView(revision, routing)

    # --- seeding --------------------------------------------------------------------------------

    async def ensure_seeded(self, actor: UUID | None) -> bool:
        """Import ``.env`` as ACTIVE revision 1 if there is no revision yet. True if it did."""
        async with self._factory() as session:
            if await session.scalar(select(func.count()).select_from(LlmConfigRevision)):
                return False
        async with self._factory() as session, session.begin():
            await self._lock(session)
            if await session.scalar(select(func.count()).select_from(LlmConfigRevision)):
                return False
            number = await self._seed(session, actor)
        log.info("llm.config_seeded", revision=number)
        await self._publish(number)
        return True

    async def _seed(self, session: AsyncSession, actor: UUID | None) -> int:
        s = self._settings
        routing: dict[LlmRoutePurpose, UUID] = {}

        async def provider(name: str, url: str) -> LlmProvider | None:
            try:
                base_url = normalize_base_url(url)
                kind = classify(base_url, s)
            except EndpointNotAllowed:
                log.warning("llm.seed_endpoint_skipped", provider=name)
                return None
            row = await session.scalar(select(LlmProvider).where(LlmProvider.name == name))
            if row is None:
                row = LlmProvider(
                    name=name,
                    kind=kind,
                    base_url=base_url,
                    inherit_env_key=True,
                    structured_output=s.structured_output,
                    timeout_seconds=s.timeout_seconds,
                    created_by=actor,
                )
                # The operator's own local server: cleared like migration 0019 clears existing
                # LOCAL providers. A cloud endpoint from ``.env`` is never cleared here.
                if kind == LlmProviderKind.LOCAL and may_receive_private_data(base_url, s):
                    row.private_data_cleared = True
                    row.cleared_region = "serwer spółdzielni"
                    row.cleared_retention = "zgodnie z konfiguracją serwera"
                    row.cleared_contract_ref = "konfiguracja serwera (.env)"
                    row.cleared_at = self._now()
                session.add(row)
                await session.flush()
            return row

        async def model(
            provider: LlmProvider, name: str, capability: LlmCapability, dim: int | None = None
        ) -> UUID:
            row = LlmModel(
                provider_id=provider.id,
                model_name=name,
                capability=capability,
                temperature=s.temperature if capability in _GENERATES else None,
                max_tokens=s.max_tokens if capability in _GENERATES else None,
                embed_dimension=dim,
                notes="Zaimportowano z konfiguracji serwera (.env)",
                created_by=actor,
            )
            session.add(row)
            await session.flush()
            return row.id

        local = await provider("local", s.local_base_url)
        if local is not None and local.kind == LlmProviderKind.LOCAL:
            cleared = self.cleared(local)  # else: RAG only, private purposes stay off (P5)
            if s.chat_model:
                chat = await model(local, s.chat_model, LlmCapability.CHAT)
                for purpose in LlmRoutePurpose:
                    if capability_for(purpose) == LlmCapability.CHAT and (
                        cleared or purpose not in PRIVATE_ROUTE_PURPOSES
                    ):
                        routing[purpose] = chat
            if s.vlm_model and cleared:
                vlm = await model(local, s.vlm_model, LlmCapability.VLM)
                routing[LlmRoutePurpose.VLM] = routing[LlmRoutePurpose.OCR] = vlm
            if s.embed_model and cleared:
                routing[LlmRoutePurpose.EMBED] = await model(
                    local, s.embed_model, LlmCapability.EMBED
                )
            if s.image_embed_model and cleared:
                routing[LlmRoutePurpose.IMAGE_EMBED] = await model(
                    local, s.image_embed_model, LlmCapability.IMAGE_EMBED
                )
            if s.transcribe_model and cleared:
                routing[LlmRoutePurpose.TRANSCRIBE] = await model(
                    local, s.transcribe_model, LlmCapability.TRANSCRIBE
                )
        if s.allow_cloud and s.cloud_model and s.cloud_base_url:
            cloud = await provider("cloud", s.cloud_base_url)
            if cloud is not None and LlmRoutePurpose.RAG not in routing:
                routing[LlmRoutePurpose.RAG] = await model(cloud, s.cloud_model, LlmCapability.CHAT)

        revision = LlmConfigRevision(
            status=LlmRevisionStatus.DRAFT,
            comment="Import konfiguracji serwera (.env)",
            created_by=actor,
        )
        session.add(revision)
        await session.flush()
        for purpose, model_id in routing.items():
            session.add(LlmRouting(revision_id=revision.id, purpose=purpose, model_id=model_id))
        await session.flush()
        snapshot = await self._snapshot(session, routing, require_probe=False)
        await self._make_active(session, revision, snapshot, actor)
        await session.refresh(revision)
        await AuditService(session).record(
            EventType.LLM_CONFIG_ACTIVATED,
            actor,
            AuditTarget("llm_config_revision", str(revision.number)),
            {"previous": None, "seeded": True, "routes": self._summary(snapshot)},
        )
        return revision.number
