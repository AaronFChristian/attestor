"""
Materiality scorecard.

This is deliberately NOT an LLM call. A risk tier that determines how much
scrutiny a model receives must be reproducible, auditable, and arguable in
plain arithmetic — "the LLM decided" is not an answer an examiner will
accept for why a model was tiered low. Four factors, each 1-5, weighted,
summed, and bucketed against fixed thresholds. Change the thresholds here,
not by asking a model to "use its judgment."
"""
from pydantic import BaseModel, Field

from app.models.orm import MaterialityTier


class MaterialityInputs(BaseModel):
    decision_autonomy: int = Field(
        ge=1, le=5,
        description="1=fully human-gated suggestion, 5=fully autonomous action with no human step",
    )
    financial_exposure: int = Field(
        ge=1, le=5,
        description="1=no direct financial impact, 5=directly moves money or sets a material rate/limit",
    )
    customer_impact: int = Field(
        ge=1, le=5,
        description="1=internal tooling only, 5=directly affects a retail customer's account or credit",
    )
    reversibility: int = Field(
        ge=1, le=5,
        description="1=trivially and immediately reversible, 5=effectively irreversible once actioned",
    )

WEIGHTS = {
    "decision_autonomy": 0.30,
    "financial_exposure": 0.30,
    "customer_impact": 0.25,
    "reversibility": 0.15,
}

# Weighted score range is 1.0 - 5.0. Thresholds are a portfolio-reasonable
# starting point — in a real deployment these would be set and owned by the
# MRM head, not hardcoded, which is exactly why they're isolated here.
TIER_1_THRESHOLD = 3.6
TIER_2_THRESHOLD = 2.4


class MaterialityResult(BaseModel):
    inputs: MaterialityInputs
    weighted_score: float
    tier: MaterialityTier
    rationale: str


def compute_materiality(inputs: MaterialityInputs) -> MaterialityResult:
    weighted_score = round(
        sum(getattr(inputs, factor) * weight for factor, weight in WEIGHTS.items()), 3
    )

    if weighted_score >= TIER_1_THRESHOLD:
        tier = MaterialityTier.TIER_1
        rationale = (
            f"Weighted score {weighted_score} >= {TIER_1_THRESHOLD}: "
            "full pillar sweep (conceptual soundness, outcomes analysis, "
            "ongoing monitoring) is mandatory."
        )
    elif weighted_score >= TIER_2_THRESHOLD:
        tier = MaterialityTier.TIER_2
        rationale = (
            f"Weighted score {weighted_score} in [{TIER_2_THRESHOLD}, {TIER_1_THRESHOLD}): "
            "conceptual soundness plus ongoing monitoring required; outcomes "
            "analysis on a reduced sampling cadence."
        )
    else:
        tier = MaterialityTier.TIER_3
        rationale = (
            f"Weighted score {weighted_score} < {TIER_2_THRESHOLD}: "
            "ongoing monitoring only, reviewed on regular schedule."
        )

    return MaterialityResult(
        inputs=inputs, weighted_score=weighted_score, tier=tier, rationale=rationale
    )
