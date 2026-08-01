"""
Deterministic scorers.

Everything in this file is arithmetic — no model calls. That's deliberate.
An eval suite built entirely on LLM-as-judge has no floor: if the judge
drifts, every score drifts with it and you have no way to notice. Mixing
deterministic scorers in gives you a set of metrics that CANNOT drift, so
when a judge score moves and these don't, you know the problem is the judge.

The tool-correctness metric here is the custom one. RAGAS covers retrieval
quality; nothing in RAGAS tells you whether an AGENT called the right
tools. For governing agentic systems that's the more important question,
which is why it's built by hand rather than imported.
"""
from dataclasses import dataclass


@dataclass
class ScoreResult:
    name: str
    value: float
    passed: bool
    detail: str = ""


def jaccard_similarity(expected: set[str], actual: set[str]) -> float:
    """|A ∩ B| / |A ∪ B|.

    Chosen over exact-match because agent tool selection is legitimately
    non-deterministic in ORDER and often in redundancy — an agent that calls
    [get_evals, get_traces] and one that calls [get_traces, get_evals] did
    the same work. Exact match would score that as a failure and generate
    noise. Jaccard captures "did it reach for the right set of tools" while
    still penalising both missing tools and spurious extra calls.

    Both empty is defined as 1.0: an agent correctly deciding no tools were
    needed is a correct outcome, not an undefined one.
    """
    if not expected and not actual:
        return 1.0
    union = expected | actual
    if not union:
        return 1.0
    return len(expected & actual) / len(union)


def score_tool_correctness(
    expected_tools: list[str], actual_tools: list[str], threshold: float = 0.8
) -> ScoreResult:
    expected_set = set(expected_tools)
    actual_set = set(actual_tools)
    value = jaccard_similarity(expected_set, actual_set)

    missing = sorted(expected_set - actual_set)
    spurious = sorted(actual_set - expected_set)
    detail_parts = []
    if missing:
        detail_parts.append(f"missing={missing}")
    if spurious:
        detail_parts.append(f"unexpected={spurious}")

    return ScoreResult(
        name="tool_correctness",
        value=round(value, 4),
        passed=value >= threshold,
        detail="; ".join(detail_parts) or "exact tool set match",
    )


def score_schema_conformance(output: dict, required_fields: list[str]) -> ScoreResult:
    """Structured output is a hard requirement in this system — a Finding
    without a severity isn't a lower-quality Finding, it's not a Finding.
    So this scores 1.0 or 0.0, no partial credit."""
    missing = [f for f in required_fields if f not in output or output[f] in (None, "")]
    passed = not missing
    return ScoreResult(
        name="schema_conformance",
        value=1.0 if passed else 0.0,
        passed=passed,
        detail="all required fields present" if passed else f"missing={missing}",
    )


def score_citation_resolvability(
    cited_ids: list[str], resolvable_ids: set[str]
) -> ScoreResult:
    """What fraction of the citations an output made actually resolve to
    real evidence. This is the metric-level counterpart to the attribution
    gate: the gate blocks individual bad findings, this measures the rate
    at which the agent produces them."""
    if not cited_ids:
        return ScoreResult(
            name="citation_resolvability",
            value=1.0,
            passed=True,
            detail="no citations made",
        )
    resolved = [c for c in cited_ids if c in resolvable_ids]
    value = len(resolved) / len(cited_ids)
    unresolved = [c for c in cited_ids if c not in resolvable_ids]
    return ScoreResult(
        name="citation_resolvability",
        value=round(value, 4),
        passed=value == 1.0,  # anything less than perfect is a real problem
        detail="all citations resolve" if not unresolved else f"unresolved={unresolved}",
    )


def score_latency_budget(latency_ms: int, budget_ms: int) -> ScoreResult:
    return ScoreResult(
        name="latency_budget",
        value=float(latency_ms),
        passed=latency_ms <= budget_ms,
        detail=f"{latency_ms}ms against {budget_ms}ms budget",
    )


def score_cost_budget(cost_usd: float, budget_usd: float) -> ScoreResult:
    return ScoreResult(
        name="cost_budget",
        value=round(cost_usd, 6),
        passed=cost_usd <= budget_usd,
        detail=f"${cost_usd:.6f} against ${budget_usd:.6f} budget",
    )
