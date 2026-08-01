import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.orm import MaterialityTier, ModelStatus
from app.services.materiality import MaterialityInputs


class ModelRegisterRequest(BaseModel):
    # protected_namespaces=(): "model_type" is legitimate domain
    # vocabulary in a model-governance tool, not a Pydantic internal.
    # Same fix as app/core/config.py on Day 1, applied here for the
    # same reason — silencing this properly rather than accumulating
    # warning noise across every schema that touches "model_*" fields.
    model_config = ConfigDict(protected_namespaces=())

    name: str = Field(min_length=3, max_length=200)
    description: str = ""
    owner_team: str
    model_type: str  # rag | agent | classifier | llm_app
    materiality_inputs: MaterialityInputs


class ModelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: uuid.UUID
    name: str
    description: str
    owner_team: str
    materiality_tier: MaterialityTier
    materiality_score: dict
    status: ModelStatus
    model_type: str
    is_self_governance: bool
    created_at: datetime
