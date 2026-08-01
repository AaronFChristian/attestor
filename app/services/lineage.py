"""
Blast-radius lineage.

When a shared component (e.g. a retriever service, an eval dataset) fails
an eval, every model that depends on it — directly or transitively — is
now running on unverified footing. This walks the dependency graph with a
recursive CTE rather than a graph database. That's a deliberate tradeoff:
Postgres closure-table recursion handles the depth this system actually
has, and it avoids a second stateful container (Neo4j) for a job that
doesn't need one at this scale. Say that tradeoff out loud if asked why
there's no graph DB here — it's a choice, not an oversight.
"""
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# depends_on edges point FROM a model TO what it depends on. Blast radius
# walks the graph BACKWARDS from a failing model: find everything that
# depends on it (directly or transitively), not what it depends on.
_BLAST_RADIUS_SQL = text(
    """
    WITH RECURSIVE affected AS (
        SELECT model_id AS affected_model_id, 1 AS depth
        FROM model_dependencies
        WHERE depends_on_model_id = :root_model_id

        UNION

        SELECT md.model_id, a.depth + 1
        FROM model_dependencies md
        JOIN affected a ON md.depends_on_model_id = a.affected_model_id
        WHERE a.depth < 10  -- guard against a cycle turning this into an infinite loop
    )
    SELECT DISTINCT gm.id, gm.name, gm.materiality_tier, a.depth
    FROM affected a
    JOIN governed_models gm ON gm.id = a.affected_model_id
    ORDER BY a.depth, gm.name
    """
)


async def compute_blast_radius(db: AsyncSession, root_model_id: uuid.UUID) -> list[dict]:
    """Every model that transitively depends on root_model_id, with depth.
    Empty list means nothing depends on this model — a real, common, and
    correctly-unremarkable result, not an error condition."""
    result = await db.execute(_BLAST_RADIUS_SQL, {"root_model_id": root_model_id})
    return [
        {
            "model_id": str(row.id),
            "name": row.name,
            "materiality_tier": row.materiality_tier,
            "depth": row.depth,
        }
        for row in result.fetchall()
    ]


async def open_provisional_findings_for_blast_radius(
    db: AsyncSession,
    root_model_id: uuid.UUID,
    root_evidence_id: uuid.UUID,
    reason: str,
) -> list[dict]:
    """When a shared component fails, auto-open a LOW-severity provisional
    finding on every downstream model, citing the SAME evidence that
    triggered it. 'Provisional' matters: these are flagged for a human to
    triage, not asserted as confirmed defects in a model that was never
    directly tested — the finding text says so explicitly."""
    import uuid as uuid_mod

    from app.models.orm import Finding, FindingSeverity
    from app.services import audit

    affected = await compute_blast_radius(db, root_model_id)
    opened: list[dict] = []

    for entry in affected:
        finding = Finding(
            id=uuid_mod.uuid4(),
            model_id=uuid_mod.UUID(entry["model_id"]),
            evidence_id=root_evidence_id,
            pillar="ongoing_monitoring",
            claim=(
                f"PROVISIONAL — not directly tested. A dependency of this model "
                f"({reason}) failed evaluation. This model depends on the failing "
                f"component at lineage depth {entry['depth']} and should be "
                f"reviewed for blast-radius impact before its next scheduled validation."
            ),
            severity=FindingSeverity.LOW,
            raised_by="lineage_blast_radius",
        )
        db.add(finding)
        await db.flush()
        opened.append({"model_id": entry["model_id"], "finding_id": str(finding.id)})

    if opened:
        await audit.record(
            db,
            actor="system",
            action="blast_radius_provisional_findings_opened",
            resource_type="governed_model",
            resource_id=str(root_model_id),
            detail={"reason": reason, "opened": opened},
        )

    return opened
