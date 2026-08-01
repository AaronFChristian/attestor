"""
Validation graph nodes.

Each pillar node's job is narrow: look at real evidence, propose findings
that cite it. None of these nodes write to the database directly — they
only add to `proposed_findings` in graph state. `gate_findings` is the
only node with write access to the `findings` table, and it only writes
what survives `verify_attribution`. That separation is deliberate: an LLM
node proposing a finding and an LLM node persisting a finding should never
be the same code path, or the attribution gate becomes decorative.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.agents.state import ProposedFindingDict, ValidationState
from app.core.database import AsyncSessionLocal
from app.evals.runner import find_cached_run
from app.gateway.llm import get_gateway
from app.guardrails.attribution import ProposedFinding, verify_attribution
from app.guardrails.injection_screen import screen_text
from app.models.orm import EvalRun, EvidenceRecord, Finding, FindingSeverity
from app.services import audit, evidence
from app.services.drift import detect_metric_drift

# Thresholds a finding gets raised against. In a real deployment these
# would be per-model, set by the MRM head — hardcoded here with that
# limitation stated plainly rather than implying they're configurable
# when they aren't yet.
OUTCOMES_METRIC_THRESHOLDS = {
    "tool_correctness": 0.80,
    "schema_conformance": 0.95,
    "citation_resolvability": 1.00,
    "rubric_mean": 0.70,
}

_CONCEPTUAL_SOUNDNESS_PROMPT = """You are validating the conceptual soundness of an \
AI/ML model under SR 26-2. Given the model's card and any design documentation, \
identify at most 2 specific, material gaps in documented rationale, design \
justification, or known limitations. Do not invent generic best-practice advice — \
only flag what's actually absent or unjustified in the material provided.

Respond with ONLY a JSON array, no preamble:
[{"claim": "<specific gap, one or two sentences>", "severity": "low|medium|high|critical"}]

Return an empty array [] if the documentation is genuinely adequate."""


async def conceptual_soundness_node(state: ValidationState) -> dict:
    """Reads the model card + any ingested design docs, asks the judge model
    to identify specific documented gaps. Every claim gets attached to the
    model_card evidence record it was read from — the gate will reject any
    claim that can't resolve back to that."""
    gateway = get_gateway()
    proposed: list[ProposedFindingDict] = []

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(EvidenceRecord)
            .where(
                EvidenceRecord.model_id == uuid.UUID(state["model_id"]),
                EvidenceRecord.evidence_type == "document",
            )
            .order_by(EvidenceRecord.created_at.desc())
            .limit(1)
        )
        doc_evidence = result.scalar_one_or_none()

        if doc_evidence is None:
            # No design doc ingested for this model. This is itself a
            # conceptual-soundness gap, and it's evidence-free by
            # construction — there's nothing to cite, so we record it as a
            # qualitative finding against... nothing resolvable. Correctly,
            # the gate will need SOME evidence_id. We create one: a small
            # evidence record noting the absence, so the finding has a real
            # citation rather than being silently dropped.
            absence_evidence = await evidence.record_evidence(
                db,
                model_id=uuid.UUID(state["model_id"]),
                evidence_type="document",
                source="conceptual_soundness_node",
                payload={"note": "No design documentation was found for this model."},
            )
            await db.commit()
            proposed.append(
                {
                    "pillar": "conceptual_soundness",
                    "claim": "No design documentation has been ingested for this model. "
                    "Conceptual soundness cannot be assessed against undocumented design decisions.",
                    "severity": "high",
                    "raised_by": "conceptual_soundness_node",
                    "evidence_id": str(absence_evidence.id),
                    "cited_metric_name": None,
                    "cited_metric_value": None,
                }
            )
            return {"proposed_findings": proposed}

        doc_text = str(doc_evidence.payload)[:4000]

        # Injection screen: doc_text is arbitrary ingested content, and
        # this is the exact place it reaches an LLM prompt. Flagging
        # rather than stripping — see module docstring on
        # app/guardrails/injection_screen.py for why silent stripping is
        # the wrong call here. A flag doesn't block the call; it makes the
        # attempt visible in the audit trail regardless of what the model
        # does with it, and the attribution gate is still the real
        # backstop against anything that makes it into a Finding.
        screen_result = screen_text(doc_text)
        if screen_result.flagged:
            await audit.record(
                db,
                actor="conceptual_soundness_node",
                action="injection_pattern_flagged_in_ingested_document",
                resource_type="evidence_record",
                resource_id=str(doc_evidence.id),
                detail={"matches": screen_result.matches},
            )
            await db.commit()

        response = gateway.complete(
            task_class="judgment",
            system=_CONCEPTUAL_SOUNDNESS_PROMPT,
            user=doc_text,
        )

    import json

    try:
        gaps = json.loads(response.text)
    except (json.JSONDecodeError, ValueError):
        gaps = []

    for gap in gaps[:2]:
        proposed.append(
            {
                "pillar": "conceptual_soundness",
                "claim": str(gap.get("claim", ""))[:1000],
                "severity": gap.get("severity", "medium"),
                "raised_by": "conceptual_soundness_node",
                "evidence_id": str(doc_evidence.id),
                "cited_metric_name": None,
                "cited_metric_value": None,
            }
        )

    return {"proposed_findings": proposed}


async def outcomes_analysis_node(state: ValidationState) -> dict:
    """Deterministic, not LLM-driven: pulls the latest eval run and compares
    against fixed thresholds. This node cannot hallucinate a metric value —
    it reads exactly what's in the eval_runs table."""
    proposed: list[ProposedFindingDict] = []

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(EvalRun)
            .where(EvalRun.model_id == uuid.UUID(state["model_id"]))
            .order_by(EvalRun.created_at.desc())
            .limit(1)
        )
        latest_run = result.scalar_one_or_none()

        if latest_run is None:
            # No eval run exists for this model — a genuine absence, not a
            # metric failure. There is no real evidence to cite for "this
            # doesn't exist," and evidence_id is NOT NULL on Finding by
            # design, so this does not become a Finding at all. The
            # absence is visible via validation_run metadata instead of
            # being forced into a fabricated citation.
            return {"proposed_findings": []}

        for metric_name, threshold in OUTCOMES_METRIC_THRESHOLDS.items():
            actual = latest_run.metrics.get(metric_name)
            if actual is None:
                continue
            if actual < threshold:
                proposed.append(
                    {
                        "pillar": "outcomes_analysis",
                        "claim": f"{metric_name} scored {actual}, below the required "
                        f"minimum of {threshold} on the most recent eval run.",
                        "severity": "high" if actual < threshold * 0.7 else "medium",
                        "raised_by": "outcomes_analysis_node",
                        "evidence_id": str(latest_run.evidence_id),
                        "cited_metric_name": metric_name,
                        "cited_metric_value": float(actual),
                    }
                )

    return {"proposed_findings": proposed}


async def ongoing_monitoring_node(state: ValidationState) -> dict:
    """Wraps Day 2's drift detection. A breached drift signal gets recorded
    as its OWN evidence record before being cited — a derived signal isn't
    directly a row in eval_runs, so it needs its own citable evidence."""
    proposed: list[ProposedFindingDict] = []

    async with AsyncSessionLocal() as db:
        signal = await detect_metric_drift(db, uuid.UUID(state["model_id"]), "rubric_mean")

        if signal is not None and signal.breached:
            drift_evidence = await evidence.record_evidence(
                db,
                model_id=uuid.UUID(state["model_id"]),
                evidence_type="drift_alert",
                source="ongoing_monitoring_node",
                payload=signal.to_payload(),
            )
            await db.commit()

            proposed.append(
                {
                    "pillar": "ongoing_monitoring",
                    "claim": signal.detail,
                    "severity": "medium",
                    "raised_by": "ongoing_monitoring_node",
                    "evidence_id": str(drift_evidence.id),
                    "cited_metric_name": signal.metric_name,
                    "cited_metric_value": signal.current_value,
                }
            )

    return {"proposed_findings": proposed}


_CHALLENGE_PROMPT = """You are the independent challenge reviewer. Your only job is \
to attack the findings below and identify which ones are weakly supported, likely \
confounded by an unrelated cause, or overstated relative to their evidence.

Findings:
{findings}

Respond with ONLY a JSON array of challenge notes, one per finding you have a \
concern about (skip findings you have no concern about):
[{{"claim_excerpt": "<first few words of the finding>", "concern": "<your specific objection>"}}]"""


async def challenge_node(state: ValidationState) -> dict:
    """Adversarial review of everything proposed so far. Does NOT remove
    findings — the attribution gate is the only thing with authority to
    drop a finding, and it drops on grounding, not on judgment. Challenge
    notes are attached to the report so a human sees the disagreement,
    which is more honest than letting the challenge agent silently prune."""
    if not state["proposed_findings"]:
        return {"challenge_notes": []}

    gateway = get_gateway()
    findings_text = "\n".join(
        f"- [{f['pillar']}] {f['claim']}" for f in state["proposed_findings"]
    )
    response = gateway.complete(
        task_class="challenge",
        system=_CHALLENGE_PROMPT.format(findings=findings_text),
        user="Review the findings above.",
    )

    import json

    try:
        notes = json.loads(response.text)
        challenge_notes = [
            f"{n.get('claim_excerpt', '?')}: {n.get('concern', '')}" for n in notes
        ]
    except (json.JSONDecodeError, ValueError):
        challenge_notes = []

    return {"challenge_notes": challenge_notes}


async def gate_findings(state: ValidationState) -> dict:
    """The only node permitted to write to the findings table. Every
    proposed finding passes through verify_attribution; only passing ones
    become real rows. Rejections are counted and audited — that rejection
    rate is Attestor's own outcomes-analysis evidence about itself."""
    accepted_ids: list[str] = []
    rejected_count = 0

    async with AsyncSessionLocal() as db:
        for pf in state["proposed_findings"]:
            proposed = ProposedFinding(
                model_id=uuid.UUID(state["model_id"]),
                evidence_id=uuid.UUID(pf["evidence_id"]),
                pillar=pf["pillar"],
                claim=pf["claim"],
                severity=pf["severity"],
                raised_by=pf["raised_by"],
                cited_metric_name=pf.get("cited_metric_name"),
                cited_metric_value=pf.get("cited_metric_value"),
            )
            result = await verify_attribution(db, proposed)

            if not result.passed:
                rejected_count += 1
                await audit.record(
                    db,
                    actor=pf["raised_by"],
                    action="finding_rejected_by_attribution_gate",
                    resource_type="governed_model",
                    resource_id=state["model_id"],
                    detail=result.as_audit_detail(),
                )
                continue

            finding = Finding(
                id=uuid.uuid4(),
                model_id=uuid.UUID(state["model_id"]),
                validation_run_id=uuid.UUID(state["validation_run_id"]),
                evidence_id=uuid.UUID(pf["evidence_id"]),
                pillar=pf["pillar"],
                claim=pf["claim"],
                severity=FindingSeverity(pf["severity"]),
                raised_by=pf["raised_by"],
            )
            db.add(finding)
            await db.flush()
            accepted_ids.append(str(finding.id))

        await audit.record(
            db,
            actor="validation_graph",
            action="gate_findings_completed",
            resource_type="validation_run",
            resource_id=state["validation_run_id"],
            detail={"accepted": len(accepted_ids), "rejected": rejected_count},
        )
        await db.commit()

    return {"accepted_finding_ids": accepted_ids, "rejected_count": rejected_count}
