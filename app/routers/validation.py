"""
Validation run lifecycle + human review queue.

Segregation of duties enforced here, not just documented: a validator
cannot start a validation run on a model they own (checked against
GovernedModel.owner_user_id), and sign-off is restricted to mrm_head, not
the validator who ran the review — an AI system cannot be the approver of
record, and neither can the same human wear both hats on one report.
"""
import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedUser, require_role
from app.core.database import get_db
from app.models.orm import Finding, FindingStatus, GovernedModel, User, ValidationRun
from app.services import audit

# Hard ceiling on a single graph run. This is the harness-level control
# that was missing entirely before: without it, a single hung LLM call
# blocks the HTTP request thread indefinitely — no cost ceiling, no
# recovery, just a stuck request. 5 minutes is generous for a synchronous
# tier-1 fan-out (3 parallel LLM calls plus challenge), which normally
# completes in well under a minute; it's sized to catch genuine hangs, not
# to be a tight production SLA.
GRAPH_INVOKE_TIMEOUT_SECONDS = 300

router = APIRouter(prefix="/validation-runs", tags=["validation"])


class StartValidationRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model_id: uuid.UUID


class FindingReviewRequest(BaseModel):
    rationale: str
    amended_claim: str | None = None  # required only for the amend action


async def _resolve_local_user(db: AsyncSession, user: AuthenticatedUser) -> User | None:
    result = await db.execute(select(User).where(User.keycloak_subject == user.subject))
    return result.scalar_one_or_none()


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def start_validation_run(
    payload: StartValidationRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(require_role("validator", "mrm_head")),
):
    """Kicks off the validation graph and runs it up to the interrupt.
    Returns once paused, not once complete — the pillar nodes and
    challenge review happen synchronously here since they're the fast
    part; the slow, human part happens after this returns."""
    result = await db.execute(select(GovernedModel).where(GovernedModel.id == payload.model_id))
    model = result.scalar_one_or_none()
    if model is None:
        raise HTTPException(status_code=404, detail="Model not found.")

    if str(model.owner_user_id) == user.subject:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot initiate a validation run on a model you own. "
            "This is a segregation-of-duties control, not a bug.",
        )

    local_user = await _resolve_local_user(db, user)
    if local_user is None:
        raise HTTPException(
            status_code=422,
            detail="No local user record for this identity. Run scripts/seed_data.py first.",
        )

    thread_id = str(uuid.uuid4())
    run = ValidationRun(
        id=uuid.uuid4(),
        model_id=model.id,
        initiated_by_user_id=local_user.id,
        langgraph_thread_id=thread_id,
        status="running",
    )
    db.add(run)
    await db.flush()

    await audit.record(
        db,
        actor=user.email,
        action="start_validation_run",
        resource_type="governed_model",
        resource_id=str(model.id),
        detail={"validation_run_id": str(run.id), "materiality_tier": model.materiality_tier.value},
    )
    await db.commit()

    graph = request.app.state.validation_graph
    config = {"configurable": {"thread_id": thread_id}}
    try:
        await asyncio.wait_for(
            graph.ainvoke(
                {
                    "model_id": str(model.id),
                    "validation_run_id": str(run.id),
                    "materiality_tier": model.materiality_tier.value,
                    "proposed_findings": [],
                    "challenge_notes": [],
                    "accepted_finding_ids": [],
                    "rejected_count": 0,
                    "report_evidence_id": None,
                },
                config=config,
            ),
            timeout=GRAPH_INVOKE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        run.status = "running"  # explicitly leave it stuck-but-visible, not silently "awaiting_review"
        await audit.record(
            db,
            actor="system",
            action="validation_run_timed_out",
            resource_type="validation_run",
            resource_id=str(run.id),
            detail={"timeout_seconds": GRAPH_INVOKE_TIMEOUT_SECONDS},
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Validation run exceeded {GRAPH_INVOKE_TIMEOUT_SECONDS}s and was aborted. "
            "Check the LLM provider status; this run's findings, if any were persisted "
            "before the hang, are still in the database.",
        )

    run.status = "awaiting_review"
    await db.commit()

    return {
        "validation_run_id": str(run.id),
        "status": "awaiting_review",
        "note": "Graph paused before report finalization. Review findings via "
        "GET /validation-runs/{id}/findings, then POST /validation-runs/{id}/finalize.",
    }


@router.get("/{run_id}")
async def get_validation_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(
        require_role("model_owner", "validator", "mrm_head", "auditor")
    ),
):
    """Single run's current status. This is what a UI polls to know
    whether it's still running, paused awaiting review, or signed off —
    there's no push/websocket layer, so polling this is the intended
    pattern for now."""
    result = await db.execute(select(ValidationRun).where(ValidationRun.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Validation run not found.")

    findings_result = await db.execute(select(Finding).where(Finding.validation_run_id == run_id))
    findings = list(findings_result.scalars().all())
    status_counts: dict[str, int] = {}
    for f in findings:
        status_counts[f.status.value] = status_counts.get(f.status.value, 0) + 1

    return {
        "id": str(run.id),
        "model_id": str(run.model_id),
        "status": run.status,
        "report_evidence_id": str(run.report_evidence_id) if run.report_evidence_id else None,
        "signed_off_by_user_id": str(run.signed_off_by_user_id) if run.signed_off_by_user_id else None,
        "signed_off_at": run.signed_off_at.isoformat() if run.signed_off_at else None,
        "created_at": run.created_at.isoformat(),
        "finding_counts": status_counts,
        "total_findings": len(findings),
    }


@router.get("/{run_id}/findings")
async def list_findings_for_review(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(
        require_role("model_owner", "validator", "mrm_head", "auditor")
    ),
):
    result = await db.execute(select(Finding).where(Finding.validation_run_id == run_id))
    findings = result.scalars().all()
    return [
        {
            "id": str(f.id),
            "pillar": f.pillar,
            "claim": f.claim,
            "severity": f.severity.value,
            "status": f.status.value,
            "raised_by": f.raised_by,
            "evidence_id": str(f.evidence_id),
        }
        for f in findings
    ]


async def _review_finding(
    db: AsyncSession,
    user: AuthenticatedUser,
    finding_id: uuid.UUID,
    new_status: FindingStatus,
    rationale: str,
) -> Finding:
    result = await db.execute(select(Finding).where(Finding.id == finding_id))
    finding = result.scalar_one_or_none()
    if finding is None:
        raise HTTPException(status_code=404, detail="Finding not found.")

    reviewer = await _resolve_local_user(db, user)

    finding.status = new_status
    finding.review_rationale = rationale
    finding.reviewed_by_user_id = reviewer.id if reviewer else None
    finding.reviewed_at = datetime.now(timezone.utc)

    await audit.record(
        db,
        actor=user.email,
        action=f"finding_{new_status.value}",
        resource_type="finding",
        resource_id=str(finding_id),
        detail={"rationale": rationale},
    )
    return finding


@router.post("/{run_id}/findings/{finding_id}/accept")
async def accept_finding(
    run_id: uuid.UUID,
    finding_id: uuid.UUID,
    payload: FindingReviewRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(require_role("validator", "mrm_head")),
):
    finding = await _review_finding(
        db, user, finding_id, FindingStatus.ACCEPTED, payload.rationale
    )
    await db.commit()
    return {"finding_id": str(finding.id), "status": finding.status.value}


@router.post("/{run_id}/findings/{finding_id}/reject")
async def reject_finding(
    run_id: uuid.UUID,
    finding_id: uuid.UUID,
    payload: FindingReviewRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(require_role("validator", "mrm_head")),
):
    """A rejection feeds Attestor's own golden set (label_source=
    'rejected_finding') — this is what turns the human override log into
    measurable evidence about the validation agent's own false-finding
    rate, per the self-governance design."""
    finding = await _review_finding(
        db, user, finding_id, FindingStatus.REJECTED, payload.rationale
    )

    from app.models.orm import GoldenDatasetExample

    db.add(
        GoldenDatasetExample(
            id=uuid.uuid4(),
            dataset_name="attestor_self_golden",
            dataset_version="v1",
            model_id=finding.model_id,
            input_payload={"claim": finding.claim, "pillar": finding.pillar},
            expected_output={"label": "rejected_finding", "rationale": payload.rationale},
            label_source="rejected_finding",
        )
    )
    await db.commit()
    return {"finding_id": str(finding.id), "status": finding.status.value}


@router.post("/{run_id}/findings/{finding_id}/amend")
async def amend_finding(
    run_id: uuid.UUID,
    finding_id: uuid.UUID,
    payload: FindingReviewRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(require_role("validator", "mrm_head")),
):
    if not payload.amended_claim:
        raise HTTPException(
            status_code=422, detail="amended_claim is required for an amend action."
        )
    result = await db.execute(select(Finding).where(Finding.id == finding_id))
    finding = result.scalar_one_or_none()
    if finding is None:
        raise HTTPException(status_code=404, detail="Finding not found.")
    finding.claim = payload.amended_claim

    finding = await _review_finding(
        db, user, finding_id, FindingStatus.AMENDED, payload.rationale
    )
    await db.commit()
    return {"finding_id": str(finding.id), "status": finding.status.value}


@router.post("/{run_id}/finalize")
async def finalize_validation_run(
    run_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(require_role("validator", "mrm_head")),
):
    """Resumes the paused graph. finalize_report_node re-reads findings
    from the database rather than trusting graph state, so whatever the
    validator accepted/amended by now is what the report contains."""
    result = await db.execute(select(ValidationRun).where(ValidationRun.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Validation run not found.")

    graph = request.app.state.validation_graph
    config = {"configurable": {"thread_id": run.langgraph_thread_id}}
    try:
        final_state = await asyncio.wait_for(
            graph.ainvoke(None, config=config), timeout=GRAPH_INVOKE_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Report finalization exceeded {GRAPH_INVOKE_TIMEOUT_SECONDS}s. "
            "The run remains paused and can be retried.",
        )

    run.report_evidence_id = uuid.UUID(final_state["report_evidence_id"])
    await db.commit()

    return {
        "validation_run_id": str(run_id),
        "report_evidence_id": final_state["report_evidence_id"],
        "note": "Report drafted. Sign-off still required: POST /validation-runs/{id}/sign-off.",
    }


@router.post("/{run_id}/sign-off")
async def sign_off_validation_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(require_role("mrm_head")),
):
    """mrm_head-only, deliberately not validator — the person who ran the
    review should not also be the one attesting to it. This is the human
    accountability anchor the whole system exists to produce."""
    result = await db.execute(select(ValidationRun).where(ValidationRun.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Validation run not found.")
    if run.report_evidence_id is None:
        raise HTTPException(
            status_code=409,
            detail="Cannot sign off before the report is finalized. "
            "Call POST /validation-runs/{id}/finalize first.",
        )

    signer = await _resolve_local_user(db, user)

    run.status = "signed_off"
    run.signed_off_by_user_id = signer.id if signer else None
    run.signed_off_at = datetime.now(timezone.utc)

    await audit.record(
        db,
        actor=user.email,
        action="sign_off_validation_run",
        resource_type="validation_run",
        resource_id=str(run_id),
        detail={"report_evidence_id": str(run.report_evidence_id)},
    )
    await db.commit()

    return {
        "validation_run_id": str(run_id),
        "status": "signed_off",
        "signed_off_by": user.email,
        "signed_off_at": run.signed_off_at.isoformat(),
    }
