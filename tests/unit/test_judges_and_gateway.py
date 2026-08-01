from app.evals.judges import (
    DISAGREEMENT_THRESHOLD,
    DualJudgeResult,
    JudgeScore,
    _parse_judge_output,
    judge_agreement_rate,
)
from app.gateway.llm import _build_anthropic_kwargs, compute_prompt_hash


class TestJudgeOutputParsing:
    def test_parses_clean_json(self):
        score = _parse_judge_output('{"score": 0.8, "rationale": "good"}', "m", "p")
        assert score.score == 0.8
        assert score.rationale == "good"

    def test_strips_markdown_fences(self):
        """Judges are told to return bare JSON. They wrap it in fences
        anyway, constantly. If this regresses, every judge score silently
        becomes 0.0 and the eval suite reports catastrophic quality."""
        score = _parse_judge_output('```json\n{"score": 0.9, "rationale": "x"}\n```', "m", "p")
        assert score.score == 0.9

    def test_clamps_out_of_range_high(self):
        score = _parse_judge_output('{"score": 1.4, "rationale": "x"}', "m", "p")
        assert score.score == 1.0

    def test_clamps_out_of_range_low(self):
        score = _parse_judge_output('{"score": -0.5, "rationale": "x"}', "m", "p")
        assert score.score == 0.0

    def test_unparseable_degrades_not_crashes(self):
        """A malformed judge response must not take down the whole eval run.
        It degrades to 0.0 with a marked rationale so it's visibly a parse
        failure, not a genuine low score."""
        score = _parse_judge_output("I think it's pretty good actually!", "m", "p")
        assert score.score == 0.0
        assert "UNPARSEABLE" in score.rationale

    def test_missing_score_key_degrades(self):
        score = _parse_judge_output('{"rationale": "no score here"}', "m", "p")
        assert score.score == 0.0
        assert "UNPARSEABLE" in score.rationale


class TestJudgeAgreement:
    def _make(self, primary: float, secondary: float) -> DualJudgeResult:
        disagreement = abs(primary - secondary)
        return DualJudgeResult(
            primary=JudgeScore(primary, "", "claude", "anthropic"),
            secondary=JudgeScore(secondary, "", "llama", "groq"),
            mean_score=(primary + secondary) / 2,
            disagreement=disagreement,
            requires_human_review=disagreement > DISAGREEMENT_THRESHOLD,
            criterion="test",
        )

    def test_perfect_agreement(self):
        results = [self._make(0.9, 0.9), self._make(0.5, 0.5)]
        agg = judge_agreement_rate(results)
        assert agg["agreement_rate"] == 1.0
        assert agg["escalated"] == 0

    def test_disagreement_escalates_to_human(self):
        results = [self._make(0.9, 0.2)]
        agg = judge_agreement_rate(results)
        assert agg["escalated"] == 1
        assert agg["agreement_rate"] == 0.0

    def test_empty_results_does_not_divide_by_zero(self):
        agg = judge_agreement_rate([])
        assert agg["n"] == 0
        assert agg["agreement_rate"] is None


class TestGatewayModelQuirks:
    def test_sonnet_5_omits_temperature_entirely(self):
        """claude-sonnet-5 REJECTS the temperature parameter — it doesn't
        ignore it, it errors. If this regresses, every judgment-class call
        in the system fails at runtime."""
        kwargs = _build_anthropic_kwargs("claude-sonnet-5", 0.0)
        assert "temperature" not in kwargs

    def test_haiku_accepts_temperature(self):
        kwargs = _build_anthropic_kwargs("claude-haiku-4-5", 0.0)
        assert kwargs["temperature"] == 0.0

    def test_none_temperature_is_omitted_not_passed_as_null(self):
        kwargs = _build_anthropic_kwargs("claude-haiku-4-5", None)
        assert "temperature" not in kwargs


class TestPromptHash:
    def test_deterministic(self):
        a = compute_prompt_hash("sys", "user")
        b = compute_prompt_hash("sys", "user")
        assert a == b

    def test_changes_with_system_prompt(self):
        assert compute_prompt_hash("sys1", "user") != compute_prompt_hash("sys2", "user")

    def test_changes_with_user_prompt(self):
        assert compute_prompt_hash("sys", "u1") != compute_prompt_hash("sys", "u2")

    def test_is_hex_sha256(self):
        h = compute_prompt_hash("a", "b")
        assert len(h) == 64
        int(h, 16)  # raises if not valid hex
