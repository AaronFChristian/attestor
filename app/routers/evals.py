"""
Eval, evidence, and drift endpoints.

RBAC notes worth reading before adding routes here:

- Triggering an eval is validator/mrm_head only. A model OWNER triggering
  evals on their own model and then choosing which results to surface is a
  segregation-of-duties problem, even though it feels harmless.
- Auditors get read on everything and write on nothing. There is no
  auditor-writable route in this file and there should never be one.
"""
import uuid

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedUser, require_role
from app.core.config import get_settings
from app.core.database import get_db
from app.models.orm import EvalRun, EvidenceRecord, GovernedModel
from app.services import audit
from app.services.drift import detect_metric_drift

router = APIRouter(prefix="/evals", tags=["evals"])
settings = get_settings()


class EvalTriggerRequest(BaseModel):
    dataset_name: str
    dataset_version: str = "v1"
    prompt_hash: str = Field(
        default="baseline",
        description="Identifier for the prompt version under test. Part of the "
        "idempotency key — the same triple returns the cached run.",
    )
    rubric_criteria: list[str] = Field(
        default_factory=lambda: [
            "The output is grounded in retrievable evidence rather than plausible assertion.",
            "The output correctly identifies the material risk in the scenario.",
            "The output's stated rationale actually supports its conclusion.",
        ]
    )


class EvalRunResponse(BaseModel):
    id: uuid.UUID
    model_id: uuid.UUID
    evidence_id: uuid.UUID
    dataset_version: str
    prompt_hash: str
    metrics: dict
    status: str

    class Config:
        from_attributes = True


@router.post("/{model_id}/run", status_code=status.HTTP_202_ACCEPTED)
async def trigger_eval(
    model_id: uuid.UUID,
    payload: EvalTriggerRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(require_role("validator", "mrm_head")),
):
    """Queue an eval suite. Returns 202 immediately — the run itself happens
    on the worker because a full suite takes minutes and would otherwise
    time out the request and get retried, double-billing the tokens."""
    result = await db.execute(select(GovernedModel).where(GovernedModel.id == model_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Model not found.")

    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    job = await redis.enqueue_job(
        "execute_eval_suite",
        str(model_id),
        payload.dataset_name,
        payload.dataset_version,
        payload.prompt_hash,
        payload.rubric_criteria,
        user.email,
    )
    await redis.aclose()

    await audit.record(
        db,
        actor=user.email,
        action="trigger_eval_suite",
        resource_type="governed_model",
        resource_id=str(model_id),
        detail={
            "dataset_name": payload.dataset_name,
            "dataset_version": payload.dataset_version,
            "job_id": job.job_id if job else None,
        },
    )
    await db.commit()

    return {
        "status": "queued",
        "job_id": job.job_id if job else None,
        "model_id": str(model_id),
        "note": "Poll GET /evals/{model_id}/runs for results.",
    }


@router.get("/{model_id}/runs", response_model=list[EvalRunResponse])
async def list_eval_runs(
    model_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(
        require_role("model_owner", "validator", "mrm_head", "auditor")
    ),
):
    result = await db.execute(
        select(EvalRun).where(EvalRun.model_id == model_id).order_by(EvalRun.created_at.desc())
    )
    return result.scalars().all()


@router.get("/{model_id}/drift")
async def get_drift(
    model_id: uuid.UUID,
    metric_name: str = "rubric_mean",
    db: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(
        require_role("model_owner", "validator", "mrm_head", "auditor")
    ),
):
    signal = await detect_metric_drift(db, model_id, metric_name)
    if signal is None:
        return {
            "signal": None,
            "note": (
                "Insufficient eval history to establish a baseline. At least two "
                "completed runs are required before drift is meaningful."
            ),
        }
    return {"signal": signal.to_payload()}


@router.get("/evidence/{evidence_id}")
async def get_evidence(
    evidence_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(
        require_role("model_owner", "validator", "mrm_head", "auditor")
    ),
):
    """Resolve an evidence record. This endpoint is what makes a finding's
    citation checkable by a human — the whole attribution chain terminates
    here."""
    result = await db.execute(select(EvidenceRecord).where(EvidenceRecord.id == evidence_id))
    evidence = result.scalar_one_or_none()
    if evidence is None:
        raise HTTPException(status_code=404, detail="Evidence record not found.")
    return {
        "id": str(evidence.id),
        "evidence_type": evidence.evidence_type,
        "model_id": str(evidence.model_id),
        "source": evidence.source,
        "payload": evidence.payload,
        "artifact_uri": evidence.artifact_uri,
        "created_at": evidence.created_at.isoformat(),
    }


@router.get("/audit/verify")
async def verify_audit_chain(
    db: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(require_role("mrm_head", "auditor")),
):
    """Walk the full audit chain and recompute every hash.

    Honest limitation, also stated in services/audit.py: this detects
    application-level tampering. It does not detect an attacker with raw DB
    write access who recomputes the entire chain. Offsite anchoring of the
    chain head is the control for that, and is not implemented here.
    """
    return await audit.verify_chain(db)
