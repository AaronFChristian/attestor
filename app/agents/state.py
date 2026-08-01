"""
Validation graph state.

Two things worth knowing:

1. `proposed_findings` holds in-memory candidates only. They become real
   `Finding` rows in Postgres ONLY after `gate_findings` runs them through
   the attribution gate. Nothing in this state is evidence of anything —
   the database is.

2. After the interrupt, `finalize_report` deliberately does NOT read
   `proposed_findings` from graph state. It re-queries the `findings` table
   directly. This is what makes human edits made during the pause (accept/
   reject/amend via the REST endpoints) actually show up in the final
   report — the database is the source of truth across the pause, not the
   graph's in-memory state. Proven with stub nodes before this was written
   for real; see the interrupt/resume mechanics this depends on.
"""
import operator
from typing import Annotated, Literal, TypedDict


class ProposedFindingDict(TypedDict):
    """Plain-dict mirror of ProposedFinding (guardrails/attribution.py) —
    LangGraph state must be JSON-serializable for checkpointing, so we
    can't carry the dataclass directly."""

    pillar: str
    claim: str
    severity: str
    raised_by: str
    evidence_id: str  # str, not uuid.UUID — same serializability reason
    cited_metric_name: str | None
    cited_metric_value: float | None


class ValidationState(TypedDict):
    model_id: str
    validation_run_id: str
    materiality_tier: Literal["tier_1", "tier_2", "tier_3"]

    # Annotated with operator.add: proven necessary, not decorative. In a
    # tier_1 validation, conceptual_soundness, outcomes_analysis, and
    # ongoing_monitoring all run in parallel and each writes to this same
    # field in the same superstep. Without an explicit reducer, LangGraph
    # cannot merge concurrent writes and raises InvalidUpdateError —
    # confirmed by testing the unreduced version before this was added.
    proposed_findings: Annotated[list[ProposedFindingDict], operator.add]
    challenge_notes: list[str]

    # Populated by gate_findings — counts feed Attestor's own
    # outcomes-analysis evidence about itself (see module docstring in
    # guardrails/attribution.py on why the rejection rate is measured).
    accepted_finding_ids: list[str]
    rejected_count: int

    report_evidence_id: str | None
