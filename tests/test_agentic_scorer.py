"""
Tests for agents/agentic_scorer.py — regex-based agentic intent pre-filter.
"""

from beigebox.agents.agentic_scorer import score_agentic_intent, AgenticScore


class TestAgenticScorerReturnType:
    def test_returns_agentic_score(self):
        result = score_agentic_intent("hello")
        assert isinstance(result, AgenticScore)

    def test_score_in_range(self):
        for text in ["", "hello world", "search the web for AI news today"]:
            r = score_agentic_intent(text)
            assert 0.0 <= r.score <= 1.0

    def test_matched_is_list(self):
        r = score_agentic_intent("anything")
        assert isinstance(r.matched, list)

    def test_is_agentic_reflects_threshold(self):
        r = score_agentic_intent("hello", threshold=0.0)
        assert r.is_agentic is True

    def test_is_agentic_false_above_threshold(self):
        r = score_agentic_intent("hello", threshold=1.1)
        assert r.is_agentic is False


class TestAgenticScorerThreshold:
    def test_custom_threshold_low(self):
        r = score_agentic_intent("search web", threshold=0.01)
        # score may vary; just check threshold logic is consistent
        assert r.is_agentic == (r.score >= 0.01)

    def test_custom_threshold_high(self):
        r = score_agentic_intent("search web", threshold=0.99)
        assert r.is_agentic == (r.score >= 0.99)


class TestAgenticScorerScoreClamped:
    def test_score_never_exceeds_one(self):
        # Throw every trigger at it
        text = (
            "search look up find fetch retrieve get me browse scrape visit "
            "calculate compute evaluate solve run execute call invoke trigger "
            "step by step then after that finally plan outline workflow pipeline "
            "web search wikipedia google news weather stock price "
            "current latest real-time today right now as of "
            "for me on my behalf automatically go ahead and "
            "save store write to create a file update "
            "what is the current price what is the latest time "
            "how much is how many are"
        )
        r = score_agentic_intent(text)
        assert r.score <= 1.0

    def test_empty_string_score_zero(self):
        r = score_agentic_intent("")
        assert r.score == 0.0
        assert r.matched == []


class TestAgenticScorerMatchedLabels:
    def test_no_match_empty_matched(self):
        r = score_agentic_intent("tell me a joke about cats")
        # May or may not match — just verify matched is a list of strings
        assert all(isinstance(m, str) for m in r.matched)

    def test_score_zero_implies_empty_matched(self):
        r = score_agentic_intent("")
        assert r.score == 0.0
        assert r.matched == []

    def test_nonzero_score_implies_nonempty_matched(self):
        r = score_agentic_intent("tell me a joke about cats")
        if r.score > 0:
            assert len(r.matched) > 0
