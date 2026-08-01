"""
Validation graph construction.

Node sequence:
  supervisor (routes by materiality tier)
    -> [conceptual_soundness, outcomes_analysis, ongoing_monitoring]  (subset per tier)
    -> challenge
    -> gate_findings  (writes real Finding rows; only write-access node)
    -> INTERRUPT  <-- graph pauses here
    -> finalize_report  (only runs on resume)
    -> END

The interrupt sits between gate_findings and finalize_report deliberately:
findings are already real, persisted, human-reviewable rows by the time the
pause happens. A validator works through the review queue (accept/reject/
amend via the REST endpoints in routers/validation.py) WHILE the graph is
paused. Resuming does not re-read graph state for findings — it queries
findings fresh from the database, so whatever the human changed is what
gets reported. This was proven structurally with stub nodes before being
built for real.

Checkpointer: MemorySaver, not Postgres-backed. Documented, deliberate
scope limit — a paused validation run will NOT survive an API process
restart with this checkpointer. The upgrade path is swapping in
langgraph-checkpoint-postgres (already compatible with this graph
structure, zero node changes needed) — not implemented here because it's
a genuine infrastructure addition (its own migration, connection pool)
that isn't justified until this is closer to real deployment. Say this
limitation out loud rather than let someone discover it by restarting the
API mid-review.
"""
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from app.agents.nodes import (
    challenge_node,
    conceptual_soundness_node,
    gate_findings,
    ongoing_monitoring_node,
    outcomes_analysis_node,
)
from app.agents.state import ValidationState


def _tier_pillars(state: ValidationState) -> list[str]:
    """Materiality tier determines which pillars run — mirrors the business
    rule stated in services/materiality.py's tier rationale text, kept in
    sync manually since LangGraph conditional routing needs a plain
    function, not a shared constant import from a Pydantic module."""
    tier = state["materiality_tier"]
    if tier == "tier_1":
        return ["conceptual_soundness", "outcomes_analysis", "ongoing_monitoring"]
    if tier == "tier_2":
        return ["conceptual_soundness", "ongoing_monitoring"]
    return ["ongoing_monitoring"]  # tier_3


async def supervisor_node(state: ValidationState) -> dict:
    """Named pass-through so the tier-routing decision is visible in the
    graph topology (and in LangSmith traces) rather than buried inside
    conditional-edge logic with no node of its own.

    Returns {"materiality_tier": ...} rather than {} deliberately: this
    pinned LangGraph version raises InvalidUpdateError on a node that
    writes to none of the declared state keys — confirmed by testing an
    empty-dict return before this was written this way. Writing back the
    tier unchanged satisfies that requirement while remaining a semantic
    no-op."""
    return {"materiality_tier": state["materiality_tier"]}


def _route_from_supervisor(state: ValidationState) -> list[str]:
    return _tier_pillars(state)


async def finalize_report_node(state: ValidationState) -> dict:
    """Runs only on resume, after the human review pause. Re-queries
    findings from the database rather than trusting graph state — this is
    what makes accept/reject/amend actions taken during the pause actually
    affect the report."""
    import uuid

    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.orm import Finding, FindingStatus
    from app.services import audit, evidence

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Finding).where(
                Finding.validation_run_id == uuid.UUID(state["validation_run_id"]),
                Finding.status.in_([FindingStatus.ACCEPTED, FindingStatus.AMENDED]),
            )
        )
        reviewed_findings = list(result.scalars().all())

        report_lines = [
            f"# Validation Report — Model {state['model_id']}",
            f"Materiality tier: {state['materiality_tier']}",
            f"Findings reviewed and accepted: {len(reviewed_findings)}",
            "",
        ]
        for f in reviewed_findings:
            report_lines.append(f"## [{f.pillar}] {f.severity.value.upper()}")
            report_lines.append(f.claim)
            report_lines.append(f"Evidence: {f.evidence_id}")
            report_lines.append("")

        if state["challenge_notes"]:
            report_lines.append("## Independent Challenge Notes")
            report_lines.extend(f"- {note}" for note in state["challenge_notes"])

        report_text = "\n".join(report_lines)

        report_evidence = await evidence.record_evidence(
            db,
            model_id=uuid.UUID(state["model_id"]),
            evidence_type="validation_report",
            source="validation_graph",
            payload={"report_text": report_text, "finding_count": len(reviewed_findings)},
        )

        await audit.record(
            db,
            actor="validation_graph",
            action="report_finalized",
            resource_type="validation_run",
            resource_id=state["validation_run_id"],
            detail={"report_evidence_id": str(report_evidence.id)},
        )
        await db.commit()

    return {"report_evidence_id": str(report_evidence.id)}


def build_validation_graph():
    graph = StateGraph(ValidationState)

    graph.add_node("supervisor", supervisor_node)
    graph.add_node("conceptual_soundness", conceptual_soundness_node)
    graph.add_node("outcomes_analysis", outcomes_analysis_node)
    graph.add_node("ongoing_monitoring", ongoing_monitoring_node)
    graph.add_node("challenge", challenge_node)
    graph.add_node("gate_findings", gate_findings)
    graph.add_node("finalize_report", finalize_report_node)

    graph.set_entry_point("supervisor")

    graph.add_conditional_edges(
        "supervisor",
        _route_from_supervisor,
        {
            "conceptual_soundness": "conceptual_soundness",
            "outcomes_analysis": "outcomes_analysis",
            "ongoing_monitoring": "ongoing_monitoring",
        },
    )
    graph.add_edge("conceptual_soundness", "challenge")
    graph.add_edge("outcomes_analysis", "challenge")
    graph.add_edge("ongoing_monitoring", "challenge")

    graph.add_edge("challenge", "gate_findings")
    graph.add_edge("gate_findings", "finalize_report")
    graph.add_edge("finalize_report", END)

    checkpointer = MemorySaver()
    return graph.compile(checkpointer=checkpointer, interrupt_before=["finalize_report"])


_compiled_graph = None


def get_validation_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_validation_graph()
    return _compiled_graph
