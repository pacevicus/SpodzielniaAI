"""Model metadata, enum <-> DB value mapping and agreement with the shipped migration."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import ARRAY

from spoldzielnia_ai.db.enums import (
    PG_ENUM_NAMES,
    ArticleStatus,
    ConsentStatus,
    ReviewDecision,
    Role,
    pg_enum,
)
from spoldzielnia_ai.db.migrate import alembic_config, head_revision
from spoldzielnia_ai.db.models import (
    ArticleBlock,
    Base,
    ExpertReview,
    NoteAttachment,
    NoteDerivative,
    PrivateNote,
    User,
    WikiArticle,
)

if TYPE_CHECKING:
    from enum import StrEnum
    from types import ModuleType

ENUMS: list[type[StrEnum]] = [Role, ConsentStatus, ArticleStatus, ReviewDecision]
DIALECT = postgresql.dialect()


def _table(model: type[Base]) -> Table:
    return cast("Table", model.__table__)


def test_tables_registered() -> None:
    assert set(Base.metadata.tables) == {
        "app_user",
        "private_note",
        "wiki_article",
        "article_block",
        "expert_review",
        "security_audit_log",
        "note_attachment",
        "note_derivative",
        "promotion",
        "vector_outbox",
        "llm_provider",
        "llm_model",
        "llm_config_revision",
        "llm_routing",
        "visual_domain",
        "visual_label",
        "annotated_image",
        "image_annotation",
        "atlas_submission",
        "atlas_image",
        "atlas_region",
        "atlas_review",
        "atlas_export",
        "branding",
        "model_wait",
        "index_generation",
        "llm_usage_daily",
    }


@pytest.mark.parametrize("enum_cls", ENUMS)
def test_enum_db_values_are_member_values(enum_cls: type[StrEnum]) -> None:
    sa_type = pg_enum(enum_cls)
    assert sa_type.name == PG_ENUM_NAMES[enum_cls]
    assert sa_type.enums == [member.value for member in enum_cls]


Processor = Callable[[object], object]


def _processors(sa_type: SAEnum) -> tuple[Processor, Processor]:
    bind = cast("Processor | None", sa_type.bind_processor(DIALECT))
    result = cast("Processor | None", sa_type.result_processor(DIALECT, None))
    assert bind is not None
    assert result is not None
    return bind, result


@pytest.mark.parametrize("enum_cls", ENUMS)
def test_enum_round_trips_through_type_processors(enum_cls: type[StrEnum]) -> None:
    bind, result = _processors(pg_enum(enum_cls))
    for member in enum_cls:
        assert bind(member) == member.value
        assert result(member.value) is member


def test_enum_rejects_unknown_strings() -> None:
    bind, _ = _processors(pg_enum(ConsentStatus))
    with pytest.raises(LookupError):
        bind("DELETED")


@pytest.mark.parametrize(
    ("model", "column", "enum_cls"),
    [
        (WikiArticle, "status", ArticleStatus),
        (ArticleBlock, "consent_status", ConsentStatus),
        (ExpertReview, "decision", ReviewDecision),
    ],
)
def test_enum_columns_use_named_pg_types(
    model: type[Base], column: str, enum_cls: type[StrEnum]
) -> None:
    sa_type = _table(model).c[column].type
    assert isinstance(sa_type, SAEnum)
    assert sa_type.name == PG_ENUM_NAMES[enum_cls]


def test_user_roles_is_array_of_role_enum() -> None:
    sa_type = _table(User).c["roles"].type
    assert isinstance(sa_type, ARRAY)
    item = cast("ARRAY[Any]", sa_type).item_type
    assert isinstance(item, SAEnum)
    assert item.name == "user_role"


def test_block_provenance_columns_are_mandatory() -> None:
    """AGENTS.md §3.2: every block carries author_id and consent_status."""
    columns = _table(ArticleBlock).c
    assert columns["author_id"].nullable is False
    assert columns["consent_status"].nullable is False


def test_private_note_is_owned_and_stores_only_ciphertext() -> None:
    for model in (PrivateNote, NoteAttachment, NoteDerivative):
        columns = _table(model).c
        assert columns["owner_id"].nullable is False
        assert "dek_wrapped" in columns
    note = set(_table(PrivateNote).c.keys())
    assert {"title_ciphertext", "body_ciphertext"} <= note
    assert not {"title", "body", "body_nonce"} & note
    assert "filename" not in _table(NoteAttachment).c


def test_constraint_names_follow_convention() -> None:
    names = {c.name for c in _table(ArticleBlock).constraints}
    assert {
        "pk_article_block",
        "uq_article_block_article_id_position",
        "fk_article_block_author_id_app_user",
        "ck_article_block_position_non_negative",
    } <= names


def _revision_module(revision: str) -> ModuleType:
    script = ScriptDirectory.from_config(alembic_config()).get_revision(revision)
    assert script is not None
    return script.module


def test_migrations_form_a_single_linear_history() -> None:
    script = ScriptDirectory.from_config(alembic_config())
    history = [rev.revision for rev in script.walk_revisions()]  # head first
    assert script.get_heads() == [head_revision()]
    assert history[::-1] == [
        "0001_core_schema",
        "0002_rls",
        "0003_audit",
        "0004_private_zone",
        "0005_quarantine",
        "0006_promotion",
        "0007_governance",
        "0008_consent",
        "0009_vector_outbox",
        "0010_account_lock",
        "0011_hidden_articles",
        "0012_llm_config",
        "0013_user_admin",
        "0014_visual_labels",
        "0015_image_annotation",
        "0016_atlas",
        "0017_atlas_export",
        "0018_recognition",
        "0019_provider_clearance",
        "0020_model_wait",
        "0021_index_generations",
        "0022_transcribe_purpose",
        "0023_ocr_purpose",
        "0024_image_embed",
        "0025_processors_seen",
        "0026_provider_removal",
        "0027_branding",
        "0028_user_locale",
        "0029_ai_usage",
    ]


def test_migration_enum_types_match_python_enums() -> None:
    migrated = {
        e.name: list(e.enums)
        for revision in (
            "0001_core_schema",
            "0004_private_zone",
            "0006_promotion",
            "0009_vector_outbox",
            "0012_llm_config",
            "0014_visual_labels",
            "0015_image_annotation",
            "0016_atlas",
            "0017_atlas_export",
            "0021_index_generations",
        )
        for e in cast("tuple[SAEnum, ...]", _revision_module(revision).ENUMS)
    }
    migrated["derivative_status"].append("QUARANTINED")  # added by 0005_quarantine
    migrated["derivative_status"].append("WAITING_FOR_MODEL")  # added by 0020_model_wait
    migrated["derivative_kind"].append("ANNOTATIONS")  # added by 0015_image_annotation
    migrated["derivative_kind"].append("RECOGNITION")  # added by 0018_recognition
    migrated["llm_capability"].append("TRANSCRIBE")  # added by 0022_transcribe_purpose
    migrated["llm_route_purpose"].append("TRANSCRIBE")  # added by 0022_transcribe_purpose
    migrated["llm_route_purpose"].append("OCR")  # added by 0023_ocr_purpose
    migrated["llm_capability"].append("IMAGE_EMBED")  # added by 0024_image_embed
    migrated["llm_route_purpose"].append("IMAGE_EMBED")  # added by 0024_image_embed
    assert migrated == {name: [m.value for m in enum] for enum, name in PG_ENUM_NAMES.items()}
