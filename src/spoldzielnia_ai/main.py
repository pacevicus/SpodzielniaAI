"""FastAPI application factory."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Literal

from fastapi import FastAPI, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, CollectorRegistry, generate_latest
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel
from redis.asyncio import Redis

from spoldzielnia_ai.api.admin import router as admin_router
from spoldzielnia_ai.api.admin_llm import router as admin_llm_router
from spoldzielnia_ai.api.admin_users import router as admin_users_router
from spoldzielnia_ai.api.atlas import router as atlas_router
from spoldzielnia_ai.api.atlas_contributions import router as atlas_contributions_router
from spoldzielnia_ai.api.atlas_export import router as atlas_export_router
from spoldzielnia_ai.api.atlas_labels import admin_router as atlas_admin_router
from spoldzielnia_ai.api.atlas_labels import curation_router as atlas_curation_router
from spoldzielnia_ai.api.atlas_labels import router as atlas_labels_router
from spoldzielnia_ai.api.atlas_review import router as atlas_review_router
from spoldzielnia_ai.api.attachments import router as attachments_router
from spoldzielnia_ai.api.auth import router as auth_router
from spoldzielnia_ai.api.branding import admin_router as admin_branding_router
from spoldzielnia_ai.api.branding import router as branding_router
from spoldzielnia_ai.api.capabilities import router as capabilities_router
from spoldzielnia_ai.api.contributions import router as contributions_router
from spoldzielnia_ai.api.links import router as links_router
from spoldzielnia_ai.api.me import router as me_router
from spoldzielnia_ai.api.notes import router as notes_router
from spoldzielnia_ai.api.photos import router as photos_router
from spoldzielnia_ai.api.processors import router as processors_router
from spoldzielnia_ai.api.promotion import notes_router as promote_router
from spoldzielnia_ai.api.promotion import router as promotions_router
from spoldzielnia_ai.api.rag import router as rag_router
from spoldzielnia_ai.api.recognition import router as recognition_router
from spoldzielnia_ai.api.reviews import router as reviews_router
from spoldzielnia_ai.api.usage import admin_router as admin_usage_router
from spoldzielnia_ai.api.usage import member_router as usage_member_router
from spoldzielnia_ai.api.wiki import router as wiki_router
from spoldzielnia_ai.atlas.generations import ImageGenerationStore
from spoldzielnia_ai.auth.crypto import CsrfTokens, TokenSealer, derive_key
from spoldzielnia_ai.auth.csrf import csrf_middleware
from spoldzielnia_ai.auth.deps import DbAuditSink
from spoldzielnia_ai.auth.keycloak_admin import KeycloakAdmin
from spoldzielnia_ai.auth.oidc_client import OidcClient
from spoldzielnia_ai.core.crypto import EnvelopeCipher, StaticKeyProvider
from spoldzielnia_ai.core.headers import security_headers_middleware
from spoldzielnia_ai.core.logging import configure_logging, get_logger
from spoldzielnia_ai.core.metrics import OUTBOX_BACKLOG
from spoldzielnia_ai.core.readiness import ReadinessCheck, default_checks
from spoldzielnia_ai.core.settings import Settings, get_settings
from spoldzielnia_ai.db.engine import create_app_engine
from spoldzielnia_ai.db.migrate import head_revision
from spoldzielnia_ai.db.session import create_session_factory
from spoldzielnia_ai.files.antivirus import ClamdScanner
from spoldzielnia_ai.files.storage import S3ObjectStorage
from spoldzielnia_ai.llm.config_store import LlmConfigStore
from spoldzielnia_ai.llm.gateway import LiteLLMGateway
from spoldzielnia_ai.llm.usage import UsageRecorder
from spoldzielnia_ai.notebook.queue import CeleryProcessingQueue
from spoldzielnia_ai.shield.alerts import make_notifier
from spoldzielnia_ai.vector.client import make_qdrant
from spoldzielnia_ai.vector.generations import GenerationStore
from spoldzielnia_ai.vector.sync import outbox_backlog
from spoldzielnia_ai.workers.celery_adapter import make_celery

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

log = get_logger(__name__)


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadinessResponse(BaseModel):
    status: Literal["ok", "unavailable"]
    checks: dict[str, bool]
    schema_revision: str


def create_app(
    settings: Settings | None = None,
    readiness_checks: dict[str, ReadinessCheck] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json=settings.environment != "dev")
    engine = create_app_engine(settings.db)
    # None of the clients connects until first use.
    redis: Redis = Redis.from_url(settings.redis.url())  # pyright: ignore[reportUnknownMemberType]
    oidc_client = OidcClient(settings.oidc)
    keycloak_admin = KeycloakAdmin(settings.keycloak_admin)
    qdrant = make_qdrant(settings.qdrant)
    schema_revision = head_revision()
    checks = (
        readiness_checks
        if readiness_checks is not None
        else default_checks(settings, engine, schema_revision)
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        log.info("app.startup", environment=settings.environment, schema_revision=schema_revision)
        yield
        await oidc_client.aclose()
        await keycloak_admin.aclose()
        await qdrant.close()
        await redis.aclose()
        await engine.dispose()
        log.info("app.shutdown")

    is_prod = settings.environment == "prod"
    app = FastAPI(
        title="Spółdzielnia AI",
        root_path=settings.root_path,
        lifespan=lifespan,
        docs_url=None if is_prod else "/docs",
        redoc_url=None,
        openapi_url=None if is_prod else "/openapi.json",
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.redis = redis
    app.state.oidc_client = oidc_client
    app.state.keycloak_admin = keycloak_admin
    app.state.qdrant = qdrant
    session_secret = settings.security.session_secret.get_secret_value()
    app.state.token_sealer = TokenSealer(derive_key(session_secret, "redis-tokens"))
    csrf_tokens = CsrfTokens(derive_key(session_secret, "csrf"))
    app.state.csrf_tokens = csrf_tokens
    app.state.audit_sink = DbAuditSink(app.state)
    app.state.cipher = EnvelopeCipher(StaticKeyProvider(settings.security.kek.get_secret_value()))
    app.state.storage = S3ObjectStorage(settings.s3)
    app.state.scanner = ClamdScanner(settings.clamav)
    # Producer only: no broker connection until the first task is sent.
    app.state.celery = make_celery(settings.redis.broker_url())
    app.state.processing_queue = CeleryProcessingQueue(
        app.state.session_factory, app.state.cipher, app.state.celery
    )
    app.state.upload_slots = asyncio.Semaphore(settings.uploads.max_concurrent)
    # Question embeddings and answer synthesis (Stage 5), routed by the admin's ACTIVE LLM
    # configuration (Stage 7, reloaded without a restart). Transports are built on first use.
    app.state.llm_config = LlmConfigStore(
        app.state.session_factory, app.state.cipher, settings.llm, redis
    )
    # Every successful model response is counted (15.2): the api's own calls (chat, questions,
    # the admin's playground) and their owner, set per request by ``current_user``.
    app.state.llm = LiteLLMGateway(
        settings.llm,
        source=app.state.llm_config,
        usage=UsageRecorder(app.state.session_factory),
    )
    # Which index generation searches use, with which embedding model (10.3, P8).
    app.state.generations = GenerationStore(
        app.state.session_factory, app.state.cipher, settings.llm
    )
    # The atlas generations (10.6): the api only writes consent changes to every live one, so it
    # never loads an image model (``builtin`` is never asked for).
    app.state.image_generations = ImageGenerationStore(
        app.state.session_factory, app.state.cipher, settings.llm, lambda: None
    )
    app.state.notifier = make_notifier(settings.shield)
    app.include_router(auth_router)
    app.include_router(me_router)
    app.include_router(usage_member_router)  # /me/usage (15.3)
    app.include_router(capabilities_router)
    app.include_router(branding_router)  # public: the look of every page (13.3)
    app.include_router(processors_router)
    app.include_router(atlas_contributions_router)  # /me/contributions/atlas…
    app.include_router(contributions_router)
    app.include_router(promote_router)  # before /notes/{note_id}
    app.include_router(rag_router)  # /notes/ask, before /notes/{note_id}
    app.include_router(notes_router)
    app.include_router(promotions_router)
    app.include_router(attachments_router)
    app.include_router(photos_router)
    app.include_router(recognition_router)
    app.include_router(links_router)
    app.include_router(wiki_router)
    app.include_router(reviews_router)
    app.include_router(admin_router)
    app.include_router(admin_llm_router)
    app.include_router(admin_usage_router)  # /admin/usage (15.4)
    app.include_router(admin_users_router)
    app.include_router(admin_branding_router)
    app.include_router(atlas_labels_router)
    app.include_router(atlas_review_router)  # /atlas/review/… before /atlas/…
    app.include_router(atlas_router)
    app.include_router(atlas_curation_router)
    app.include_router(atlas_admin_router)
    app.include_router(atlas_export_router)

    @app.get("/healthz", response_model=HealthResponse, tags=["ops"])
    async def healthz() -> HealthResponse:  # pyright: ignore[reportUnusedFunction]
        """Liveness: the process is up. Does not touch dependencies."""
        return HealthResponse()

    # HTTP metrics in a registry of this app (tests build many apps in one process); the
    # domain metrics (core.metrics) live in the process-wide default registry.
    http_metrics = CollectorRegistry()
    Instrumentator(
        excluded_handlers=["/metrics", "/healthz", "/readyz"], registry=http_metrics
    ).instrument(app)

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:  # pyright: ignore[reportUnusedFunction]
        """Prometheus scrape target on the internal network (the proxy hides it)."""
        try:
            OUTBOX_BACKLOG.set(await outbox_backlog(app.state.session_factory))
        except Exception as exc:
            log.warning("metrics.backlog_failed", error=type(exc).__name__)
        body = generate_latest(REGISTRY) + generate_latest(http_metrics)
        return Response(body, media_type=CONTENT_TYPE_LATEST)

    @app.get("/readyz", response_model=ReadinessResponse, tags=["ops"])
    async def readyz(response: Response) -> ReadinessResponse:  # pyright: ignore[reportUnusedFunction]
        """Readiness: all backing services required to serve traffic are reachable."""
        results = {name: await check() for name, check in checks.items()}
        ok = all(results.values())
        if not ok:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(
            status="ok" if ok else "unavailable", checks=results, schema_revision=schema_revision
        )

    # Registered before `security_headers`, so its 403s still get the security headers.
    app.middleware("http")(csrf_middleware(settings.security.allowed_origins, csrf_tokens))

    app.middleware("http")(security_headers_middleware(app.docs_url))

    return app
