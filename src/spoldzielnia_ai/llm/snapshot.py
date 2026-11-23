"""The frozen form of an ACTIVE LLM configuration revision (``llm_config_revision.snapshot``).

Written once by activation (7.1), read by every process (7.2). No secrets: keys stay on the
provider row and are decrypted when a process loads the snapshot. Deployment keys are model ids,
which the database trigger of migration 0012 relies on (``deployments -> <key> -> provider_id``).
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID  # noqa: TC003 - pydantic resolves annotations at runtime

from pydantic import BaseModel

from spoldzielnia_ai.db.enums import (  # noqa: TC001 - pydantic resolves annotations at runtime
    LlmCapability,
    LlmProviderKind,
    LlmRoutePurpose,
)


class SnapshotDeployment(BaseModel):
    provider_id: UUID
    provider_name: str
    model_id: UUID
    model: str
    capability: LlmCapability
    base_url: str
    litellm_provider: str = "openai"
    # As classified at activation; processes classify ``base_url`` again on load (S4).
    kind: LlmProviderKind
    # Cleared for private data at activation (10.1); processes re-check the live provider row
    # and ``llm.private_data_endpoints`` on load (P1/P2).
    private_data: bool = False
    inherit_env_key: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    structured_output: Literal["json_schema", "prompt"] | None = None
    timeout_seconds: float | None = None
    embed_dimension: int | None = None
    # Price per million tokens in micro-units (15.3). Frozen with the revision, so the usage
    # counters price a call with the price its revision had.
    price_in_micro: int | None = None
    price_out_micro: int | None = None

    @property
    def display_name(self) -> str:
        return f"{self.provider_name}/{self.model}"


class ConfigSnapshot(BaseModel):
    version: Literal[1] = 1
    routes: dict[LlmRoutePurpose, str]
    deployments: dict[str, SnapshotDeployment]

    def deployment_for(self, purpose: LlmRoutePurpose) -> SnapshotDeployment | None:
        key = self.routes.get(purpose)
        return self.deployments.get(key) if key is not None else None
