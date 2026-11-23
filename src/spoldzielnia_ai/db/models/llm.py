from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from spoldzielnia_ai.db.base import Base, Entity
from spoldzielnia_ai.db.enums import (
    LlmCapability,
    LlmProviderKind,
    LlmRevisionStatus,
    LlmRoutePurpose,
)


class LlmProvider(Entity):
    """An OpenAI-compatible endpoint managed by an admin (7.1). Global configuration, not user
    data (no RLS). ``kind`` is derived from the endpoint allowlists in settings and re-checked at
    runtime; it decides the network path only. The API key is envelope-encrypted under the row's
    DEK (AAD ``llm_provider:<id>``); ``inherit_env_key`` uses the Docker secret instead
    (providers imported from ``.env``).

    ``private_data_cleared`` (10.1) is what lets private purposes use the provider: set by an
    ADMIN with step-up, together with the region, retention and contract reference shown to
    members. It counts only while the endpoint is on ``llm.private_data_endpoints``.

    ``deleted_at`` (11.1): an archived provider, removed by an admin but still named by the
    history of revisions. Hidden, without a key or clearance, never routed to again; its name is
    free for a new provider."""

    __tablename__ = "llm_provider"
    # Timestamps and identity values come back via RETURNING: the admin API returns the rows
    # after their session has closed.
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012 - SQLAlchemy reads it
    __table_args__ = (
        CheckConstraint(
            "NOT private_data_cleared OR (cleared_region IS NOT NULL AND cleared_retention IS NOT"
            " NULL AND cleared_contract_ref IS NOT NULL AND cleared_at IS NOT NULL)",
            name="clearance_complete",
        ),
        Index(
            "uq_llm_provider_name",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    name: Mapped[str] = mapped_column(String(64))
    kind: Mapped[LlmProviderKind]
    base_url: Mapped[str] = mapped_column(String(512))
    litellm_provider: Mapped[str] = mapped_column(String(32), server_default="openai")
    api_key_wrapped: Mapped[bytes | None]
    api_key_ciphertext: Mapped[bytes | None]
    api_key_hint: Mapped[str | None] = mapped_column(String(8))
    inherit_env_key: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), default=False
    )
    structured_output: Mapped[str | None] = mapped_column(String(16))
    timeout_seconds: Mapped[float | None] = mapped_column(Float)
    disabled_at: Mapped[datetime | None]
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))
    private_data_cleared: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), default=False
    )
    cleared_region: Mapped[str | None] = mapped_column(String(64))
    cleared_retention: Mapped[str | None] = mapped_column(String(64))
    cleared_contract_ref: Mapped[str | None] = mapped_column(String(200))
    cleared_by: Mapped[UUID | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))
    cleared_at: Mapped[datetime | None]
    deleted_at: Mapped[datetime | None]


class LlmModel(Entity):
    """A model on a provider, with its generation parameters and the last probe (7.4). The
    probe report holds timings, token counts and pass/fail only: never prompts or output."""

    __tablename__ = "llm_model"
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012 - SQLAlchemy reads it
    __table_args__ = (
        UniqueConstraint("provider_id", "model_name", "capability"),
        CheckConstraint(
            "price_in_micro IS NULL OR price_in_micro BETWEEN 0 AND 1000000000000",
            name="price_in",
        ),
        CheckConstraint(
            "price_out_micro IS NULL OR price_out_micro BETWEEN 0 AND 1000000000000",
            name="price_out",
        ),
        CheckConstraint("(price_in_micro IS NULL) = (price_out_micro IS NULL)", name="price_pair"),
    )

    provider_id: Mapped[UUID] = mapped_column(
        ForeignKey("llm_provider.id", ondelete="RESTRICT"), index=True
    )
    model_name: Mapped[str] = mapped_column(String(256))
    capability: Mapped[LlmCapability]
    temperature: Mapped[float | None] = mapped_column(Float)
    max_tokens: Mapped[int | None] = mapped_column(Integer)
    embed_dimension: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(String(500))
    # Price of one million prompt / completion tokens, in millionths of the usage currency
    # (15.1). Both or neither: an unpriced model is reported as tokens only.
    price_in_micro: Mapped[int | None] = mapped_column(BigInteger)
    price_out_micro: Mapped[int | None] = mapped_column(BigInteger)
    disabled_at: Mapped[datetime | None]
    last_probe_at: Mapped[datetime | None]
    last_probe_ok: Mapped[bool | None]
    last_probe_report: Mapped[dict[str, Any] | None]
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))


class LlmConfigRevision(Entity):
    """A numbered configuration. Exactly one is ACTIVE. Activation freezes ``snapshot`` (the
    resolved providers, models and routes, without secrets); the runtime reads only the
    snapshot, so later catalog edits change nothing until the next activation. ACTIVE and
    ARCHIVED rows are immutable (trigger, migration 0012)."""

    __tablename__ = "llm_config_revision"
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012 - SQLAlchemy reads it
    __table_args__ = (
        Index(
            "uq_llm_config_revision_one_active",
            "status",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    number: Mapped[int] = mapped_column(BigInteger, Identity(always=True), unique=True)
    status: Mapped[LlmRevisionStatus] = mapped_column(
        server_default=LlmRevisionStatus.DRAFT.value, default=LlmRevisionStatus.DRAFT
    )
    comment: Mapped[str | None] = mapped_column(String(500))
    snapshot: Mapped[dict[str, Any] | None]
    based_on: Mapped[int | None] = mapped_column(BigInteger)
    rolled_back_from: Mapped[int | None] = mapped_column(BigInteger)
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))
    activated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    activated_at: Mapped[datetime | None]


class LlmRouting(Base):
    """Purpose → model in a DRAFT revision. A trigger rejects providers not cleared for private
    data on private purposes (10.1), capability mismatches, and any change once the revision
    left DRAFT."""

    __tablename__ = "llm_routing"

    revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("llm_config_revision.id", ondelete="CASCADE"), primary_key=True
    )
    purpose: Mapped[LlmRoutePurpose] = mapped_column(primary_key=True)
    model_id: Mapped[UUID] = mapped_column(
        ForeignKey("llm_model.id", ondelete="RESTRICT"), index=True
    )


class LlmUsageDaily(Base):
    """Usage counters (15.1, US-09): one row per day, user (NULL = system work), purpose and
    model. Counters only: no prompt, answer or file content is ever stored. Not RLS-protected
    (like ``llm_model``): the API filters by the caller's own ``user_id``, and only ADMIN reads
    all rows. Deleting a user removes their counters (``ON DELETE CASCADE``)."""

    __tablename__ = "llm_usage_daily"
    __table_args__ = (
        Index(
            "uq_llm_usage_daily_user",
            "day",
            "user_id",
            "purpose",
            "model",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        Index(
            "uq_llm_usage_daily_system",
            "day",
            "purpose",
            "model",
            unique=True,
            postgresql_where=text("user_id IS NULL"),
        ),
        Index("ix_llm_usage_daily_day", "day"),
        CheckConstraint(
            "calls >= 0 AND unpriced_calls >= 0 AND unpriced_calls <= calls",
            name="calls_counted",
        ),
        CheckConstraint(
            "prompt_tokens >= 0 AND completion_tokens >= 0 AND cost_micro >= 0",
            name="counts_non_negative",
        ),
        CheckConstraint("char_length(purpose) BETWEEN 1 AND 32", name="purpose_length"),
        CheckConstraint("char_length(model) BETWEEN 1 AND 256", name="model_length"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    day: Mapped[date]
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    purpose: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(256))
    calls: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    unpriced_calls: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    prompt_tokens: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    completion_tokens: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    cost_micro: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
