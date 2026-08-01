from app.evals.scorers import (
    jaccard_similarity,
    score_citation_resolvability,
    score_schema_conformance,
    score_tool_correctness,
)


class TestJaccardSimilarity:
    def test_identical_sets_score_one(self):
        assert jaccard_similarity({"a", "b"}, {"a", "b"}) == 1.0

    def test_disjoint_sets_score_zero(self):
        assert jaccard_similarity({"a"}, {"b"}) == 0.0

    def test_both_empty_scores_one(self):
        """An agent correctly deciding no tools were needed is a CORRECT
        outcome, not an undefined one. If this ever returns 0.0 or raises,
        every 'no tool needed' example in the golden set fails."""
        assert jaccard_similarity(set(), set()) == 1.0

    def test_order_does_not_matter(self):
        a = jaccard_similarity({"x", "y", "z"}, {"z", "y", "x"})
        assert a == 1.0

    def test_partial_overlap(self):
        # {a,b} ∩ {b,c} = {b} (1); {a,b} ∪ {b,c} = {a,b,c} (3)
        assert jaccard_similarity({"a", "b"}, {"b", "c"}) == 1 / 3

    def test_spurious_extra_tool_is_penalised(self):
        """Calling extra tools isn't free — it costs money and latency and
        can have side effects. Must not score 1.0."""
        assert jaccard_similarity({"a"}, {"a", "b"}) < 1.0


class TestToolCorrectness:
    def test_exact_match_passes(self):
        result = score_tool_correctness(["get_x", "get_y"], ["get_y", "get_x"])
        assert result.value == 1.0
        assert result.passed

    def test_missing_tool_reported_in_detail(self):
        result = score_tool_correctness(["get_x", "get_y"], ["get_x"])
        assert "missing" in result.detail
        assert "get_y" in result.detail

    def test_spurious_tool_reported_in_detail(self):
        result = score_tool_correctness(["get_x"], ["get_x", "drop_table"])
        assert "unexpected" in result.detail
        assert "drop_table" in result.detail

    def test_below_threshold_fails(self):
        result = score_tool_correctness(["a", "b", "c"], ["a"], threshold=0.8)
        assert not result.passed


class TestSchemaConformance:
    def test_all_fields_present_passes(self):
        result = score_schema_conformance(
            {"disposition": "escalate", "rationale": "because"},
            ["disposition", "rationale"],
        )
        assert result.value == 1.0
        assert result.passed

    def test_missing_field_is_total_failure_not_partial(self):
        """Deliberately binary. A Finding missing its severity isn't a
        lower-quality Finding — it isn't a Finding."""
        result = score_schema_conformance({"disposition": "escalate"}, ["disposition", "rationale"])
        assert result.value == 0.0
        assert not result.passed

    def test_empty_string_counts_as_missing(self):
        result = score_schema_conformance({"rationale": ""}, ["rationale"])
        assert not result.passed

    def test_none_counts_as_missing(self):
        result = score_schema_conformance({"rationale": None}, ["rationale"])
        assert not result.passed


class TestCitationResolvability:
    def test_all_citations_resolve(self):
        result = score_citation_resolvability(["e1", "e2"], {"e1", "e2", "e3"})
        assert result.value == 1.0
        assert result.passed

    def test_partial_resolution_fails_not_just_scores_low(self):
        """Anything less than 100% resolvable is a real problem — a report
        with one fabricated citation is not 'mostly fine'."""
        result = score_citation_resolvability(["e1", "ghost"], {"e1"})
        assert result.value == 0.5
        assert not result.passed
        assert "ghost" in result.detail

    def test_no_citations_is_vacuously_true(self):
        result = score_citation_resolvability([], {"e1"})
        assert result.passed
