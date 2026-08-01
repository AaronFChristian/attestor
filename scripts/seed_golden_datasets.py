"""
Seeds golden datasets for the three governed subject models, plus one for
Attestor's own validation agent.

That last one is the interesting part. Attestor's golden set contains
human-labelled examples of what a GOOD finding looks like and what a
BAD/ungrounded finding looks like — which is what lets Attestor produce
outcomes-analysis evidence about itself. On Day 3, rejected findings from
the human review queue flow back into this same table with
label_source='rejected_finding', so the dataset improves as the tool is used.

Run with: uv run python scripts/seed_golden_datasets.py
Idempotent: skips examples that already exist for a given name+version.
"""
import asyncio
import uuid

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.orm import GoldenDatasetExample, GovernedModel

DATASET_VERSION = "v1"


def _fraud_agent_examples() -> list[dict]:
    return [
        {
            "input_payload": {
                "query": "Alert 88213: card-not-present transaction, $4,200, new device, foreign IP",
                "observed_output": {
                    "tools_called": ["get_transaction_history", "check_device_fingerprint", "score_risk"],
                    "disposition": "escalate",
                    "rationale": "New device plus foreign IP on a high-value CNP transaction.",
                },
            },
            "expected_output": {
                "expected_tools": ["get_transaction_history", "check_device_fingerprint", "score_risk"],
                "required_fields": ["disposition", "rationale"],
                "expected_disposition": "escalate",
            },
        },
        {
            "input_payload": {
                "query": "Alert 88214: $12 coffee purchase, known device, home city",
                "observed_output": {
                    "tools_called": ["get_transaction_history", "score_risk"],
                    "disposition": "auto_close",
                    "rationale": "Low value, known device, established merchant pattern.",
                },
            },
            "expected_output": {
                "expected_tools": ["get_transaction_history", "score_risk"],
                "required_fields": ["disposition", "rationale"],
                "expected_disposition": "auto_close",
            },
        },
        {
            "input_payload": {
                "query": "Alert 88215: five $900 transfers in 20 minutes to five new payees",
                "observed_output": {
                    # Deliberately WRONG: skipped the payee check. This example
                    # exists so the tool-correctness metric has something to
                    # actually catch — a golden set where everything passes
                    # tells you nothing about whether the metric works.
                    "tools_called": ["get_transaction_history"],
                    "disposition": "auto_close",
                    "rationale": "Individually below threshold.",
                },
            },
            "expected_output": {
                "expected_tools": ["get_transaction_history", "check_payee_history", "score_risk"],
                "required_fields": ["disposition", "rationale"],
                "expected_disposition": "escalate",
            },
        },
    ]


def _support_rag_examples() -> list[dict]:
    return [
        {
            "input_payload": {
                "query": "What is the daily ATM withdrawal limit on a standard checking account?",
                "observed_output": {
                    "tools_called": ["retrieve_policy_docs"],
                    "answer": "The standard daily ATM withdrawal limit is $500.",
                    "citations": ["policy-doc-ATM-2026-p3"],
                },
            },
            "expected_output": {
                "expected_tools": ["retrieve_policy_docs"],
                "required_fields": ["answer", "citations"],
            },
        },
        {
            "input_payload": {
                "query": "Can you tell me my account balance?",
                "observed_output": {
                    "tools_called": [],
                    "answer": "I can't access account balances. Please sign in to online banking.",
                    "citations": [],
                },
            },
            "expected_output": {
                # Correctly calling NO tools is a valid expected outcome —
                # this is why jaccard_similarity defines both-empty as 1.0.
                "expected_tools": [],
                "required_fields": ["answer"],
            },
        },
    ]


def _credit_classifier_examples() -> list[dict]:
    return [
        {
            "input_payload": {
                "query": "Customer 4471: utilization 22%, on-time 36/36, income verified up 15%",
                "observed_output": {
                    "tools_called": ["fetch_credit_profile", "compute_band"],
                    "recommended_band": "increase_tier_2",
                    "rationale": "Sustained on-time history with verified income growth.",
                },
            },
            "expected_output": {
                "expected_tools": ["fetch_credit_profile", "compute_band"],
                "required_fields": ["recommended_band", "rationale"],
            },
        },
        {
            "input_payload": {
                "query": "Customer 4472: utilization 94%, two late payments in 6 months",
                "observed_output": {
                    "tools_called": ["fetch_credit_profile", "compute_band"],
                    "recommended_band": "decrease_tier_1",
                    "rationale": "High utilization with recent delinquency.",
                },
            },
            "expected_output": {
                "expected_tools": ["fetch_credit_profile", "compute_band"],
                "required_fields": ["recommended_band", "rationale"],
            },
        },
    ]


def _attestor_self_examples() -> list[dict]:
    """Human-labelled examples of good vs. ungrounded findings. This is what
    makes Attestor's self-governance claim measurable rather than rhetorical."""
    return [
        {
            "input_payload": {
                "scenario": "Eval run shows faithfulness dropped 0.91 -> 0.83 across a prompt change",
                "observed_output": {
                    "tools_called": ["get_eval_history", "get_prompt_versions"],
                    "claim": "Faithfulness declined from 0.91 to 0.83 following prompt version change.",
                    "evidence_cited": True,
                    "cited_metric_name": "faithfulness",
                    "cited_metric_value": 0.83,
                },
            },
            "expected_output": {
                "expected_tools": ["get_eval_history", "get_prompt_versions"],
                "required_fields": ["claim", "cited_metric_name"],
                "label": "good_finding",
            },
        },
        {
            "input_payload": {
                "scenario": "Same eval run, but agent invents a number that was never recorded",
                "observed_output": {
                    "tools_called": ["get_eval_history"],
                    "claim": "Faithfulness collapsed to 0.42, indicating severe degradation.",
                    "evidence_cited": True,
                    "cited_metric_name": "faithfulness",
                    "cited_metric_value": 0.42,
                },
            },
            "expected_output": {
                "expected_tools": ["get_eval_history", "get_prompt_versions"],
                "required_fields": ["claim", "cited_metric_name"],
                # The attribution gate MUST reject this one. It's the
                # negative control for the whole system.
                "label": "ungrounded_finding_must_be_blocked",
            },
        },
    ]


DATASETS = {
    "fraud-triage-agent-v3": ("fraud_triage_golden", _fraud_agent_examples),
    "customer-support-rag": ("support_rag_golden", _support_rag_examples),
    "credit-line-adjustment-classifier": ("credit_classifier_golden", _credit_classifier_examples),
    "attestor-validation-agent": ("attestor_self_golden", _attestor_self_examples),
}


async def main() -> None:
    async with AsyncSessionLocal() as db:
        for model_name, (dataset_name, builder) in DATASETS.items():
            result = await db.execute(
                select(GovernedModel).where(GovernedModel.name == model_name)
            )
            model = result.scalar_one_or_none()
            if model is None:
                print(f"  SKIP: model '{model_name}' not found — run seed_data.py first")
                continue

            existing = await db.execute(
                select(GoldenDatasetExample).where(
                    GoldenDatasetExample.dataset_name == dataset_name,
                    GoldenDatasetExample.dataset_version == DATASET_VERSION,
                )
            )
            if existing.scalars().first() is not None:
                print(f"  SKIP: dataset '{dataset_name}' {DATASET_VERSION} already seeded")
                continue

            examples = builder()
            for ex in examples:
                db.add(
                    GoldenDatasetExample(
                        id=uuid.uuid4(),
                        dataset_name=dataset_name,
                        dataset_version=DATASET_VERSION,
                        model_id=model.id,
                        input_payload=ex["input_payload"],
                        expected_output=ex["expected_output"],
                        label_source="human",
                    )
                )
            print(f"  seeded '{dataset_name}' {DATASET_VERSION} with {len(examples)} examples")

        await db.commit()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
