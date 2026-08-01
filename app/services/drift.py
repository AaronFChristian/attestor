"""
Drift monitoring.

The governance problem: an eval suite tells you how a model performs on the
golden dataset. It tells you nothing about whether production traffic still
resembles that dataset. A model can hold a 0.94 faithfulness score forever
while the questions users actually ask drift somewhere the golden set never
covered — and the score stays green the whole way down.

Two signals implemented here, deliberately both cheap and deterministic:

1. METRIC DRIFT — rolling comparison of an eval metric against its own
   baseline. Catches gradual quality decay across prompt/model changes.

2. DISTRIBUTION DRIFT — how far current production inputs sit from the
   golden set, approximated by lexical overlap rather than embeddings.

On that second choice: a proper implementation uses embedding distance
(e.g. population stability index over embedding clusters). Lexical overlap
is a weaker proxy. It's used here because it needs no embedding model call
per sample, which keeps this affordable to run continuously on a portfolio
budget — and because a weaker signal that actually runs beats a better one
that's too expensive to schedule. Say that out loud rather than implying
this is production-grade drift detection; the honest framing is stronger
than the overclaim.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orm import EvalRun, GoldenDatasetExample

METRIC_DRIFT_THRESHOLD = 0.05  # a 5-point drop is material
DISTRIBUTION_DRIFT_THRESHOLD = 0.60  # below 60% overlap = investigate


@dataclass
class DriftSignal:
    signal_type: str
    metric_name: str
    baseline_value: float | None
    current_value: float
    delta: float | None
    breached: bool
    detail: str

    def to_payload(self) -> dict:
        return {
            "signal_type": self.signal_type,
            "metric_name": self.metric_name,
            "baseline_value": self.baseline_value,
            "current_value": self.current_value,
            "delta": self.delta,
            "breached": self.breached,
            "detail": self.detail,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
        }


def _tokenize(text: str) -> set[str]:
    return {t for t in text.lower().split() if len(t) > 2}


async def detect_metric_drift(
    db: AsyncSession, model_id: uuid.UUID, metric_name: str
) -> DriftSignal | None:
    """Compare the most recent eval run's metric against the run before it."""
    result = await db.execute(
        select(EvalRun)
        .where(EvalRun.model_id == model_id)
        .order_by(EvalRun.created_at.desc())
        .limit(2)
    )
    runs = list(result.scalars().all())

    if len(runs) < 2:
        return None  # not enough history to establish a baseline yet

    current_run, baseline_run = runs[0], runs[1]
    current = current_run.metrics.get(metric_name)
    baseline = baseline_run.metrics.get(metric_name)

    if current is None or baseline is None:
        return None

    delta = round(float(current) - float(baseline), 4)
    breached = delta <= -METRIC_DRIFT_THRESHOLD

    return DriftSignal(
        signal_type="metric_drift",
        metric_name=metric_name,
        baseline_value=float(baseline),
        current_value=float(current),
        delta=delta,
        breached=breached,
        detail=(
            f"{metric_name} moved {delta:+.4f} from {baseline} to {current}. "
            + (
                "Exceeds the material-degradation threshold; this warrants a finding."
                if breached
                else "Within tolerance."
            )
        ),
    )


async def detect_distribution_drift(
    db: AsyncSession,
    model_id: uuid.UUID,
    dataset_name: str,
    dataset_version: str,
    production_samples: list[str],
) -> DriftSignal | None:
    """Approximate how well the golden set still covers production traffic."""
    if not production_samples:
        return None

    result = await db.execute(
        select(GoldenDatasetExample).where(
            GoldenDatasetExample.dataset_name == dataset_name,
            GoldenDatasetExample.dataset_version == dataset_version,
        )
    )
    examples = list(result.scalars().all())
    if not examples:
        return None  # guard: no golden set means no meaningful comparison

    golden_vocab: set[str] = set()
    for ex in examples:
        golden_vocab |= _tokenize(str(ex.input_payload))

    coverages = []
    for sample in production_samples:
        sample_tokens = _tokenize(sample)
        if not sample_tokens:
            continue
        coverages.append(len(sample_tokens & golden_vocab) / len(sample_tokens))

    if not coverages:
        return None

    mean_coverage = round(sum(coverages) / len(coverages), 4)
    breached = mean_coverage < DISTRIBUTION_DRIFT_THRESHOLD

    return DriftSignal(
        signal_type="distribution_drift",
        metric_name="golden_set_coverage",
        baseline_value=DISTRIBUTION_DRIFT_THRESHOLD,
        current_value=mean_coverage,
        delta=round(mean_coverage - DISTRIBUTION_DRIFT_THRESHOLD, 4),
        breached=breached,
        detail=(
            f"Production inputs share {mean_coverage:.1%} lexical overlap with the "
            f"golden set (n={len(coverages)} samples). "
            + (
                "Below threshold — the golden set may no longer represent live traffic, "
                "which means the eval scores are measuring the wrong distribution."
                if breached
                else "Golden set still broadly representative."
            )
        ),
    )
