"""Tests for route_check scorer, /api/v1/route-check endpoint, and routing holdout loading."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from beigebox.eval.scorer import score_route_check
from beigebox.eval.runner import EvalRunner
from beigebox.eval.models import EvalCase


# ---------------------------------------------------------------------------
# score_route_check
# ---------------------------------------------------------------------------

class TestScoreRouteCheck:
    def test_pass_when_routes_match(self):
        passed, score, reason = score_route_check(
            "", {"route": "simple"}, meta={"route": "simple"}
        )
        assert passed is True
        assert score == 1.0
        assert "simple" in reason

    def test_fail_when_routes_differ(self):
        passed, score, reason = score_route_check(
            "", {"route": "simple"}, meta={"route": "complex"}
        )
        assert passed is False
        assert score == 0.0
        assert "complex" in reason
        assert "simple" in reason

    def test_case_insensitive(self):
        passed, _, _ = score_route_check(
            "", {"route": "Simple"}, meta={"route": "simple"}
        )
        assert passed is True

    def test_fail_when_no_meta(self):
        passed, score, reason = score_route_check("", {"route": "simple"}, meta=None)
        assert passed is False
        assert "missing" in reason.lower() or "metadata" in reason.lower()

    def test_fail_when_meta_has_no_route(self):
        passed, score, reason = score_route_check("", {"route": "simple"}, meta={})
        assert passed is False

    def test_fail_when_expect_has_no_route(self):
        passed, score, reason = score_route_check("", {}, meta={"route": "simple"})
        assert passed is False
        assert "no expected route" in reason.lower()

    def test_all_four_routes_pass(self):
        for route in ("simple", "complex", "code", "creative"):
            passed, score, _ = score_route_check(
                "", {"route": route}, meta={"route": route}
            )
            assert passed, f"Expected pass for route={route}"
            assert score == 1.0

    def test_route_check_in_scorers_dict(self):
        from beigebox.eval.scorer import SCORERS
        assert "route_check" in SCORERS


# ---------------------------------------------------------------------------
# EvalCase — route field loading
# ---------------------------------------------------------------------------

class TestEvalCaseRouteField:
    def test_route_field_defaults_empty(self):
        case = EvalCase(id="x", input="hi")
        assert case.route == ""

    def test_route_field_set(self):
        case = EvalCase(id="x", input="hi", route="simple")
        assert case.route == "simple"


# ---------------------------------------------------------------------------
# EvalRunner — routing_holdout.yaml loading
# ---------------------------------------------------------------------------

class TestRoutingHoldoutLoading:
    def test_load_routing_holdout(self):
        suite = EvalRunner.load_suite("evals/routing_holdout.yaml")
        assert suite.name == "routing_holdout"
        assert len(suite.cases) == 48  # 15 simple + 10 complex + 15 code + 8 creative

    def test_all_cases_have_route_check_scorer(self):
        suite = EvalRunner.load_suite("evals/routing_holdout.yaml")
        for case in suite.cases:
            assert case.scorer == "route_check", f"Case {case.id} has scorer {case.scorer!r}"

    def test_route_merged_into_expect(self):
        suite = EvalRunner.load_suite("evals/routing_holdout.yaml")
        for case in suite.cases:
            assert "route" in case.expect, f"Case {case.id} missing route in expect"

    def test_four_routes_represented(self):
        suite = EvalRunner.load_suite("evals/routing_holdout.yaml")
        routes = {c.route for c in suite.cases}
        assert "simple" in routes
        assert "complex" in routes
        assert "code" in routes
        assert "creative" in routes

    def test_route_matches_expect_route(self):
        suite = EvalRunner.load_suite("evals/routing_holdout.yaml")
        for case in suite.cases:
            assert case.route == case.expect.get("route"), (
                f"Case {case.id}: case.route={case.route!r} != expect.route={case.expect.get('route')!r}"
            )


# ---------------------------------------------------------------------------
# EvalRunner._run_case — route_check path
# ---------------------------------------------------------------------------

class TestEvalRunnerRouteCheckPath:
    def _make_runner(self):
        return EvalRunner(base_url="http://localhost:1337")

    def _make_case(self, route="simple"):
        return EvalCase(
            id="test",
            input="how much disk space?",
            scorer="route_check",
            expect={"route": route},
            route=route,
        )

    def _make_suite(self):
        from beigebox.eval.models import EvalSuite
        return EvalSuite(name="test", cases=[], model="")

    def test_route_check_calls_route_check_endpoint(self):
        import httpx
        runner = self._make_runner()
        case = self._make_case("simple")
        suite = self._make_suite()

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"route": "simple", "model": "llama3.2:3b"}

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            result = runner._run_case(case, suite, "http://localhost:1337", "run1")

        call_url = mock_post.call_args[0][0]
        assert "/api/v1/route-check" in call_url
        assert result.passed is True
        assert result.score == 1.0

    def test_route_check_does_not_call_chat_completions(self):
        runner = self._make_runner()
        case = self._make_case("simple")
        suite = self._make_suite()

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"route": "simple", "model": ""}

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            runner._run_case(case, suite, "http://localhost:1337", "run1")

        call_url = mock_post.call_args[0][0]
        assert "/v1/chat/completions" not in call_url

    def test_wrong_route_fails(self):
        runner = self._make_runner()
        case = self._make_case("simple")
        suite = self._make_suite()

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"route": "complex", "model": ""}

        with patch("httpx.post", return_value=mock_resp):
            result = runner._run_case(case, suite, "http://localhost:1337", "run1")

        assert result.passed is False
        assert result.score == 0.0

    def test_network_error_fails_gracefully(self):
        runner = self._make_runner()
        case = self._make_case("simple")
        suite = self._make_suite()

        with patch("httpx.post", side_effect=Exception("connection refused")):
            result = runner._run_case(case, suite, "http://localhost:1337", "run1")

        assert result.passed is False
        assert "connection refused" in result.error


# ---------------------------------------------------------------------------
# EmbeddingDecision — route field
# ---------------------------------------------------------------------------

class TestEmbeddingDecisionRouteField:
    def test_route_field_exists(self):
        from beigebox.agents.embedding_classifier import EmbeddingDecision
        d = EmbeddingDecision(tier="simple", route="code")
        assert d.route == "code"

    def test_route_defaults_empty(self):
        from beigebox.agents.embedding_classifier import EmbeddingDecision
        d = EmbeddingDecision()
        assert d.route == ""
