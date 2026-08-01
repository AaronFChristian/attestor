"""
The attribution gate.

This is the single most important piece of code in Attestor. It is what
separates "a governance tool" from "a chatbot that writes plausible
compliance text."

The rule: a Finding cannot be persisted unless its evidence_id resolves to
a real EvidenceRecord, AND any metric value it cites matches what that
evidence record actually contains.

Note carefully that this is a DETERMINISTIC check, not an LLM check. We do
not ask a model "is this finding grounded?" — we look up the row and
compare the numbers. An LLM verifying an LLM is a weaker control and an
examiner will say so. Database lookups don't hallucinate.

Every rejection is logged as a rail-trip event with the reason. Those
rejections are data: the rate at which the validation agent produces
ungrounded findings IS Attestor's own outcomes-analysis metric, which is
what makes the self-governance story real rather than decorative.
"""
import math
import uuid
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orm import EvidenceRecord

# Tolerance for float comparison when a finding cites a metric value.
# Findings quote rounded numbers ("faithfulness fell to 0.83"); the stored
# value may be 0.8299999. This is not a license to be sloppy — it's two
# decimal places, matching how metrics are actually reported.
METRIC_TOLERANCE = 0.005


class RailTripReason(str, Enum):
    EVIDENCE_NOT_FOUND = "evidence_not_found"
    EVIDENCE_WRONG_MODEL = "evidence_belongs_to_different_model"
    METRIC_MISMATCH = "cited_metric_does_not_match_evidence"
    METRIC_NOT_IN_EVIDENCE = "cited_metric_absent_from_evidence"
    EMPTY_CLAIM = "claim_is_empty"


@dataclass
class AttributionResult:
    passed: bool
    reason: RailTripReason | None = None
    detail: str = ""

    def as_audit_detail(self) -> dict:
        return {
            "passed": self.passed,
            "reason": self.reason.value if self.reason else None,
            "detail": self.detail,
        }


@dataclass
class ProposedFinding:
    """What an agent hands to the gate. Deliberately NOT the ORM model —
    an agent should never hold a database object it could accidentally
    persist. It proposes; the gate decides."""

    model_id: uuid.UUID
    evidence_id: uuid.UUID
    pillar: str
    claim: str
    severity: str
    raised_by: str
    # Optional: if the finding cites a specific metric, name and value go
    # here so the gate can verify them against the evidence payload.
    cited_metric_name: str | None = None
    cited_metric_value: float | None = None


async def verify_attribution(
    db: AsyncSession, proposed: ProposedFinding
) -> AttributionResult:
    """Resolve and verify. Returns a result rather than raising, because a
    failed attribution is an expected, measurable event — not an error
    condition. We want to count these, not crash on them."""

    if not proposed.claim or not proposed.claim.strip():
        return AttributionResult(
            passed=False,
            reason=RailTripReason.EMPTY_CLAIM,
            detail="Finding has no claim text.",
        )

    result = await db.execute(
        select(EvidenceRecord).where(EvidenceRecord.id == proposed.evidence_id)
    )
    evidence = result.scalar_one_or_none()

    if evidence is None:
        return AttributionResult(
            passed=False,
            reason=RailTripReason.EVIDENCE_NOT_FOUND,
            detail=(
                f"evidence_id {proposed.evidence_id} does not exist. The agent "
                "referenced evidence that was never recorded — this is the "
                "signature of a fabricated citation."
            ),
        )

    if evidence.model_id != proposed.model_id:
        return AttributionResult(
            passed=False,
            reason=RailTripReason.EVIDENCE_WRONG_MODEL,
            detail=(
                f"Evidence {proposed.evidence_id} belongs to model "
                f"{evidence.model_id}, but the finding is against model "
                f"{proposed.model_id}. Cross-model citation is not valid grounding."
            ),
        )

    # If no metric was cited, existence + ownership of the evidence is
    # sufficient grounding for a qualitative finding.
    if proposed.cited_metric_name is None:
        return AttributionResult(passed=True, detail="Evidence resolved; no metric cited.")

    metrics = (evidence.payload or {}).get("metrics", {})
    if proposed.cited_metric_name not in metrics:
        return AttributionResult(
            passed=False,
            reason=RailTripReason.METRIC_NOT_IN_EVIDENCE,
            detail=(
                f"Finding cites metric '{proposed.cited_metric_name}' but evidence "
                f"{proposed.evidence_id} contains only {sorted(metrics.keys())}."
            ),
        )

    actual = metrics[proposed.cited_metric_name]
    cited = proposed.cited_metric_value

    if cited is None:
        return AttributionResult(
            passed=True,
            detail=f"Metric '{proposed.cited_metric_name}' referenced by name only.",
        )

    try:
        actual_f = float(actual)
    except (TypeError, ValueError):
        return AttributionResult(
            passed=False,
            reason=RailTripReason.METRIC_MISMATCH,
            detail=f"Stored metric '{proposed.cited_metric_name}' is non-numeric: {actual!r}.",
        )

    if not math.isclose(actual_f, cited, abs_tol=METRIC_TOLERANCE):
        return AttributionResult(
            passed=False,
            reason=RailTripReason.METRIC_MISMATCH,
            detail=(
                f"Finding claims {proposed.cited_metric_name}={cited}, but evidence "
                f"{proposed.evidence_id} records {actual_f}. The agent misreported a "
                "number that exists — this is exactly the class of error that makes "
                "a validation report indefensible."
            ),
        )

    return AttributionResult(
        passed=True,
        detail=f"Verified {proposed.cited_metric_name}={actual_f} against evidence.",
    )
