"""
Dual-judge rubric scoring.

The problem this solves: Attestor uses Claude to judge the quality of
systems that were, in this portfolio, also built with Claude. A model
scoring outputs from its own family exhibits self-preference bias — it
rates text that matches its own style and reasoning patterns more
favourably. With a single judge you have no way to detect this and no
answer when someone asks about it.

The control: score every rubric item with two judges from different model
families (Claude via Anthropic, Llama via Groq), record both scores, and
compute agreement. Where they disagree beyond a threshold, the item is
flagged for MANDATORY human review rather than being averaged into a
number that hides the disagreement.

The agreement rate itself is a governance metric. "Our judges agree 94% of
the time, and the 6% goes to a human" is a defensible statement. "We used
an LLM to score it" is not.
"""
import json
import re
from dataclasses import dataclass

from app.gateway.llm import get_gateway

# Beyond this gap on a 0-1 scale, the two judges are considered to be
# materially disagreeing and the item escalates to a human.
DISAGREEMENT_THRESHOLD = 0.25

_JUDGE_SYSTEM = """You are an independent model-risk validator scoring a single \
rubric criterion. You must be strict and evidence-bound.

Respond with ONLY a JSON object, no preamble, no markdown fences:
{"score": <float 0.0-1.0>, "rationale": "<one sentence>"}

Score 1.0 only if the criterion is fully and demonstrably met. Score 0.0 if \
it is not met at all. Do not award credit for plausible-sounding text that \
lacks specific supporting detail."""


@dataclass
class JudgeScore:
    score: float
    rationale: str
    model_id: str
    provider: str


@dataclass
class DualJudgeResult:
    primary: JudgeScore
    secondary: JudgeScore
    mean_score: float
    disagreement: float
    requires_human_review: bool
    criterion: str

    def to_payload(self) -> dict:
        return {
            "criterion": self.criterion,
            "primary": {
                "score": self.primary.score,
                "model_id": self.primary.model_id,
                "provider": self.primary.provider,
                "rationale": self.primary.rationale,
            },
            "secondary": {
                "score": self.secondary.score,
                "model_id": self.secondary.model_id,
                "provider": self.secondary.provider,
                "rationale": self.secondary.rationale,
            },
            "mean_score": self.mean_score,
            "disagreement": self.disagreement,
            "requires_human_review": self.requires_human_review,
        }


def _parse_judge_output(text: str, model_id: str, provider: str) -> JudgeScore:
    """Judges are instructed to return bare JSON. They sometimes wrap it in
    fences anyway. Strip and parse defensively — a malformed judge response
    should degrade to a flagged-for-review 0.0, never crash the eval run."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(cleaned)
        score = float(data["score"])
        score = max(0.0, min(1.0, score))  # clamp; judges occasionally emit 1.2
        return JudgeScore(
            score=score,
            rationale=str(data.get("rationale", ""))[:500],
            model_id=model_id,
            provider=provider,
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return JudgeScore(
            score=0.0,
            rationale=f"UNPARSEABLE_JUDGE_OUTPUT: {text[:200]}",
            model_id=model_id,
            provider=provider,
        )


def score_criterion(
    criterion: str,
    subject_output: str,
    supporting_context: str = "",
) -> DualJudgeResult:
    """Score one rubric criterion with both judges."""
    gateway = get_gateway()

    user_prompt = (
        f"CRITERION TO SCORE:\n{criterion}\n\n"
        f"SUPPORTING CONTEXT:\n{supporting_context or '(none provided)'}\n\n"
        f"OUTPUT UNDER REVIEW:\n{subject_output}"
    )

    primary_response = gateway.complete(
        task_class="judgment",
        system=_JUDGE_SYSTEM,
        user=user_prompt,
        max_tokens=512,
    )
    primary = _parse_judge_output(
        primary_response.text, primary_response.model_id, primary_response.provider
    )

    secondary_response = gateway.complete_secondary_judge(
        system=_JUDGE_SYSTEM, user=user_prompt, max_tokens=512
    )
    secondary = _parse_judge_output(
        secondary_response.text, secondary_response.model_id, secondary_response.provider
    )

    disagreement = abs(primary.score - secondary.score)

    return DualJudgeResult(
        primary=primary,
        secondary=secondary,
        mean_score=round((primary.score + secondary.score) / 2, 4),
        disagreement=round(disagreement, 4),
        requires_human_review=disagreement > DISAGREEMENT_THRESHOLD,
        criterion=criterion,
    )


def judge_agreement_rate(results: list[DualJudgeResult]) -> dict:
    """Aggregate agreement across a run. This is the number you quote when
    someone asks how you know the judge is trustworthy."""
    if not results:
        return {"agreement_rate": None, "n": 0, "escalated": 0}
    escalated = sum(1 for r in results if r.requires_human_review)
    mean_disagreement = sum(r.disagreement for r in results) / len(results)
    return {
        "agreement_rate": round(1 - (escalated / len(results)), 4),
        "mean_disagreement": round(mean_disagreement, 4),
        "n": len(results),
        "escalated": escalated,
        "threshold": DISAGREEMENT_THRESHOLD,
    }
