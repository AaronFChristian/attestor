"""
Evidence record creation.

This module is the only permitted writer to `evidence_records`. There is
deliberately no update() and no delete() function here. Evidence is
append-only: an eval run that happened, happened, and a validation report
signed six months ago must still resolve to the exact numbers it cited.

If you find yourself wanting to "correct" an evidence record, the answer is
to write a new one and supersede it, not to edit history. That's the whole
point of the design.
"""
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orm import EvidenceRecord


async def record_evidence(
    db: AsyncSession,
    model_id: uuid.UUID,
    evidence_type: str,
    source: str,
    payload: dict[str, Any],
    artifact_uri: str | None = None,
) -> EvidenceRecord:
    """Append one evidence record.

    Caller commits. This lets a single transaction write the evidence AND
    whatever depends on it (an EvalRun row, an audit entry) atomically —
    so you can never end up with an eval run pointing at evidence that
    doesn't exist.
    """
    if evidence_type not in {
        "eval_run",
        "trace_sample",
        "red_team_result",
        "document",
        "drift_alert",
        "validation_report",
        "judge_agreement",
    }:
        raise ValueError(
            f"Unknown evidence_type '{evidence_type}'. Add it to the allowed set "
            "explicitly rather than passing a free string — evidence types are "
            "queried and reported on, so typos become silent data gaps."
        )

    evidence = EvidenceRecord(
        id=uuid.uuid4(),
        model_id=model_id,
        evidence_type=evidence_type,
        source=source,
        payload=payload,
        artifact_uri=artifact_uri,
    )
    db.add(evidence)
    await db.flush()  # populates evidence.id for the caller to reference
    return evidence
