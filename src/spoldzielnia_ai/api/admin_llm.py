"""``/admin/llm``: LLM providers, models, probes and configuration revisions (7.4). ADMIN + MFA
on every route (``elevated_router``).

Every request first imports ``.env`` as revision 1 if nothing is stored yet, so the console
always shows what the system actually runs. Rules (private purposes need cleared providers,
the cloud ceiling, endpoint allowlists, probe freshness, no embedding model change) live in
``llm.config_service``; this module maps their ``ConfigError`` codes to HTTP answers. API keys
are accepted, never returned (``key_set`` / ``key_hint`` only). Connection tests and probes are
rate limited per admin (twice ``llm.playground_per_hour``). Clearing a provider for private
data (10.1, P3) also needs a recent login (step-up), like revoking consent.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - pydantic resolves annotations at runtime
from decimal import Decimal  # noqa: TC003 - pydantic resolves annotations at runtime
from typing import TYPE_CHECKING, Annotated, Any
from uuid import UUID  # noqa: TC003 - pydantic resolves annotations at runtime

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from spoldzielnia_ai.api.contributions import recently_authenticated
from spoldzielnia_ai.atlas.generations import count_photos, image_generations_overview
from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.audit.service import AuditService
from spoldzielnia_ai.auth.deps import CurrentUser, elevated_router
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.enums import (
    IndexGenerationStatus,
    LlmCapability,
    LlmProviderKind,
    LlmRevisionStatus,
    LlmRoutePurpose,
    Role,
)
from spoldzielnia_ai.llm.capabilities import builtin_purposes, missing_purposes
from spoldzielnia_ai.llm.config_service import (
    PRIVATE_ROUTE_PURPOSES,
    ClearanceIn,
    ConfigError,
    LlmConfigService,
    ModelIn,
    ModelPatch,
    ProviderIn,
    ProviderPatch,
    Removal,
    RevisionView,
    capability_for,
)
from spoldzielnia_ai.llm.endpoints import (
    allowed_endpoints,
    may_receive_private_data,
    private_data_endpoints,
)
from spoldzielnia_ai.llm.playground import MAX_PROMPT, PRESETS, compare, prompt_digest
from spoldzielnia_ai.llm.probes import deployment_for, list_models, probe_model, single_gateway
from spoldzielnia_ai.llm.snapshot import ConfigSnapshot
from spoldzielnia_ai.llm.usage import UsageRecorder, micro_to_price
from spoldzielnia_ai.vector.reembed import (
    GenerationError,
    GenerationManager,
    count_items,
    generations_overview,
)

if TYPE_CHECKING:
    from pydantic import JsonValue
    from redis.asyncio import Redis

    from spoldzielnia_ai.core.settings import Settings
    from spoldzielnia_ai.db.models import IndexGeneration, LlmModel, LlmProvider
    from spoldzielnia_ai.llm.gateway import ChatTransport, Deployment, LiteLLMGateway
    from spoldzielnia_ai.notebook.uploads import ProcessingQueue

log = get_logger(__name__)

router = elevated_router(Role.ADMIN, prefix="/admin/llm", tags=["admin-llm"])


# --- Dependencies & errors ----------------------------------------------------------------------


async def get_service(request: Request, principal: CurrentUser) -> LlmConfigService:
    state = request.app.state
    settings: Settings = state.settings
    service = LlmConfigService(
        state.session_factory,
        state.cipher,
        settings.llm,
        redis=state.redis,
    )
    try:
        await service.ensure_seeded(principal.id)
    except ConfigError as exc:  # pragma: no cover - seeding only skips what is not allowed
        raise _http(exc) from None
    return service


Service = Annotated[LlmConfigService, Depends(get_service)]


def _http(exc: ConfigError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"error": exc.code, **exc.detail})


async def _rate_limit(request: Request, principal_id: UUID, what: str, per_hour: int) -> None:
    """A fixed one-hour window per admin in Redis. Fails open if Redis is down (admins only)."""
    redis: Redis = request.app.state.redis
    key = f"llm:limit:{what}:{principal_id}"
    try:
        count = int(await redis.incr(key))
        if count == 1:
            await redis.expire(key, 3600)
    except Exception as exc:
        log.warning("llm.rate_limit_unavailable", error=type(exc).__name__)
        return
    if count > per_hour:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail={"error": "rate_limited"}
        )


async def _config_changed(request: Request) -> None:
    """Resume work parked for want of a model (10.2); the beat tick is the fallback."""
    queue: ProcessingQueue = request.app.state.processing_queue
    try:
        await queue.model_config_changed()
    except Exception as exc:  # the broker being down must not fail the admin's change
        log.warning("llm.resume_signal_failed", error=type(exc).__name__)


def _transport(request: Request) -> ChatTransport | None:
    """Tests replace the transport of probes and the playground; production builds LiteLLM's."""
    return getattr(request.app.state, "llm_probe_transport", None)


# --- Schemas ------------------------------------------------------------------------------------


class ProviderOut(BaseModel):
    id: UUID
    name: str
    kind: LlmProviderKind
    allowed: bool  # still on the allowlists (they live in the server configuration)
    base_url: str
    litellm_provider: str
    key_set: bool
    key_hint: str | None
    inherit_env_key: bool
    structured_output: str | None
    timeout_seconds: float | None
    disabled: bool
    in_active: bool
    # 10.1: may receive private data (cleared by an admin and within the operator's list).
    private_data_cleared: bool
    clearable: bool  # the endpoint is on ``llm.private_data_endpoints``
    cleared_region: str | None
    cleared_retention: str | None
    cleared_contract_ref: str | None
    cleared_at: datetime | None
    # 11.1: why "Usuń" is unavailable now (the error code a DELETE would answer with), and
    # whether removing archives it (a past revision used it) instead of deleting it.
    removal_blocked: str | None = None
    removal_archives: bool = False
    removal_draft: int | None = None
    created_at: datetime
    updated_at: datetime


class ModelOut(BaseModel):
    id: UUID
    provider_id: UUID
    model_name: str
    capability: LlmCapability
    temperature: float | None
    max_tokens: int | None
    embed_dimension: int | None
    notes: str | None
    disabled: bool
    in_active: bool
    last_probe_at: datetime | None
    last_probe_ok: bool | None
    last_probe_report: dict[str, Any] | None
    # Per million tokens, in the usage currency (15.3); both null when unpriced.
    price_in_per_million: Decimal | None = None
    price_out_per_million: Decimal | None = None


class RouteOut(BaseModel):
    model_id: UUID
    name: str
    kind: LlmProviderKind
    private_data: bool


class RevisionOut(BaseModel):
    number: int
    status: LlmRevisionStatus
    comment: str | None
    based_on: int | None
    rolled_back_from: int | None
    created_by: UUID | None
    created_at: datetime
    activated_by: UUID | None
    activated_at: datetime | None
    routing: dict[LlmRoutePurpose, UUID]
    # Frozen routes of ACTIVE/ARCHIVED revisions (what actually ran).
    routes: dict[LlmRoutePurpose, RouteOut] | None


class PurposeOut(BaseModel):
    purpose: LlmRoutePurpose
    capability: LlmCapability
    private_data: bool  # needs a provider cleared for private data


class StatusOut(BaseModel):
    config_source: str
    allow_cloud: bool
    local_endpoints: list[str]
    cloud_hosts: list[str]
    private_data_endpoints: list[str]
    egress_proxy: bool
    probe_max_age_hours: int
    active_revision: int | None
    process_revision: int  # what this API process routes by right now (0 = .env)
    purposes: list[PurposeOut]
    # 10.2: purposes without a usable model in the running configuration (features off).
    missing_purposes: list[LlmRoutePurpose]


class DraftIn(BaseModel):
    based_on: int | None = Field(default=None, ge=1)
    comment: str | None = Field(default=None, max_length=500)


class RoutingIn(BaseModel):
    routes: dict[LlmRoutePurpose, UUID | None]
    comment: str | None = Field(default=None, max_length=500)


class RollbackIn(BaseModel):
    comment: str | None = Field(default=None, max_length=500)


class DisableIn(BaseModel):
    disabled: bool


class ConnectionOut(BaseModel):
    ok: bool
    status: int | None = None
    latency_ms: int | None = None
    error: str | None = None
    models: list[str] = Field(default_factory=list[str])


class ProbeOut(BaseModel):
    model: ModelOut
    report: dict[str, Any]


def _allowed(provider: LlmProvider, service: LlmConfigService) -> bool:
    try:
        service.kind_of(provider.base_url)
    except ConfigError:
        return False
    return True


def _provider_out(
    provider: LlmProvider,
    service: LlmConfigService,
    active: set[UUID],
    removal: Removal | None = None,
) -> ProviderOut:
    removal = removal or Removal()
    return ProviderOut(
        id=provider.id,
        name=provider.name,
        kind=provider.kind,
        allowed=_allowed(provider, service),
        base_url=provider.base_url,
        litellm_provider=provider.litellm_provider,
        key_set=provider.api_key_ciphertext is not None or provider.inherit_env_key,
        key_hint=provider.api_key_hint,
        inherit_env_key=provider.inherit_env_key,
        structured_output=provider.structured_output,
        timeout_seconds=provider.timeout_seconds,
        disabled=provider.disabled_at is not None,
        in_active=provider.id in active,
        private_data_cleared=service.cleared(provider),
        clearable=may_receive_private_data(provider.base_url, service.settings),
        cleared_region=provider.cleared_region if provider.private_data_cleared else None,
        cleared_retention=provider.cleared_retention if provider.private_data_cleared else None,
        cleared_contract_ref=provider.cleared_contract_ref
        if provider.private_data_cleared
        else None,
        cleared_at=provider.cleared_at,
        removal_blocked=removal.blocked,
        removal_archives=removal.archive,
        removal_draft=removal.draft,
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


def _model_out(model: LlmModel, active: set[UUID]) -> ModelOut:
    return ModelOut(
        id=model.id,
        provider_id=model.provider_id,
        model_name=model.model_name,
        capability=model.capability,
        temperature=model.temperature,
        max_tokens=model.max_tokens,
        embed_dimension=model.embed_dimension,
        notes=model.notes,
        disabled=model.disabled_at is not None,
        in_active=model.id in active,
        last_probe_at=model.last_probe_at,
        last_probe_ok=model.last_probe_ok,
        last_probe_report=dict(model.last_probe_report) if model.last_probe_report else None,
        price_in_per_million=None
        if model.price_in_micro is None
        else micro_to_price(model.price_in_micro),
        price_out_per_million=None
        if model.price_out_micro is None
        else micro_to_price(model.price_out_micro),
    )


def _revision_out(view: RevisionView) -> RevisionOut:
    revision = view.revision
    routes: dict[LlmRoutePurpose, RouteOut] | None = None
    if revision.snapshot is not None:
        snapshot = ConfigSnapshot.model_validate(revision.snapshot)
        routes = {
            purpose: RouteOut(
                model_id=d.model_id, name=d.display_name, kind=d.kind, private_data=d.private_data
            )
            for purpose in snapshot.routes
            if (d := snapshot.deployment_for(purpose)) is not None
        }
    return RevisionOut(
        number=revision.number,
        status=revision.status,
        comment=revision.comment,
        based_on=revision.based_on,
        rolled_back_from=revision.rolled_back_from,
        created_by=revision.created_by,
        created_at=revision.created_at,
        activated_by=revision.activated_by,
        activated_at=revision.activated_at,
        routing=view.routing,
        routes=routes,
    )


async def _active_providers(service: LlmConfigService) -> tuple[set[UUID], set[UUID]]:
    active = await service.active()
    if active is None or active.revision.snapshot is None:
        return set(), set()
    snapshot = ConfigSnapshot.model_validate(active.revision.snapshot)
    deployments = snapshot.deployments.values()
    return {d.provider_id for d in deployments}, {d.model_id for d in deployments}


# --- Status -------------------------------------------------------------------------------------


@router.get("/status", response_model=StatusOut)
async def get_status(request: Request, service: Service) -> StatusOut:
    settings: Settings = request.app.state.settings
    llm = settings.llm
    local, cloud = allowed_endpoints(llm)
    active = await service.active()
    store = getattr(request.app.state, "llm_config", None)
    missing = await missing_purposes(request.app.state.llm, builtin_purposes(llm))
    return StatusOut(
        config_source=llm.config_source,
        allow_cloud=llm.allow_cloud,
        local_endpoints=local,
        cloud_hosts=cloud,
        private_data_endpoints=private_data_endpoints(llm),
        egress_proxy=llm.egress_proxy_url is not None,
        probe_max_age_hours=llm.probe_max_age_hours,
        active_revision=active.revision.number if active else None,
        process_revision=(await store.current()).revision if store is not None else 0,
        missing_purposes=[LlmRoutePurpose(p.value) for p in missing],
        purposes=[
            PurposeOut(
                purpose=p, capability=capability_for(p), private_data=p in PRIVATE_ROUTE_PURPOSES
            )
            for p in LlmRoutePurpose
        ],
    )


# --- Providers ----------------------------------------------------------------------------------


@router.get("/providers", response_model=list[ProviderOut])
async def get_providers(service: Service) -> list[ProviderOut]:
    active, _ = await _active_providers(service)
    removals = await service.removals()
    providers = await service.providers()
    return [_provider_out(p, service, active, removals.get(p.id)) for p in providers]


@router.post("/providers", response_model=ProviderOut, status_code=status.HTTP_201_CREATED)
async def create_provider(
    payload: ProviderIn, service: Service, principal: CurrentUser
) -> ProviderOut:
    try:
        provider = await service.create_provider(principal.id, payload)
    except ConfigError as exc:
        raise _http(exc) from None
    return _provider_out(provider, service, set())


@router.patch("/providers/{provider_id}", response_model=ProviderOut)
async def update_provider(
    provider_id: UUID, payload: ProviderPatch, service: Service, principal: CurrentUser
) -> ProviderOut:
    try:
        provider = await service.update_provider(principal.id, provider_id, payload)
    except ConfigError as exc:
        raise _http(exc) from None
    active, _ = await _active_providers(service)
    return _provider_out(provider, service, active)


@router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(provider_id: UUID, service: Service, principal: CurrentUser) -> Response:
    """Remove a provider nothing running needs (11.1): deleted, or archived if a past revision
    used it. 409 ``provider_in_use`` / ``provider_in_index`` / ``provider_in_draft`` otherwise."""
    try:
        await service.delete_provider(principal.id, provider_id)
    except ConfigError as exc:
        raise _http(exc) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/providers/{provider_id}/disabled", response_model=ProviderOut)
async def disable_provider(
    provider_id: UUID, payload: DisableIn, service: Service, principal: CurrentUser
) -> ProviderOut:
    try:
        provider = await service.set_provider_disabled(principal.id, provider_id, payload.disabled)
    except ConfigError as exc:
        raise _http(exc) from None
    active, _ = await _active_providers(service)
    return _provider_out(provider, service, active)


@router.post("/providers/{provider_id}/clearance", response_model=ProviderOut)
async def clear_provider(
    provider_id: UUID,
    payload: ClearanceIn,
    request: Request,
    service: Service,
    principal: CurrentUser,
) -> ProviderOut | JSONResponse:
    """Allow private purposes to use this provider (P2/P3): needs a recent login (step-up)."""
    settings: Settings = request.app.state.settings
    if not recently_authenticated(principal, settings):
        step_up = f"{settings.root_path}/auth/login?prompt=login"
        return JSONResponse(
            {"detail": {"error": "reauthentication_required", "step_up": step_up}},
            status_code=status.HTTP_403_FORBIDDEN,
        )
    try:
        provider = await service.clear_provider(principal.id, provider_id, payload)
    except ConfigError as exc:
        raise _http(exc) from None
    await _config_changed(request)
    active, _ = await _active_providers(service)
    return _provider_out(provider, service, active)


@router.delete("/providers/{provider_id}/clearance", response_model=ProviderOut)
async def unclear_provider(
    provider_id: UUID, service: Service, principal: CurrentUser
) -> ProviderOut:
    """Stop private data going to this provider, at once. Private purposes it served stop too
    until another cleared model takes them over (no fallback)."""
    try:
        provider = await service.unclear_provider(principal.id, provider_id)
    except ConfigError as exc:
        raise _http(exc) from None
    active, _ = await _active_providers(service)
    return _provider_out(provider, service, active)


@router.post("/providers/{provider_id}/test", response_model=ConnectionOut)
async def test_provider(
    provider_id: UUID, request: Request, service: Service, principal: CurrentUser
) -> ConnectionOut:
    """Reachability, authentication and the models on offer (``GET {base_url}/models``)."""
    settings: Settings = request.app.state.settings
    await _rate_limit(request, principal.id, "probe", settings.llm.playground_per_hour * 2)
    try:
        provider = await service.provider(provider_id)
        kind = service.kind_of(provider.base_url)
    except ConfigError as exc:
        raise _http(exc) from None
    if kind == LlmProviderKind.CLOUD and not settings.llm.allow_cloud:
        raise _http(ConfigError("cloud_not_allowed"))
    client = getattr(request.app.state, "llm_probe_http", None)
    result = await list_models(
        provider.base_url, service.api_key(provider), kind, settings.llm, client
    )
    return ConnectionOut.model_validate(result)


# --- Models -------------------------------------------------------------------------------------


@router.get("/models", response_model=list[ModelOut])
async def get_models(service: Service) -> list[ModelOut]:
    _, active = await _active_providers(service)
    return [_model_out(m, active) for m in await service.models()]


@router.post("/models", response_model=ModelOut, status_code=status.HTTP_201_CREATED)
async def create_model(payload: ModelIn, service: Service, principal: CurrentUser) -> ModelOut:
    try:
        model = await service.create_model(principal.id, payload)
    except ConfigError as exc:
        raise _http(exc) from None
    return _model_out(model, set())


@router.patch("/models/{model_id}", response_model=ModelOut)
async def update_model(
    model_id: UUID, payload: ModelPatch, service: Service, principal: CurrentUser
) -> ModelOut:
    try:
        model = await service.update_model(principal.id, model_id, payload)
    except ConfigError as exc:
        raise _http(exc) from None
    _, active = await _active_providers(service)
    return _model_out(model, active)


@router.post("/models/{model_id}/disabled", response_model=ModelOut)
async def disable_model(
    model_id: UUID, payload: DisableIn, service: Service, principal: CurrentUser
) -> ModelOut:
    try:
        model = await service.set_model_disabled(principal.id, model_id, payload.disabled)
    except ConfigError as exc:
        raise _http(exc) from None
    _, active = await _active_providers(service)
    return _model_out(model, active)


@router.post("/models/{model_id}/probe", response_model=ProbeOut)
async def probe(
    model_id: UUID, request: Request, service: Service, principal: CurrentUser
) -> ProbeOut:
    """Synthetic checks against the model (``llm.probes``); the report is stored on the model
    and gates activation."""
    settings: Settings = request.app.state.settings
    await _rate_limit(request, principal.id, "probe", settings.llm.playground_per_hour * 2)
    try:
        model, provider = await service.model(model_id)
        kind = service.kind_of(provider.base_url)
    except ConfigError as exc:
        raise _http(exc) from None
    if kind == LlmProviderKind.CLOUD and not settings.llm.allow_cloud:
        raise _http(ConfigError("cloud_not_allowed"))
    deployment = deployment_for(
        f"{provider.name}/{model.model_name}",
        model.model_name,
        provider.base_url,
        service.api_key(provider),
        kind,
        temperature=model.temperature,
        max_tokens=model.max_tokens,
        structured_output=provider.structured_output,
        timeout_seconds=provider.timeout_seconds,
    )
    report: dict[str, JsonValue] = await probe_model(
        model.capability,
        deployment,
        settings.llm,
        transport=_transport(request),
        usage=UsageRecorder(request.app.state.session_factory),  # probes cost compute too (15.2)
    )
    updated = await service.record_probe(principal.id, model.id, bool(report.get("ok")), report)
    _, active = await _active_providers(service)
    return ProbeOut(model=_model_out(updated, active), report=dict(report))


# --- Revisions ----------------------------------------------------------------------------------


@router.get("/revisions", response_model=list[RevisionOut])
async def get_revisions(service: Service) -> list[RevisionOut]:
    out: list[RevisionOut] = []
    for revision in await service.revisions():
        out.append(_revision_out(await service.revision(revision.number)))
    return out


@router.get("/revisions/{number}", response_model=RevisionOut)
async def get_revision(number: int, service: Service) -> RevisionOut:
    try:
        return _revision_out(await service.revision(number))
    except ConfigError as exc:
        raise _http(exc) from None


@router.post("/revisions", response_model=RevisionOut, status_code=status.HTTP_201_CREATED)
async def create_draft(payload: DraftIn, service: Service, principal: CurrentUser) -> RevisionOut:
    try:
        view = await service.create_draft(principal.id, payload.based_on, payload.comment)
    except ConfigError as exc:
        raise _http(exc) from None
    return _revision_out(view)


@router.put("/revisions/{number}/routing", response_model=RevisionOut)
async def set_routing(
    number: int, payload: RoutingIn, service: Service, principal: CurrentUser
) -> RevisionOut:
    try:
        view = await service.set_routing(principal.id, number, payload.routes, payload.comment)
    except ConfigError as exc:
        raise _http(exc) from None
    return _revision_out(view)


@router.delete("/revisions/{number}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_draft(number: int, service: Service, principal: CurrentUser) -> Response:
    try:
        await service.delete_draft(principal.id, number)
    except ConfigError as exc:
        raise _http(exc) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/revisions/{number}/activate", response_model=RevisionOut)
async def activate(
    number: int, request: Request, service: Service, principal: CurrentUser
) -> RevisionOut:
    try:
        view = await service.activate(principal.id, number)
    except ConfigError as exc:
        raise _http(exc) from None
    await _config_changed(request)
    return _revision_out(view)


@router.post("/revisions/{number}/rollback", response_model=RevisionOut)
async def rollback(
    number: int, payload: RollbackIn, request: Request, service: Service, principal: CurrentUser
) -> RevisionOut:
    try:
        view = await service.rollback(principal.id, number, payload.comment)
    except ConfigError as exc:
        raise _http(exc) from None
    await _config_changed(request)
    return _revision_out(view)


# --- Playground (7.5) ---------------------------------------------------------------------------


class PresetOut(BaseModel):
    key: str
    title: str
    prompt: str
    private_data: bool
    structured: bool


class PlaygroundIn(BaseModel):
    model_ids: list[UUID] = Field(min_length=1, max_length=3)
    prompt: str | None = Field(default=None, min_length=1, max_length=MAX_PROMPT)
    preset: str | None = None


class PlaygroundResultOut(BaseModel):
    model_id: UUID
    name: str
    local: bool
    ok: bool
    content: str | None = None
    truncated: bool = False
    latency_ms: int | None = None
    tokens: int | None = None
    attempts: int | None = None
    json_valid: bool | None = None
    error: str | None = None


class PlaygroundOut(BaseModel):
    results: list[PlaygroundResultOut]


@router.get("/playground/presets", response_model=list[PresetOut])
async def get_presets() -> list[PresetOut]:
    return [
        PresetOut(
            key=p.key,
            title=p.title,
            prompt=p.prompt,
            private_data=p.private_data,
            structured=p.response_model is not None,
        )
        for p in PRESETS.values()
    ]


@router.post("/playground", response_model=PlaygroundOut)
async def playground(
    payload: PlaygroundIn, request: Request, service: Service, principal: CurrentUser
) -> PlaygroundOut:
    """Up to three CHAT/VLM models answer the same prompt (the admin's own text or a preset).
    No retrieval, nothing stored but an audit entry with the prompt's hash."""
    state = request.app.state
    settings: Settings = state.settings
    if (payload.prompt is None) == (payload.preset is None):
        raise _http(ConfigError("prompt_or_preset"))
    preset = PRESETS.get(payload.preset) if payload.preset is not None else None
    if payload.preset is not None and preset is None:
        raise _http(ConfigError("unknown_preset"))
    prompt = preset.prompt if preset is not None else str(payload.prompt)
    if len(set(payload.model_ids)) != len(payload.model_ids):
        raise _http(ConfigError("duplicate_models"))
    await _rate_limit(request, principal.id, "playground", settings.llm.playground_per_hour)
    runs: list[tuple[UUID, LiteLLMGateway, Deployment]] = []
    for model_id in payload.model_ids:
        try:
            model, provider = await service.model(model_id)
            kind = service.kind_of(provider.base_url)
        except ConfigError as exc:
            raise _http(exc) from None
        if model.capability in (
            LlmCapability.EMBED,
            LlmCapability.TRANSCRIBE,
            LlmCapability.IMAGE_EMBED,
        ):
            raise _http(ConfigError("capability_mismatch", model=model.model_name))
        if model.disabled_at is not None or provider.disabled_at is not None:
            raise _http(ConfigError("model_disabled", model=model.model_name))
        if kind == LlmProviderKind.CLOUD and not settings.llm.allow_cloud:
            raise _http(ConfigError("cloud_not_allowed", model=model.model_name))
        cleared = service.cleared(provider)
        if preset is not None and preset.private_data and not cleared:
            raise _http(ConfigError("provider_not_cleared", model=model.model_name))
        deployment = deployment_for(
            f"{provider.name}/{model.model_name}",
            model.model_name,
            provider.base_url,
            service.api_key(provider),
            kind,
            private_data=cleared,
            temperature=model.temperature,
            max_tokens=model.max_tokens,
            structured_output=provider.structured_output,
            timeout_seconds=provider.timeout_seconds,
        )
        runs.append(
            (
                model.id,
                single_gateway(
                    deployment,
                    settings.llm,
                    _transport(request),
                    usage=UsageRecorder(state.session_factory),
                ),
                deployment,
            )
        )
    results = await compare([(g, d) for _, g, d in runs], prompt, preset)
    async with state.session_factory() as session, session.begin():
        await AuditService(session).record(
            EventType.LLM_PLAYGROUND_USED,
            principal.id,
            None,
            {
                "preset": preset.key if preset else None,
                "prompt_sha256": prompt_digest(prompt),
                "prompt_chars": len(prompt),
                "models": [d.name for _, _, d in runs],
                "ok": [bool(r.get("ok")) for r in results],
            },
        )
    return PlaygroundOut(
        results=[
            PlaygroundResultOut.model_validate({"model_id": model_id, **result})
            for (model_id, _, _), result in zip(runs, results, strict=True)
        ]
    )


# --- Index generations (10.3) -------------------------------------------------------------------


class GenerationOut(BaseModel):
    number: int
    status: IndexGenerationStatus
    model: str
    dimension: int
    total: int
    done: int
    failed: int
    created_at: datetime
    serving_since: datetime | None
    retired_at: datetime | None
    drop_after: datetime | None
    dropped: bool


class IndexOut(BaseModel):
    generations: list[GenerationOut]
    # What switching the embedding model re-embeds (cost guard): articles + derivatives.
    items: int
    reembed_per_minute: int
    # The visual atlas (10.6): its generations, and the approved photos a switch re-embeds.
    atlas_generations: list[GenerationOut]
    atlas_items: int


def _manager(request: Request) -> GenerationManager:
    state = request.app.state
    settings: Settings = state.settings
    return GenerationManager(
        state.session_factory, state.cipher, state.qdrant, state.llm, settings.llm, settings.qdrant
    )


def _generation_out(r: IndexGeneration) -> GenerationOut:
    return GenerationOut(
        number=r.number,
        status=r.status,
        model=r.model,
        dimension=r.dimension,
        total=r.total,
        done=r.done,
        failed=r.failed,
        created_at=r.created_at,
        serving_since=r.serving_since,
        retired_at=r.retired_at,
        drop_after=r.drop_after,
        dropped=r.dropped_at is not None,
    )


async def _index_out(request: Request) -> IndexOut:
    state = request.app.state
    settings: Settings = state.settings
    factory = state.session_factory
    async with factory() as session:
        photos = await count_photos(session)
    return IndexOut(
        generations=[_generation_out(r) for r in await generations_overview(factory)],
        items=await count_items(factory),
        reembed_per_minute=settings.llm.reembed_per_minute,
        atlas_generations=[_generation_out(r) for r in await image_generations_overview(factory)],
        atlas_items=photos,
    )


@router.get("/index", response_model=IndexOut)
async def get_index(request: Request, _service: Service) -> IndexOut:
    return await _index_out(request)


@router.post("/index/rebuild", response_model=IndexOut, status_code=status.HTTP_202_ACCEPTED)
async def rebuild_index(request: Request, _service: Service, principal: CurrentUser) -> IndexOut:
    """Re-embed everything with the active model into a new generation (``reindex``)."""
    try:
        await _manager(request).start(principal.id)
    except GenerationError as exc:
        raise _http(ConfigError(exc.code, 409)) from None
    await _config_changed(request)  # the worker's reconcile sends the build task
    return await _index_out(request)


@router.post("/index/{number}/cancel", response_model=IndexOut)
async def cancel_index(
    number: int, request: Request, _service: Service, principal: CurrentUser
) -> IndexOut:
    """Abandon a build before the swap: the serving generation stays as it is."""
    try:
        await _manager(request).cancel(number, principal.id)
    except GenerationError as exc:
        status_code = 404 if exc.code == "generation_not_found" else 409
        raise _http(ConfigError(exc.code, status_code)) from None
    return await _index_out(request)
