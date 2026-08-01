"""
FastMCP server — read-only risk-posture tools.

Every tool here is a query. There is no write path exposed through MCP,
deliberately: an MCP client (Claude Desktop, another agent) asking "what's
our risk posture" should never be able to accidentally register a model,
approve a finding, or sign off a report through a conversational
side-channel. Governance actions stay behind the RBAC-checked REST API.
"""
from fastmcp import FastMCP
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.orm import EvalRun, Finding, FindingStatus, GovernedModel

mcp = FastMCP("attestor-risk-posture")


@mcp.tool()
async def list_models() -> list[dict]:
    """List every governed model with its current materiality tier and status."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(GovernedModel))
        return [
            {
                "id": str(m.id),
                "name": m.name,
                "materiality_tier": m.materiality_tier.value,
                "status": m.status.value,
                "owner_team": m.owner_team,
            }
            for m in result.scalars().all()
        ]


@mcp.tool()
async def get_risk_posture(model_name: str) -> dict:
    """Summarize a model's current risk posture: tier, open findings by
    severity, and its most recent eval metrics."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(GovernedModel).where(GovernedModel.name == model_name))
        model = result.scalar_one_or_none()
        if model is None:
            return {"error": f"No governed model named '{model_name}'."}

        findings_result = await db.execute(
            select(Finding).where(
                Finding.model_id == model.id,
                Finding.status == FindingStatus.PROPOSED,
            )
        )
        open_findings = list(findings_result.scalars().all())
        severity_counts: dict[str, int] = {}
        for f in open_findings:
            severity_counts[f.severity.value] = severity_counts.get(f.severity.value, 0) + 1

        eval_result = await db.execute(
            select(EvalRun)
            .where(EvalRun.model_id == model.id)
            .order_by(EvalRun.created_at.desc())
            .limit(1)
        )
        latest_eval = eval_result.scalar_one_or_none()

        return {
            "model_name": model.name,
            "materiality_tier": model.materiality_tier.value,
            "status": model.status.value,
            "open_findings_by_severity": severity_counts,
            "total_open_findings": len(open_findings),
            "latest_eval_metrics": latest_eval.metrics if latest_eval else None,
        }


@mcp.tool()
async def get_open_findings(model_name: str | None = None) -> list[dict]:
    """List findings still awaiting human review (status='proposed').
    Pass model_name to filter to one model, or omit for the full queue."""
    async with AsyncSessionLocal() as db:
        query = select(Finding).where(Finding.status == FindingStatus.PROPOSED)
        if model_name:
            model_result = await db.execute(
                select(GovernedModel).where(GovernedModel.name == model_name)
            )
            model = model_result.scalar_one_or_none()
            if model is None:
                return []
            query = query.where(Finding.model_id == model.id)

        result = await db.execute(query)
        return [
            {
                "id": str(f.id),
                "pillar": f.pillar,
                "claim": f.claim,
                "severity": f.severity.value,
                "raised_by": f.raised_by,
            }
            for f in result.scalars().all()
        ]


@mcp.tool()
async def get_eval_history(model_name: str, limit: int = 5) -> list[dict]:
    """Recent eval run metrics for a model, most recent first."""
    async with AsyncSessionLocal() as db:
        model_result = await db.execute(
            select(GovernedModel).where(GovernedModel.name == model_name)
        )
        model = model_result.scalar_one_or_none()
        if model is None:
            return []

        result = await db.execute(
            select(EvalRun)
            .where(EvalRun.model_id == model.id)
            .order_by(EvalRun.created_at.desc())
            .limit(limit)
        )
        return [
            {
                "eval_run_id": str(r.id),
                "dataset_version": r.dataset_version,
                "metrics": r.metrics,
                "created_at": r.created_at.isoformat(),
            }
            for r in result.scalars().all()
        ]


if __name__ == "__main__":
    mcp.run()
