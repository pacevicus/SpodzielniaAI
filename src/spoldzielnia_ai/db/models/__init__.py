"""ORM models. Importing this package registers every table on ``Base.metadata``."""

from spoldzielnia_ai.db.base import Base
from spoldzielnia_ai.db.models.atlas import (
    AnnotatedImage,
    AtlasExport,
    AtlasImage,
    AtlasRegion,
    AtlasReview,
    AtlasSubmission,
    ImageAnnotation,
    VisualDomain,
    VisualLabel,
)
from spoldzielnia_ai.db.models.audit import SecurityAuditLog
from spoldzielnia_ai.db.models.branding import Branding
from spoldzielnia_ai.db.models.index_generation import IndexGeneration
from spoldzielnia_ai.db.models.llm import (
    LlmConfigRevision,
    LlmModel,
    LlmProvider,
    LlmRouting,
    LlmUsageDaily,
)
from spoldzielnia_ai.db.models.model_wait import ModelWait
from spoldzielnia_ai.db.models.outbox import VectorOutbox
from spoldzielnia_ai.db.models.private_note import NoteAttachment, NoteDerivative, PrivateNote
from spoldzielnia_ai.db.models.promotion import Promotion
from spoldzielnia_ai.db.models.user import User
from spoldzielnia_ai.db.models.wiki import ArticleBlock, ExpertReview, WikiArticle

__all__ = [
    "AnnotatedImage",
    "ArticleBlock",
    "AtlasExport",
    "AtlasImage",
    "AtlasRegion",
    "AtlasReview",
    "AtlasSubmission",
    "Base",
    "Branding",
    "ExpertReview",
    "ImageAnnotation",
    "IndexGeneration",
    "LlmConfigRevision",
    "LlmModel",
    "LlmProvider",
    "LlmRouting",
    "LlmUsageDaily",
    "ModelWait",
    "NoteAttachment",
    "NoteDerivative",
    "PrivateNote",
    "Promotion",
    "SecurityAuditLog",
    "User",
    "VectorOutbox",
    "VisualDomain",
    "VisualLabel",
    "WikiArticle",
]
