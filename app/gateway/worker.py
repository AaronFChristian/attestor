"""
ARQ background worker.

This module is what `docker-compose.yml` points the `worker` service at. It
did not exist until Day 2, which means the worker container was
crash-looping silently since Day 1 — Docker kept restarting it and
`docker compose ps` reported it as running.

Worth internalising as a general lesson: a container in "restarting" state
looks alive in most dashboards. Check logs, not status, when you care.

Why eval runs belong here and not in the request thread: a full suite makes
multiple LLM calls and takes minutes. Running that inside an HTTP handler
means the request times out, the client retries, and you pay for the same
eval twice. Async + idempotency-keyed is the correct shape.
"""
import os
import uuid

from arq import cron
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.evals.runner import EmptyDatasetError, run_eval_suite
from app.services import audit

settings = get_settings()


async def execute_eval_suite(
    ctx: dict,
    model_id: str,
    dataset_name: str,
    dataset_version: str,
    prompt_hash: str,
    rubric_criteria: list[str],
    actor: str = "system",
) -> dict:
    """Background job: run an eval suite and persist evidence."""
    async with AsyncSessionLocal() as db:
        try:
            result = await run_eval_suite(
                db,
                model_id=uuid.UUID(model_id),
                dataset_name=dataset_name,
                dataset_version=dataset_version,
                prompt_hash=prompt_hash,
                rubric_criteria=rubric_criteria,
            )
            await audit.record(
                db,
                actor=actor,
                action="execute_eval_suite",
                resource_type="eval_run",
                resource_id=str(result.eval_run_id),
                detail={
                    "model_id": model_id,
                    "dataset_version": dataset_version,
                    "cached": result.cached,
                    "metrics": result.metrics,
                },
            )
            await db.commit()
            return {
                "eval_run_id": str(result.eval_run_id),
                "evidence_id": str(result.evidence_id),
                "metrics": result.metrics,
                "cached": result.cached,
            }
        except EmptyDatasetError as exc:
            # Explicitly audit the abort. A run that didn't happen because
            # the dataset was empty is itself governance-relevant
            # information — it means a model went un-evaluated.
            await audit.record(
                db,
                actor=actor,
                action="eval_suite_aborted_empty_dataset",
                resource_type="governed_model",
                resource_id=model_id,
                detail={"dataset_name": dataset_name, "dataset_version": dataset_version,
                        "error": str(exc)},
            )
            await db.commit()
            raise


async def verify_audit_chain_scheduled(ctx: dict) -> dict:
    """Scheduled integrity check on the audit log.

    Running this on a schedule rather than only on demand is the difference
    between "we can verify the chain" and "we do verify the chain." An
    examiner will ask which one it is.
    """
    async with AsyncSessionLocal() as db:
        report = await audit.verify_chain(db)
        if not report["chain_intact"]:
            await audit.record(
                db,
                actor="system",
                action="audit_chain_integrity_failure",
                resource_type="audit_log",
                resource_id="chain",
                detail=report,
            )
            await db.commit()
        return report


async def startup(ctx: dict) -> None:
    if settings.langsmith_api_key:
        # Same activation as app/main.py, duplicated deliberately: eval
        # suites execute in THIS process (the ARQ worker), which never
        # imports main.py, so main.py's setup never reaches here. Without
        # this, a LangSmith key configured in .env would trace validation
        # runs (API process) but silently miss every eval run (worker
        # process) — a confusing, easy-to-miss gap if left unaddressed.
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
        os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    print("[worker] Attestor worker started.")


async def shutdown(ctx: dict) -> None:
    print("[worker] Attestor worker shutting down.")


class WorkerSettings:
    functions = [execute_eval_suite, verify_audit_chain_scheduled]
    cron_jobs = [
        # Hourly audit-chain verification. Cheap, and it means tampering is
        # detected within an hour rather than at the next exam.
        cron(verify_audit_chain_scheduled, minute=0),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 5  # bounded: eval runs are LLM-bound, unbounded concurrency
    # would blow through rate limits and cost budget simultaneously
    job_timeout = 900  # 15 min — a full suite with judges is genuinely slow
