"""AI usage monitoring (chunk 15.1, US-09).

Revision ID: 0029_ai_usage
Revises: 0028_user_locale
Create Date: 2026-11-23

- ``llm_model.price_in_micro`` / ``price_out_micro``: the price of one million prompt and
  completion tokens, in millionths of the configured currency. Both or neither (CHECK), so a
  model is either priced or reported as tokens only.
- ``llm_usage_daily``: counters per day, user (NULL = system work), purpose and model. No prompt,
  answer or other content is stored (rule: counters only). Two partial unique indexes give the
  upsert a key for both user rows and system rows, since NULLs are distinct in a plain unique
  index.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0029_ai_usage"
down_revision: str | None = "0028_user_locale"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_PRICE_MICRO = 1_000_000_000_000  # 1 000 000 currency units per million tokens


def upgrade() -> None:
    op.add_column("llm_model", sa.Column("price_in_micro", sa.BigInteger(), nullable=True))
    op.add_column("llm_model", sa.Column("price_out_micro", sa.BigInteger(), nullable=True))
    op.create_check_constraint(
        op.f("ck_llm_model_price_in"),
        "llm_model",
        f"price_in_micro IS NULL OR price_in_micro BETWEEN 0 AND {MAX_PRICE_MICRO}",
    )
    op.create_check_constraint(
        op.f("ck_llm_model_price_out"),
        "llm_model",
        f"price_out_micro IS NULL OR price_out_micro BETWEEN 0 AND {MAX_PRICE_MICRO}",
    )
    op.create_check_constraint(
        op.f("ck_llm_model_price_pair"),
        "llm_model",
        "(price_in_micro IS NULL) = (price_out_micro IS NULL)",
    )

    op.create_table(
        "llm_usage_daily",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("model", sa.String(256), nullable=False),
        sa.Column("calls", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("unpriced_calls", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("prompt_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("completion_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("cost_micro", sa.BigInteger(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name=op.f("fk_llm_usage_daily_user_id_app_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_usage_daily")),
        sa.CheckConstraint(
            "calls >= 0 AND unpriced_calls >= 0 AND unpriced_calls <= calls",
            name="calls_counted",
        ),
        sa.CheckConstraint(
            "prompt_tokens >= 0 AND completion_tokens >= 0 AND cost_micro >= 0",
            name="counts_non_negative",
        ),
        sa.CheckConstraint("char_length(purpose) BETWEEN 1 AND 32", name="purpose_length"),
        sa.CheckConstraint("char_length(model) BETWEEN 1 AND 256", name="model_length"),
    )
    op.create_index(
        op.f("uq_llm_usage_daily_user"),
        "llm_usage_daily",
        ["day", "user_id", "purpose", "model"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.create_index(
        op.f("uq_llm_usage_daily_system"),
        "llm_usage_daily",
        ["day", "purpose", "model"],
        unique=True,
        postgresql_where=sa.text("user_id IS NULL"),
    )
    op.create_index(op.f("ix_llm_usage_daily_day"), "llm_usage_daily", ["day"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_llm_usage_daily_day"), table_name="llm_usage_daily")
    op.drop_index(op.f("uq_llm_usage_daily_system"), table_name="llm_usage_daily")
    op.drop_index(op.f("uq_llm_usage_daily_user"), table_name="llm_usage_daily")
    op.drop_table("llm_usage_daily")
    op.drop_constraint(op.f("ck_llm_model_price_pair"), "llm_model", type_="check")
    op.drop_constraint(op.f("ck_llm_model_price_out"), "llm_model", type_="check")
    op.drop_constraint(op.f("ck_llm_model_price_in"), "llm_model", type_="check")
    op.drop_column("llm_model", "price_out_micro")
    op.drop_column("llm_model", "price_in_micro")
