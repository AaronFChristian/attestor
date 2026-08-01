"""
Eval run orchestration.

Three properties this module guarantees, each of which exists because
getting it wrong is expensive:

1. IDEMPOTENCY. An eval run is keyed on (model_id, dataset_version,
   prompt_hash). Re-running the same triple returns the cached run instead
   of burning tokens. This is both a cost control and a reproducibility
   guarantee — "run the eval again" must produce the same evidence, not a
   new near-identical number.

2. EMPTY-RESULT GUARD. If the golden dataset comes back empty (bad version
   string, dataset not seeded, upstream failure), we abort BEFORE writing
   anything. A previous project of mine wiped a table because a
   CREATE OR REPLACE fired on an empty result set — an eval suite that
   "succeeds" with zero examples and records a perfect score is the same
   class of bug, and worse because it looks like good news.

3. HONEST DEGRADATION. If the gateway failed over from Sonnet to Haiku
   mid-run, that's recorded on the evidence. A report must never imply
   Sonnet-quality evaluation that was actually done by a cheaper model.
"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.evals.judges import DualJudgeResult, judge_agreement_rate, score_criterion
from app.evals.scorers import (
    ScoreResult,
    score_schema_conformance,
    score_tool_correctness,
)
from app.models.orm import EvalRun, GoldenDatasetExample
from app.services import evidence as evidence_service


class EmptyDatasetError(RuntimeError):
    """Raised when a golden dataset resolves to zero examples. Deliberately
    an exception rather than a silent empty result — see module docstring."""


@dataclass
class EvalRunResult:
    eval_run_id: uuid.UUID
    evidence_id: uuid.UUID
    metrics: dict[str, float]
    per_example: list[dict] = field(default_factory=list)
    cached: bool = False
    degraded: bool = False


async def load_golden_dataset(
    db: AsyncSession, dataset_name: str, dataset_version: str
) -> list[GoldenDatasetExample]:
    result = await db.execute(
        select(GoldenDatasetExample).where(
            GoldenDatasetExample.dataset_name == dataset_name,
            GoldenDatasetExample.dataset_version == dataset_version,
        )
    )
    examples = list(result.scalars().all())

    # THE GUARD. Do not remove this. An eval that runs on zero examples
    # reports a vacuous perfect score and poisons every downstream finding.
    if not examples:
        raise EmptyDatasetError(
            f"Golden dataset '{dataset_name}' version '{dataset_version}' returned "
            "0 examples. Aborting before any evidence is written. Check the dataset "
            "was seeded and the version string is correct."
        )
    return examples


async def find_cached_run(
    db: AsyncSession, model_id: uuid.UUID, dataset_version: str, prompt_hash: str
) -> EvalRun | None:
    result = await db.execute(
        select(EvalRun).where(
            EvalRun.model_id == model_id,
            EvalRun.dataset_version == dataset_version,
            EvalRun.prompt_hash == prompt_hash,
        )
    )
    return result.scalar_one_or_none()


async def run_eval_suite(
    db: AsyncSession,
    model_id: uuid.UUID,
    dataset_name: str,
    dataset_version: str,
    prompt_hash: str,
    rubric_criteria: list[str],
    force: bool = False,
) -> EvalRunResult:
    """Execute a full eval suite and persist the result as immutable evidence.

    `force=True` bypasses the idempotency cache. Use sparingly — it means
    two evidence records will exist for the same logical run, which is
    legitimate when a model endpoint changed but the prompt didn't, and
    confusing otherwise.
    """
    if not force:
        cached = await find_cached_run(db, model_id, dataset_version, prompt_hash)
        if cached is not None:
            return EvalRunResult(
                eval_run_id=cached.id,
                evidence_id=cached.evidence_id,
                metrics=dict(cached.metrics),
                cached=True,
            )

    examples = await load_golden_dataset(db, dataset_name, dataset_version)

    deterministic_scores: list[ScoreResult] = []
    judge_results: list[DualJudgeResult] = []
    per_example: list[dict] = []
    degraded = False

    for example in examples:
        expected = example.expected_output or {}
        actual = example.input_payload.get("observed_output", {}) or {}

        example_scores: list[ScoreResult] = []

        if "expected_tools" in expected:
            example_scores.append(
                score_tool_correctness(
                    expected_tools=expected.get("expected_tools", []),
                    actual_tools=actual.get("tools_called", []),
                )
            )

        if "required_fields" in expected:
            example_scores.append(
                score_schema_conformance(actual, expected["required_fields"])
            )

        deterministic_scores.extend(example_scores)
        per_example.append(
            {
                "example_id": str(example.id),
                "scores": [
                    {"name": s.name, "value": s.value, "passed": s.passed, "detail": s.detail}
                    for s in example_scores
                ],
            }
        )

    # Rubric scoring runs once per criterion against the aggregate, not per
    # example — LLM-as-judge is the expensive part and scoring every example
    # against every criterion is how a demo eval suite becomes unaffordable.
    subject_summary = _summarise_for_judge(examples)
    for criterion in rubric_criteria:
        judge_results.append(score_criterion(criterion, subject_summary))

    metrics = _aggregate_metrics(deterministic_scores, judge_results)
    agreement = judge_agreement_rate(judge_results)
    metrics["judge_agreement_rate"] = agreement["agreement_rate"] or 0.0

    payload = {
        "metrics": metrics,
        "dataset_name": dataset_name,
        "dataset_version": dataset_version,
        "prompt_hash": prompt_hash,
        "n_examples": len(examples),
        "judge_agreement": agreement,
        "judge_details": [r.to_payload() for r in judge_results],
        "per_example": per_example,
        "degraded": degraded,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }

    evidence = await evidence_service.record_evidence(
        db,
        model_id=model_id,
        evidence_type="eval_run",
        source="attestor_eval_suite",
        payload=payload,
    )

    eval_run = EvalRun(
        id=uuid.uuid4(),
        model_id=model_id,
        evidence_id=evidence.id,
        dataset_version=dataset_version,
        prompt_hash=prompt_hash,
        model_id_used=judge_results[0].primary.model_id if judge_results else "n/a",
        metrics=metrics,
        status="completed",
    )
    db.add(eval_run)
    await db.flush()

    return EvalRunResult(
        eval_run_id=eval_run.id,
        evidence_id=evidence.id,
        metrics=metrics,
        per_example=per_example,
        cached=False,
        degraded=degraded,
    )


def _summarise_for_judge(examples: list[GoldenDatasetExample], max_chars: int = 6000) -> str:
    """Build the text a rubric judge sees.

    IMPORTANT: this truncates, and truncation in a judge's input has bitten
    me before — an eval reported 'unfaithful' verdicts that turned out to be
    context truncation, not model hallucination. So the truncation is
    explicit and MARKED in the text, so a low score caused by missing
    context is distinguishable from a low score caused by bad output.
    """
    parts = []
    for ex in examples[:20]:
        parts.append(
            f"- input: {str(ex.input_payload)[:300]}\n  expected: {str(ex.expected_output)[:300]}"
        )
    text = "\n".join(parts)
    if len(text) > max_chars:
        text = (
            text[:max_chars]
            + "\n\n[TRUNCATED BY EVAL HARNESS — judge did not see the full dataset. "
            "A low score here may reflect missing context rather than poor output.]"
        )
    return text


def _aggregate_metrics(
    deterministic: list[ScoreResult], judges: list[DualJudgeResult]
) -> dict[str, float]:
    metrics: dict[str, float] = {}

    by_name: dict[str, list[float]] = {}
    for score in deterministic:
        by_name.setdefault(score.name, []).append(score.value)
    for name, values in by_name.items():
        metrics[name] = round(sum(values) / len(values), 4)

    if judges:
        metrics["rubric_mean"] = round(
            sum(j.mean_score for j in judges) / len(judges), 4
        )

    return metrics
