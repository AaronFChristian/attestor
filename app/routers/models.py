"""
Model inventory endpoints.

Registration is model_owner or mrm_head only — a validator should never be
the one who registers the thing they might later validate; that's a
segregation-of-duties smell even at registration time.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedUser, require_role
from app.core.database import get_db
from app.models.orm import GovernedModel, User
from app.models.schemas import ModelRegisterRequest, ModelResponse
from app.services import audit
from app.services.materiality import compute_materiality

router = APIRouter(prefix="/models", tags=["models"])


@router.post("", response_model=ModelResponse, status_code=status.HTTP_201_CREATED)
async def register_model(
    payload: ModelRegisterRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(require_role("model_owner", "mrm_head")),
):
    existing = await db.execute(select(GovernedModel).where(GovernedModel.name == payload.name))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail=f"Model '{payload.name}' is already registered.")

    owner_row = await db.execute(select(User).where(User.keycloak_subject == user.subject))
    owner = owner_row.scalar_one_or_none()
    if owner is None:
        raise HTTPException(
            status_code=422,
            detail="No local user record for this identity. Run scripts/seed_data.py or register via /users first.",
        )

    result = compute_materiality(payload.materiality_inputs)

    model = GovernedModel(
        id=uuid.uuid4(),
        name=payload.name,
        description=payload.description,
        owner_team=payload.owner_team,
        owner_user_id=owner.id,
        materiality_tier=result.tier,
        materiality_score=result.model_dump(mode="json"),
        model_type=payload.model_type,
    )
    db.add(model)

    await audit.record(
        db,
        actor=user.email,
        action="register_model",
        resource_type="governed_model",
        resource_id=str(model.id),
        detail={"name": model.name, "tier": result.tier.value, "score": result.weighted_score},
    )

    await db.commit()
    await db.refresh(model)
    return model


@router.get("", response_model=list[ModelResponse])
async def list_models(
    db: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(
        require_role("model_owner", "validator", "mrm_head", "auditor")
    ),
):
    result = await db.execute(select(GovernedModel).order_by(GovernedModel.created_at.desc()))
    return result.scalars().all()


@router.get("/{model_id}", response_model=ModelResponse)
async def get_model(
    model_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(
        require_role("model_owner", "validator", "mrm_head", "auditor")
    ),
):
    result = await db.execute(select(GovernedModel).where(GovernedModel.id == model_id))
    model = result.scalar_one_or_none()
    if model is None:
        raise HTTPException(status_code=404, detail="Model not found.")
    return model
