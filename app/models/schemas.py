import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.orm import MaterialityTier, ModelStatus
from app.services.materiality import MaterialityInputs


class ModelRegisterRequest(BaseModel):
    name: str = Field(min_length=3, max_length=200)
    description: str = ""
    owner_team: str
    model_type: str  # rag | agent | classifier | llm_app
    materiality_inputs: MaterialityInputs


class ModelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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
