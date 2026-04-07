"""Tests for the eval framework (models, scorers, runner load)."""
import json
import pytest
from pathlib import Path

from beigebox.eval.models import EvalCase, EvalResult, EvalSuite
from beigebox.eval.scorer import (
    score_contains,
    score_exact,
    score_regex,
    score_not_contains,
    SCORERS,
)
from beigebox.eval.runner import EvalRunner


# ── Scorers ───────────────────────────────────────────────────────────────

class TestScoreContains:
    def test_all_present(self):
        p, s, _ = score_contains("Hello world", {"contains": ["hello", "world"]})
        assert p and s == 1.0

    def test_partial(self):
        p, s, _ = score_contains("Hello world", {"contains": ["hello", "missing"]})
        assert not p and s == 0.5

    def test_none_present(self):
        p, s, _ = score_contains("Hello world", {"contains": ["foo", "bar"]})
        assert not p and s == 0.0

    def test_case_insensitive(self):
        p, s, _ = score_contains("HELLO WORLD", {"contains": ["hello"]})
        assert p

    def test_no_criteria(self):
        p, s, _ = score_contains("anything", {})
        assert p and s == 1.0


class TestScoreExact:
    def test_match(self):
        p, s, _ = score_exact("  hello  ", {"exact": "hello"})
        assert p and s == 1.0

    def test_mismatch(self):
        p, s, _ = score_exact("hello world", {"exact": "hello"})
        assert not p and s == 0.0


class TestScoreRegex:
    def test_match(self):
        p, s, _ = score_regex('{"status": "ok"}', {"regex": r'"status"\s*:\s*"ok"'})
        assert p and s == 1.0

    def test_no_match(self):
        p, s, _ = score_regex("plain text", {"regex": r'"status"\s*:\s*"ok"'})
        assert not p and s == 0.0

    def test_no_pattern(self):
        p, s, _ = score_regex("anything", {})
        assert p and s == 1.0

    def test_invalid_regex(self):
        p, s, reason = score_regex("text", {"regex": "[invalid"})
        assert not p and "invalid regex" in reason


class TestScoreNotContains:
    def test_none_found(self):
        p, s, _ = score_not_contains("clean text", {"not_contains": ["bad", "evil"]})
        assert p and s == 1.0

    def test_one_found(self):
        p, s, _ = score_not_contains("bad text", {"not_contains": ["bad", "evil"]})
        assert not p and s == 0.5

    def test_all_found(self):
        p, s, _ = score_not_contains("bad evil text", {"not_contains": ["bad", "evil"]})
        assert not p and s == 0.0

    def test_no_criteria(self):
        p, s, _ = score_not_contains("anything", {})
        assert p and s == 1.0


class TestScorerRegistry:
    def test_all_scorers_callable(self):
        for name, fn in SCORERS.items():
            assert callable(fn), f"{name} not callable"

    def test_expected_keys(self):
        assert "contains" in SCORERS
        assert "exact" in SCORERS
        assert "regex" in SCORERS
        assert "not_contains" in SCORERS


# ── Suite loading ─────────────────────────────────────────────────────────

class TestLoadSuite:
    def test_load_json(self, tmp_path):
        data = {
            "name": "test",
            "model": "qwen3:4b",
            "cases": [
                {"id": "c1", "input": "hello", "scorer": "contains", "expect": {"contains": ["hi"]}}
            ],
        }
        p = tmp_path / "suite.json"
        p.write_text(json.dumps(data))
        suite = EvalRunner.load_suite(p)
        assert suite.name == "test"
        assert len(suite.cases) == 1
        assert suite.cases[0].id == "c1"
        assert suite.model == "qwen3:4b"

    def test_load_yaml(self, tmp_path):
        pytest.importorskip("yaml")
        text = "name: yaml-suite\ncases:\n  - id: c1\n    input: hello\n    scorer: contains\n    expect:\n      contains:\n        - hi\n"
        p = tmp_path / "suite.yaml"
        p.write_text(text)
        suite = EvalRunner.load_suite(p)
        assert suite.name == "yaml-suite"
        assert suite.cases[0].scorer == "contains"

    def test_defaults(self, tmp_path):
        data = {"cases": [{"id": "x", "input": "hi"}]}
        p = tmp_path / "s.json"
        p.write_text(json.dumps(data))
        suite = EvalRunner.load_suite(p)
        assert suite.cases[0].scorer == "contains"
        assert suite.cases[0].model == ""


# ── EvalResult dataclass ──────────────────────────────────────────────────

def test_eval_result_fields():
    r = EvalResult(
        case_id="x", input="hi", output="hello",
        passed=True, score=1.0, scorer="contains",
        model="qwen3:4b", latency_ms=42.0, run_id="abc123",
    )
    assert r.passed
    assert r.score == 1.0
    assert r.error == ""
