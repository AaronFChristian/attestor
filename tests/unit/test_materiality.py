from app.models.orm import MaterialityTier
from app.services.materiality import MaterialityInputs, compute_materiality


def test_max_inputs_yield_tier_1():
    result = compute_materiality(
        MaterialityInputs(
            decision_autonomy=5, financial_exposure=5, customer_impact=5, reversibility=5
        )
    )
    assert result.tier == MaterialityTier.TIER_1
    assert result.weighted_score == 5.0


def test_min_inputs_yield_tier_3():
    result = compute_materiality(
        MaterialityInputs(
            decision_autonomy=1, financial_exposure=1, customer_impact=1, reversibility=1
        )
    )
    assert result.tier == MaterialityTier.TIER_3
    assert result.weighted_score == 1.0


def test_scorecard_is_deterministic():
    inputs = MaterialityInputs(
        decision_autonomy=3, financial_exposure=4, customer_impact=2, reversibility=3
    )
    r1 = compute_materiality(inputs)
    r2 = compute_materiality(inputs)
    assert r1.weighted_score == r2.weighted_score
    assert r1.tier == r2.tier


def test_financial_exposure_weighted_higher_than_reversibility():
    """Sanity check on the weights themselves: a model with high financial
    exposure but low reversibility concern should score higher than the
    inverse, given financial_exposure carries 0.30 vs reversibility 0.15."""
    high_financial = compute_materiality(
        MaterialityInputs(
            decision_autonomy=2, financial_exposure=5, customer_impact=2, reversibility=1
        )
    )
    high_reversibility = compute_materiality(
        MaterialityInputs(
            decision_autonomy=2, financial_exposure=1, customer_impact=2, reversibility=5
        )
    )
    assert high_financial.weighted_score > high_reversibility.weighted_score


def test_out_of_range_input_rejected():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        MaterialityInputs(decision_autonomy=6, financial_exposure=1, customer_impact=1, reversibility=1)
