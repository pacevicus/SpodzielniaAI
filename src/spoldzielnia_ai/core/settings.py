"""Application settings.

Non-secret configuration comes from environment variables (prefix ``SPOLDZIELNIA_``, nested
groups separated by ``__``, e.g. ``SPOLDZIELNIA_DB__HOST``). Secrets come from Docker secrets
mounted under ``/run/secrets``. Each file name is the nested field path, e.g.
``/run/secrets/db__app_password``. Defaults for hosts are compose service names and never
``localhost``.

Each process gets only the secrets it needs (least privilege): the API loads ``Settings``,
the one-shot ``migrate`` job loads ``MigrateSettings`` (owner DSN only), ``audit-verify`` loads
``AuditVerifySettings`` (app DSN only) and ``bootstrap`` loads ``BootstrapSettings`` (object
storage and Qdrant only).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal, Self
from urllib.parse import quote

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    NestedSecretsSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from sqlalchemy.engine import URL

DEFAULT_SECRETS_DIR = "/run/secrets"


class DbSettings(BaseModel):
    host: str = "postgres"
    port: int = 5432
    name: str = "spoldzielnia"
    app_user: str = "spoldzielnia_app"
    app_password: SecretStr | None = None
    owner_user: str = "spoldzielnia_owner"
    owner_password: SecretStr | None = None
    pool_size: int = Field(default=10, ge=1)

    def _url(self, user: str, password: SecretStr) -> str:
        return URL.create(
            "postgresql+asyncpg",
            username=user,
            password=password.get_secret_value(),
            host=self.host,
            port=self.port,
            database=self.name,
        ).render_as_string(hide_password=False)

    def app_url(self) -> str:
        """DSN for the runtime role (``NOBYPASSRLS``, not the table owner)."""
        if self.app_password is None:
            raise ValueError("db.app_password is required for the runtime DSN")
        return self._url(self.app_user, self.app_password)

    def migration_url(self) -> str:
        """DSN for the schema owner role, used only by Alembic."""
        if self.owner_password is None:
            raise ValueError("db.owner_password is required for migrations")
        return self._url(self.owner_user, self.owner_password)


class RedisSettings(BaseModel):
    host: str = "redis"
    port: int = 6379
    db: int = 0
    password: SecretStr

    # Celery broker (2.5), kept apart from sessions/login state in `db`.
    broker_db: int = 1

    def url(self, db: int | None = None) -> str:
        password = quote(self.password.get_secret_value(), safe="")
        return f"redis://:{password}@{self.host}:{self.port}/{self.db if db is None else db}"

    def broker_url(self) -> str:
        return self.url(self.broker_db)


# Asymmetric signatures only: `none` and HMAC (`HS*`, keyed with the shared client secret) are
# never acceptable for ID tokens.
OIDC_SIGNING_ALGS = frozenset({"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256"})


class OidcSettings(BaseModel):
    """The single trusted issuer (Keycloak). External IdPs are brokered inside it."""

    provider: str = "keycloak"
    issuer: str = "https://auth.spoldzielnia.localhost/realms/spoldzielnia"
    client_id: str = "spoldzielnia-backend"
    client_secret: SecretStr
    redirect_uri: str = "https://spoldzielnia.localhost/api/auth/callback"
    post_logout_redirect_uri: str = "https://spoldzielnia.localhost/"
    scopes: list[str] = Field(default_factory=lambda: ["openid", "profile", "email"])
    # Dotted path to the roles list in the ID token, e.g. "roles" or "realm_access.roles".
    roles_claim: str = "roles"
    # Dev: Caddy's local root CA. Prod: public certificates, no custom CA.
    ca_bundle: str | None = None
    signing_algs: list[str] = Field(default_factory=lambda: ["RS256", "ES256"])
    leeway_seconds: int = Field(default=60, ge=0, le=300)
    login_state_ttl_seconds: int = Field(default=600, ge=60, le=600)
    http_timeout_seconds: float = Field(default=10.0, gt=0)
    metadata_ttl_seconds: int = Field(default=3600, ge=60)
    # A `kid` miss refetches the JWKS at most this often (unknown-kid tokens can't force a flood).
    jwks_min_refresh_seconds: int = Field(default=30, ge=1)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not self.signing_algs or not set(self.signing_algs) <= OIDC_SIGNING_ALGS:
            raise ValueError(f"oidc.signing_algs must be a subset of {sorted(OIDC_SIGNING_ALGS)}")
        if "openid" not in self.scopes:
            raise ValueError("oidc.scopes must include 'openid'")
        return self


class QdrantSettings(BaseModel):
    url: str = "https://qdrant:6333"
    api_key: SecretStr | None = None
    # Internal CA (compose secret `internal_ca`): Qdrant serves TLS with a dev/internal cert.
    ca_bundle: str | None = None
    # The text index's dimension is no longer configured: each index generation records the
    # dimension its embedding model produced when probed (10.3). A replaced generation keeps
    # receiving consent changes, and is dropped this long after the swap.
    retired_grace_hours: int = Field(default=24, ge=0, le=24 * 30)
    # Bounded wait for synchronous index operations (consent propagation, 4.3).
    timeout_seconds: int = Field(default=10, ge=1, le=120)


class S3Settings(BaseModel):
    endpoint_url: str = "http://minio:9000"
    access_key: str = "spoldzielnia"
    secret_key: SecretStr | None = None
    bucket: str = "spoldzielnia-private"
    region: str = "us-east-1"


class ClamavSettings(BaseModel):
    host: str = "clamav"
    port: int = 3310
    connect_timeout_seconds: float = Field(default=5.0, gt=0)
    scan_timeout_seconds: float = Field(default=180.0, gt=0)
    # Must not exceed clamd's StreamMaxLength (compose: CLAMD_CONF_StreamMaxLength=210M).
    stream_max_bytes: int = Field(default=210 * 1024 * 1024, gt=0)


class UploadSettings(BaseModel):
    # Spool directory (tmpfs in the container). None: the system temp dir.
    tmp_dir: str | None = None
    # Each upload needs up to ~2x its size in the spool: bound the parallelism.
    max_concurrent: int = Field(default=2, ge=1)
    # Hard cap on the streamed file part, before type-specific limits apply.
    max_bytes: int = Field(default=200 * 1024 * 1024, gt=0)
    max_image_pixels: int = Field(default=60_000_000, gt=0)


class FetchSettings(BaseModel):
    """SSRF-safe link fetching (egress worker)."""

    allow_http: bool = False
    allowed_ports: list[int] = Field(default_factory=lambda: [80, 443, 8080, 8443])
    connect_timeout_seconds: float = Field(default=5.0, gt=0)
    total_timeout_seconds: float = Field(default=15.0, gt=0)
    max_redirects: int = Field(default=3, ge=0, le=10)
    max_body_bytes: int = Field(default=5 * 1024 * 1024, gt=0)
    max_url_length: int = 2048
    allowed_content_types: list[str] = Field(
        default_factory=lambda: [
            "text/html",
            "application/xhtml+xml",
            "text/plain",
            "text/markdown",
            "application/pdf",
        ]
    )
    user_agent: str = "SpoldzielniaAI-LinkFetcher/1.0 (+https://spoldzielnia.localhost)"


class ModelSettings(BaseModel):
    """Local ML models in the read-only `models` volume (filled by `make models`)."""

    root: str = "/models"
    whisper_model: str = "small"  # dev: small (CPU int8). Prod: large-v3 (GPU).
    whisper_device: Literal["cpu", "cuda", "auto"] = "cpu"
    whisper_compute_type: str = "int8"
    whisper_language: str = "pl"
    whisper_beam_size: int = Field(default=5, ge=1)
    docling_ocr: bool = True

    @property
    def whisper_dir(self) -> str:
        return f"{self.root}/whisper/{self.whisper_model}"

    @property
    def docling_dir(self) -> str:
        return f"{self.root}/docling"

    @property
    def injection_dir(self) -> str:
        return f"{self.root}/injection/deberta-v3-base-prompt-injection-v2"

    @property
    def image_embed_dir(self) -> str:
        return f"{self.root}/image-embed/siglip-base-patch16-224"


class WorkerSecuritySettings(BaseModel):
    """Workers decrypt and encrypt private-zone data: they need the KEK and nothing else."""

    kek: SecretStr


class PromotionSettings(BaseModel):
    daily_limit: int = Field(default=20, ge=1)  # promotions per user per UTC day


class KeycloakAdminSettings(BaseModel):
    """Keycloak Admin REST API for user management (Stage 8), through the `spoldzielnia-admin`
    service account. Internal URL: the public proxy refuses ``/admin`` paths."""

    base_url: str = "http://keycloak:8080"
    realm: str = "spoldzielnia"
    client_id: str = "spoldzielnia-admin"
    # Docker secret `keycloak_admin__client_secret` (api only). Unset: user management is off.
    client_secret: SecretStr | None = None
    timeout_seconds: float = Field(default=10.0, gt=0)
    # Lifespan of the link in a password-reset e-mail.
    reset_link_lifespan_seconds: int = Field(default=24 * 3600, ge=300, le=7 * 24 * 3600)
    # Granting EXPERT/ADMIN, unblocking: the admin's last login with a second factor is fresher.
    step_up_max_age_seconds: int = Field(default=300, ge=60, le=3600)
    mutations_per_minute: int = Field(default=30, ge=1)
    temporary_passwords_per_hour: int = Field(default=10, ge=1)


class GovernanceSettings(BaseModel):
    """Expert review and creator consent (Stage 4)."""

    # Distinct experts whose approval makes an article CANONICAL.
    review_quorum: int = Field(default=1, ge=1, le=10)
    # Revoking contributions needs a login at most this old (step-up otherwise).
    revoke_max_auth_age_seconds: int = Field(default=600, ge=60, le=3600)
    # A vector-outbox operation failing this many times in a row raises an alert (4.4).
    outbox_alert_after: int = Field(default=5, ge=1)


class AtlasSettings(BaseModel):
    """Visual atlas: annotated photos, expert review, export, recognition (Stage 9)."""

    label_proposals_per_day: int = Field(default=10, ge=1)  # per member, UTC day
    submissions_per_day: int = Field(default=20, ge=1)  # photos sent to the atlas, per member
    # dHash distance at or below which a photo counts as already in the atlas (of 64 bits).
    duplicate_max_distance: int = Field(default=6, ge=0, le=20)
    # Preview/full images of other members' photos per viewer and hour (A7; thumbnails free).
    image_views_per_hour: int = Field(default=600, ge=1)
    page_size_max: int = Field(default=48, ge=1, le=200)
    # Dataset export (9.7, A9): off unless the operator turns it on; the UI cannot widen it.
    export_enabled: bool = False
    exports_per_day: int = Field(default=5, ge=1)  # per admin, UTC day
    export_link_ttl_seconds: int = Field(default=86_400, ge=300, le=7 * 86_400)
    export_step_up_max_age_seconds: int = Field(default=300, ge=60, le=3600)
    # Recognition (9.9). The score threshold is provisional: calibrate it on the cooperative's
    # own approved photos with `spoldzielnia atlas benchmark`.
    recognitions_per_day: int = Field(default=30, ge=1)
    recognition_min_score: float = Field(default=0.80, gt=0, lt=1)
    recognition_max_labels: int = Field(default=3, ge=1, le=10)
    recognition_references: int = Field(default=3, ge=0, le=6)


class RagSettings(BaseModel):
    """Cooperative RAG (5.1)."""

    top_k: int = Field(default=8, ge=1, le=50)
    # Cosine similarity a hit needs. Below it nothing counts as an answer (strict grounding).
    score_threshold: float = Field(default=0.45, ge=-1, le=1)
    question_max_chars: int = Field(default=1000, ge=10, le=10_000)
    answer_max_words: int = Field(default=350, ge=20, le=2000)
    # Link to an article in the frontend (Stage 6 routes).
    wiki_url_template: str = "https://spoldzielnia.localhost/wiki/{article_id}"

    @field_validator("wiki_url_template")
    @classmethod
    def _has_placeholder(cls, value: str) -> str:
        if "{article_id}" not in value:
            raise ValueError("rag.wiki_url_template must contain {article_id}")
        return value


class ShieldSettings(BaseModel):
    """Anti-distillation shield (Stage 5, docs/SECURITY.md)."""

    # Coverage budget (5.2): share of retrievable canonical blocks one account may touch.
    coverage_24h_max: float = Field(default=0.05, gt=0, le=1)
    coverage_7d_max: float = Field(default=0.15, gt=0, le=1)
    coverage_30d_max: float = Field(default=0.25, gt=0, le=1)
    # WARN once a window reaches this fraction of its limit.
    coverage_warn_fraction: float = Field(default=0.6, gt=0, lt=1)
    # No lock below this many unique blocks, whatever the ratio (gap G14: small corpora).
    min_blocks_before_lock: int = Field(default=50, ge=0)
    # How long the corpus size (the denominator) is cached in Redis.
    corpus_size_ttl_seconds: int = Field(default=60, ge=1, le=3600)
    # Admin alerts: POSTed as JSON when set (an internal relay; the API has no internet).
    alert_webhook_url: str | None = None

    # Semantic traversal detector (5.3). A rule-based logistic model: the weights are a
    # starting point, to be calibrated on real query logs.
    semantic_history: int = Field(default=50, ge=5, le=500)
    semantic_min_queries: int = Field(default=5, ge=2)
    semantic_threshold: float = Field(default=0.7, gt=0, lt=1)  # anomalous (risk input)
    semantic_hard_threshold: float = Field(default=0.97, gt=0, le=1)  # blocks on its own
    semantic_bias: float = -6.0
    semantic_w_consecutive: float = 2.5
    semantic_w_dispersion: float = 1.5
    semantic_w_novel_clusters: float = 3.0
    semantic_w_regularity: float = 1.5
    semantic_w_template: float = 2.0
    semantic_w_rate: float = 1.0
    semantic_rate_ref_per_hour: float = Field(default=60.0, gt=0)
    # k for the nightly k-means over canonical_wiki_index.
    semantic_clusters: int = Field(default=20, ge=2, le=1000)

    # Honeypots (5.4). The key (Docker secret `honeypot_key`) signs canary tokens: needed to
    # inject and to scan leaked text, not to detect a hit (the block flag does that).
    honeypot_key: SecretStr | None = None
    honeypot_prefix: str = Field(default="PSZ", pattern=r"^[A-Z]{2,5}$")
    honeypot_penalty: float = Field(default=0.5, gt=0, le=1)
    honeypot_lock_after: int = Field(default=2, ge=1)  # triggers within 30 days

    # Risk score (5.4, 5.6): incident points decay with this half-life.
    risk_half_life_days: float = Field(default=7.0, gt=0)

    # Verbatim-output guard (5.5): block when any is exceeded.
    verbatim_overlap_max: float = Field(default=0.70, gt=0, le=1)  # word 5-grams of a source
    verbatim_rouge_l_max: float = Field(default=0.70, gt=0, le=1)  # vs. all sources
    verbatim_span_max_words: int = Field(default=40, ge=5)
    verbatim_penalty: float = Field(default=0.3, ge=0, le=1)
    # Answers in another language than the sources (14.5, shield.translation): a source counts
    # as translated when sentences matched above the similarity hold this share of its words.
    verbatim_xlang_similarity: float = Field(default=0.86, gt=0, le=1)
    verbatim_xlang_share: float = Field(default=0.5, gt=0, le=1)
    verbatim_xlang_min_words: int = Field(default=20, ge=1)
    extraction_intent_penalty: float = Field(default=0.1, ge=0, le=1)
    # Watermark (5.5): Docker secret `watermark_key`. Without it answers are not watermarked.
    watermark_key: SecretStr | None = None
    watermark_z_threshold: float = Field(default=4.0, gt=0)
    # Also watermark canonical Wiki text a member reads (6.3). Off by default: it rewords
    # expert-approved text (synonyms only), which a cooperative may not want.
    watermark_wiki: bool = False

    # RiskEngine (5.6): risk = incidents + coverage term + semantic term, capped at 1.
    risk_coverage_weight: float = Field(default=0.4, ge=0, le=1)  # * (fraction of limit)^2
    risk_semantic_weight: float = Field(default=0.6, ge=0, le=1)  # * semantic score
    risk_elevated: float = Field(default=0.3, gt=0, lt=1)
    risk_high: float = Field(default=0.5, gt=0, lt=1)
    risk_critical: float = Field(default=0.7, gt=0, le=1)
    # Progressive degradation (SECURITY.md layer 5), never announced as detection.
    elevated_top_k: int = Field(default=4, ge=1)
    elevated_max_words: int = Field(default=150, ge=20)
    elevated_delay_ms: tuple[int, int] = (300, 1200)  # jittered extra latency
    high_top_k: int = Field(default=2, ge=1)
    # HIGH: a login with MFA at most this old, or a solved proof-of-work, per request.
    step_up_max_auth_age_seconds: int = Field(default=300, ge=60, le=3600)
    pow_difficulty_bits: int = Field(default=18, ge=1, le=28)
    # Each puzzle solved within 24h makes the next one this much harder (x4 per 2 bits).
    pow_escalation_bits: int = Field(default=2, ge=0, le=8)
    pow_max_bits: int = Field(default=28, ge=1, le=32)
    pow_ttl_seconds: int = Field(default=300, ge=30, le=3600)
    # Ignoring step-up is a signal too: each challenge issued while this many are still
    # unsolved adds risk, so a bot that never solves them ends at CRITICAL (locked).
    step_up_unresolved_max: int = Field(default=3, ge=1)
    unresolved_challenge_penalty: float = Field(default=0.1, ge=0, le=1)

    @model_validator(mode="after")
    def _risk_levels_ascend(self) -> Self:
        if not self.risk_elevated < self.risk_high < self.risk_critical:
            raise ValueError("risk levels must ascend: elevated < high < critical")
        low, high = self.elevated_delay_ms
        if not 0 <= low <= high <= 10_000:
            raise ValueError("shield.elevated_delay_ms must be 0 <= min <= max <= 10000")
        return self

    @model_validator(mode="after")
    def _windows_widen(self) -> Self:
        if not self.coverage_24h_max <= self.coverage_7d_max <= self.coverage_30d_max:
            raise ValueError("coverage limits must not shrink as the window widens")
        return self


class LlmSettings(BaseModel):
    """LLM endpoints over the standard OpenAI API (LM Studio, Ollama, vLLM, …).

    The local endpoint is the compose service ``llm``. In dev it relays to the host (LM Studio
    or Ollama), in prod it is an Ollama container. A model left empty means "not available":
    purposes that must stay local then fail with ``ModelUnavailable``, never fall back to
    the cloud.
    """

    local_base_url: str = "http://llm:11434/v1"
    chat_model: str | None = None  # e.g. "speakleash/bielik-11b-v2.3-instruct"
    vlm_model: str | None = None  # e.g. "qwen2.5-vl-7b-instruct"
    embed_model: str | None = None  # e.g. "text-embedding-embeddinggemma"
    # Speech-to-text on the local endpoint (10.4), e.g. a self-hosted whisper server.
    transcribe_model: str | None = None
    # Image embeddings on the local endpoint (10.6), e.g. SigLIP/CLIP behind an embedding server.
    image_embed_model: str | None = None
    # Docker secret `llm_api_key` (LM Studio requires a token; Ollama ignores it).
    api_key: SecretStr | None = None
    timeout_seconds: float = Field(default=180.0, gt=0)
    max_tokens: int = Field(default=4096, gt=0)
    temperature: float = Field(default=0.2, ge=0, le=2)
    # How a JSON schema reaches the model: ``response_format`` (constrained decoding) or, for
    # servers/models that can't do that, the system prompt (output is validated either way).
    structured_output: Literal["json_schema", "prompt"] = "json_schema"
    # Optional cloud deployment from ``.env`` (RAG only, unless its host is cleared, 10.1).
    allow_cloud: bool = False
    # Currency label of the estimated AI usage costs (15.3, US-09). Prices are entered per model
    # in this currency; the figures are estimates, not an invoice.
    usage_currency: str = Field(default="PLN", pattern=r"^[A-Z]{3}$")
    cloud_base_url: str | None = None
    cloud_model: str | None = None
    cloud_api_key: SecretStr | None = None
    # Stage 7: admins choose models in the UI, within the limits set here (the ceiling):
    # ``allow_cloud`` and the endpoint allowlists. ``env`` ignores the database configuration.
    config_source: Literal["db", "env"] = "db"
    # Extra local endpoints besides ``local_base_url`` (origins: scheme://host[:port]).
    local_endpoints: list[str] = Field(default_factory=list[str])
    # Cloud API hosts admins may use (https only), besides the host of ``cloud_base_url``.
    cloud_hosts: list[str] = Field(default_factory=list[str])
    # Endpoints that may be cleared for private data (10.1, AGENTS.md rule 4): origins
    # (scheme://host[:port]). ``None`` means the local endpoints (``local_base_url`` and
    # ``local_endpoints``); ``[]`` means none, so private features stay off. An admin clears a
    # provider within this list; the UI can narrow it, never widen it.
    private_data_endpoints: list[str] | None = None
    # Forward proxy for cloud endpoints (`llm-egress`, 7.3). Local endpoints never use it.
    egress_proxy_url: str | None = None
    # How long a process trusts its cached configuration without asking the database.
    config_ttl_seconds: float = Field(default=30.0, gt=0)
    probe_max_age_hours: int = Field(default=24, ge=1)
    playground_per_hour: int = Field(default=30, ge=1)
    # Re-embedding the text index with a new model (10.3): items per minute when the model is
    # not on a local endpoint (cost guard for cloud APIs). Local models run unthrottled.
    reembed_per_minute: int = Field(default=600, ge=1)
    # Speech-to-text over the API (10.4): recordings are cut into segments of at most this
    # length (providers cap the request size; 600 s of 16 kHz mono WAV is 19.2 MB) and sent with
    # this language. One request may take long on a CPU server, hence its own timeout.
    transcribe_segment_seconds: int = Field(default=600, ge=10, le=1200)
    transcribe_language: str = "pl"
    transcribe_timeout_seconds: float = Field(default=900.0, gt=0)
    # The media worker's built-in faster-whisper (optional, needs its model in the ``models``
    # volume) transcribes when no TRANSCRIBE model is routed. Never used when one is routed,
    # even if that one fails (no fallback, P5); it never leaves the server.
    builtin_transcription: bool = True
    # Scanned PDF pages (no text layer) go to the OCR purpose: a vision model reads each page
    # image (10.5). Pages beyond the limit fail the document (cost guard). Docling's own OCR
    # (optional, its models in the ``models`` volume) reads them when no OCR model is routed.
    ocr_max_pages: int = Field(default=200, ge=1)
    builtin_ocr: bool = True
    # The visual atlas (10.6): image embeddings from the IMAGE_EMBED model, else worker-core's
    # built-in SigLIP (optional, its model in the ``models`` volume). Recognition and atlas
    # indexing wait without either.
    builtin_image_embed: bool = True

    @field_validator(
        "chat_model",
        "vlm_model",
        "embed_model",
        "transcribe_model",
        "image_embed_model",
        "cloud_model",
        "cloud_base_url",
        "egress_proxy_url",
    )
    @classmethod
    def _empty_is_unset(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class SecuritySettings(BaseModel):
    # Keys for token encryption at rest and CSRF tokens are derived from it (HKDF).
    session_secret: SecretStr
    # Key-encryption key of the private zone (base64, 32 bytes): Docker secret `kek`.
    kek: SecretStr
    allowed_origins: list[str] = Field(default_factory=lambda: ["https://spoldzielnia.localhost"])
    # How long the server trusts a session before re-checking with the IdP (refresh).
    access_token_ttl_seconds: int = Field(default=15 * 60, ge=60)
    session_absolute_ttl_seconds: int = Field(default=12 * 60 * 60, ge=300)
    mfa_acr_values: list[str] = Field(default_factory=lambda: ["mfa"])
    mfa_amr_values: list[str] = Field(default_factory=lambda: ["otp", "mfa", "hwk", "fido"])
    # Dev/test only (`make mfa-off`): treat every session as MFA-satisfied. Refused in prod.
    mfa_required: bool = True
    # Waiting for another request's refresh of the same token family.
    refresh_lock_timeout_seconds: float = Field(default=10.0, gt=0)


class _BaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SPOLDZIELNIA_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        secrets_dir=DEFAULT_SECRETS_DIR,
        secrets_dir_missing="ok",  # local runs outside Docker have no /run/secrets
        secrets_prefix="",
        secrets_nested_delimiter="__",
        extra="ignore",
        frozen=True,
    )

    environment: Literal["dev", "test", "prod"] = "prod"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Docker secrets map to nested fields: /run/secrets/db__app_password -> db.app_password.
        secrets = NestedSecretsSettingsSource(file_secret_settings)
        return (init_settings, env_settings, dotenv_settings, secrets)


class Settings(_BaseSettings):
    """Runtime settings of the API process."""

    root_path: str = "/api"
    api_host: str = "0.0.0.0"  # noqa: S104 # nosec B104 - in-container, behind Caddy
    api_port: int = 8000
    forwarded_allow_ips: str = "127.0.0.1"

    db: DbSettings
    redis: RedisSettings
    oidc: OidcSettings
    security: SecuritySettings
    qdrant: QdrantSettings = Field(default_factory=QdrantSettings)
    s3: S3Settings = Field(default_factory=S3Settings)
    llm: LlmSettings = Field(default_factory=LlmSettings)
    clamav: ClamavSettings = Field(default_factory=ClamavSettings)
    uploads: UploadSettings = Field(default_factory=UploadSettings)
    fetch: FetchSettings = Field(default_factory=FetchSettings)
    promotion: PromotionSettings = Field(default_factory=PromotionSettings)
    governance: GovernanceSettings = Field(default_factory=GovernanceSettings)
    rag: RagSettings = Field(default_factory=RagSettings)
    shield: ShieldSettings = Field(default_factory=ShieldSettings)
    keycloak_admin: KeycloakAdminSettings = Field(default_factory=KeycloakAdminSettings)
    atlas: AtlasSettings = Field(default_factory=AtlasSettings)

    @model_validator(mode="after")
    def _require_runtime_secrets(self) -> Self:
        if self.db.app_password is None:
            raise ValueError("db.app_password is required")
        if self.environment == "prod" and not self.security.mfa_required:
            raise ValueError("security.mfa_required cannot be disabled in prod")
        return self


class MigrateSettings(_BaseSettings):
    """Settings of the one-shot ``migrate`` job: schema-owner DSN only."""

    db: DbSettings = Field(default_factory=DbSettings)

    @model_validator(mode="after")
    def _require_owner_password(self) -> Self:
        if self.db.owner_password is None:
            raise ValueError("db.owner_password is required")
        return self


class AuditVerifySettings(_BaseSettings):
    """Settings of the one-shot ``audit-verify`` job: runtime DSN only (it only reads the log)."""

    db: DbSettings = Field(default_factory=DbSettings)

    @model_validator(mode="after")
    def _require_app_password(self) -> Self:
        if self.db.app_password is None:
            raise ValueError("db.app_password is required")
        return self


class WorkerSettings(_BaseSettings):
    """Celery workers (`spoldzielnia worker`): app DSN, broker, KEK, storage, scanner, fetcher,
    models. No session secret and no OIDC client secret."""

    db: DbSettings = Field(default_factory=DbSettings)
    redis: RedisSettings
    security: WorkerSecuritySettings
    s3: S3Settings = Field(default_factory=S3Settings)
    clamav: ClamavSettings = Field(default_factory=ClamavSettings)
    uploads: UploadSettings = Field(default_factory=UploadSettings)
    fetch: FetchSettings = Field(default_factory=FetchSettings)
    models: ModelSettings = Field(default_factory=ModelSettings)
    llm: LlmSettings = Field(default_factory=LlmSettings)
    qdrant: QdrantSettings = Field(default_factory=QdrantSettings)
    governance: GovernanceSettings = Field(default_factory=GovernanceSettings)
    shield: ShieldSettings = Field(default_factory=ShieldSettings)
    atlas: AtlasSettings = Field(default_factory=AtlasSettings)
    concurrency: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _require_app_password(self) -> Self:
        if self.db.app_password is None:
            raise ValueError("db.app_password is required")
        return self


class BeatSettings(_BaseSettings):
    """Celery beat (`spoldzielnia beat`, 4.4): the broker and nothing else."""

    redis: RedisSettings
    schedule_file: str = "/tmp/celerybeat-schedule"  # noqa: S108 # nosec B108 - tmpfs in the container


class LlmEgressSettings(_BaseSettings):
    """The ``llm-egress`` forward proxy (7.3): the cloud allowlist and nothing else."""

    llm: LlmSettings = Field(default_factory=LlmSettings)
    listen_host: str = "0.0.0.0"  # noqa: S104 # nosec B104 - in-container, `backend` network
    listen_port: int = 3128
    header_timeout_seconds: float = Field(default=10.0, gt=0)
    connect_timeout_seconds: float = Field(default=10.0, gt=0)
    idle_timeout_seconds: float = Field(default=300.0, gt=0)
    max_connections: int = Field(default=64, ge=1)


class BootstrapSettings(_BaseSettings):
    """Settings of the one-shot ``bootstrap`` job: object storage and Qdrant collections."""

    s3: S3Settings = Field(default_factory=S3Settings)
    qdrant: QdrantSettings = Field(default_factory=QdrantSettings)

    @model_validator(mode="after")
    def _require_secrets(self) -> Self:
        if self.s3.secret_key is None:
            raise ValueError("s3.secret_key is required")
        if self.qdrant.api_key is None:
            raise ValueError("qdrant.api_key is required")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once per process; raises ``ValidationError`` if required values are missing."""
    return Settings()  # pyright: ignore[reportCallIssue]  # values come from env/secrets
