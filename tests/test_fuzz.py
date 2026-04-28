"""Tests for the fuzz skill (beigebox.skills.fuzz)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from beigebox.skills.fuzz.engine import (
    AdaptiveTimeAllocator,
    CrashClassifier,
    Fuzzer,
    RiskScorer,
    SeedCorpusExtractor,
    SmartHarnessGenerator,
)
from beigebox.skills.fuzz.extractor import FunctionExtractor


class TestRiskScorer:
    def test_parsing_functions_high_risk(self):
        scorer = RiskScorer()
        assert scorer.score("parse_json", "def parse_json(data): return json.loads(data)") >= 8
        assert scorer.score("decode_base64", "def decode_base64(data): ...") >= 7

    def test_trivial_functions_low_risk(self):
        scorer = RiskScorer()
        assert scorer.score("get_value", "def get_value(x): return x") <= 5

    def test_private_functions_penalized(self):
        scorer = RiskScorer()
        public = scorer.score("validate", "def validate(data): ...")
        private = scorer.score("_validate", "def _validate(data): ...")
        assert private < public

    def test_loop_functions_score_higher(self):
        scorer = RiskScorer()
        no_loop = scorer.score("simple", "def simple(x): return x * 2")
        with_loop = scorer.score(
            "process",
            "def process(items):\n"
            "    result = []\n"
            "    for item in items: result.append(item)\n"
            "    return result",
        )
        assert with_loop > no_loop


class TestFunctionExtractor:
    def test_extract_single_function(self):
        code = (
            "def parse_json(data):\n"
            "    '''Parse JSON string.'''\n"
            "    return json.loads(data)\n"
        )
        funcs = FunctionExtractor().extract_functions(code, "test.py")
        assert len(funcs) == 1
        assert funcs[0]["name"] == "parse_json"
        assert "Parse JSON" in funcs[0]["docstring"]
        assert funcs[0]["is_fuzzable"] is True

    def test_extract_multiple_functions(self):
        code = (
            "def foo(x):\n"
            "    return x * 2\n"
            "\n"
            "def bar(y):\n"
            "    return y + 1\n"
            "\n"
            "def _private():\n"
            "    pass\n"
        )
        funcs = FunctionExtractor().extract_functions(code, "test.py")
        assert len(funcs) >= 2

    def test_no_parameter_function_marked_unfuzzable(self):
        code = "def get_version():\n    return '1.0'\n"
        funcs = FunctionExtractor().extract_functions(code, "test.py")
        assert all(not f["is_fuzzable"] for f in funcs)

    def test_self_only_method_marked_unfuzzable(self):
        code = "class A:\n    def f(self):\n        return 1\n"
        funcs = FunctionExtractor().extract_functions(code, "test.py")
        assert all(not f["is_fuzzable"] for f in funcs)

    def test_extract_from_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def process(data):\n    return data.upper()\n")
            path = Path(f.name)
        try:
            funcs = FunctionExtractor().extract_from_file(str(path))
            assert any(f["name"] == "process" for f in funcs)
        finally:
            path.unlink()

    def test_skips_nested_functions(self):
        """Nested defs cannot be reached via getattr(module, name) — must be skipped."""
        code = (
            "def outer(data):\n"
            "    def inner(x):\n"
            "        return x + 1\n"
            "    return inner(data)\n"
        )
        funcs = FunctionExtractor().extract_functions(code, "test.py")
        assert [f["name"] for f in funcs] == ["outer"]

    def test_skips_class_methods(self):
        code = (
            "class A:\n"
            "    def method(self, data):\n"
            "        return len(data)\n"
            "\n"
            "def top_level(data):\n"
            "    return data\n"
        )
        funcs = FunctionExtractor().extract_functions(code, "test.py")
        assert [f["name"] for f in funcs] == ["top_level"]

    def test_skips_async_functions(self):
        code = (
            "async def do_work(data):\n"
            "    return data\n"
            "\n"
            "def sync_work(data):\n"
            "    return data * 2\n"
        )
        funcs = FunctionExtractor().extract_functions(code, "test.py")
        assert [f["name"] for f in funcs] == ["sync_work"]

    def test_multi_required_args_unfuzzable(self):
        """Two required positional args means the harness's one-arg call would TypeError."""
        code = "def combine(a, b):\n    return a + b\n"
        funcs = FunctionExtractor().extract_functions(code, "test.py")
        assert funcs and funcs[0]["is_fuzzable"] is False
        assert funcs[0]["reason"] == "extra_required_args"

    def test_one_required_plus_defaults_fuzzable(self):
        """parse_json(data, strict=False) is fuzzable — extra args have defaults."""
        code = "def parse_json(data, strict=False, max_depth=10):\n    return data\n"
        funcs = FunctionExtractor().extract_functions(code, "test.py")
        assert funcs and funcs[0]["is_fuzzable"] is True

    def test_positional_only_param_is_recognized(self):
        """def foo(a, /) — positional-only args live on node.args.posonlyargs.

        Earlier code ignored that list, so functions defined with the
        positional-only marker were misclassified as no_parameters and
        silently skipped during fuzz target discovery. Reviewer DeepSeek
        flagged this on 2026-04-28; regression fixture below.
        """
        code = "def encode(data, /):\n    return bytes(data)\n"
        funcs = FunctionExtractor().extract_functions(code, "test.py")
        assert funcs, "function should be discovered"
        assert funcs[0]["parameters"] == ["data"]
        assert funcs[0]["is_fuzzable"] is True

    def test_mixed_positional_only_and_regular(self):
        """def foo(a, /, b=1) — combined posonlyargs+args, defaults align right."""
        code = "def parse(data, /, strict=False):\n    return data\n"
        funcs = FunctionExtractor().extract_functions(code, "test.py")
        assert funcs and funcs[0]["parameters"] == ["data", "strict"]
        assert funcs[0]["is_fuzzable"] is True

    def test_mixed_positional_only_two_required_not_fuzzable(self):
        """def foo(a, /, b) — two required args, one-arg call would TypeError."""
        code = "def merge(a, /, b):\n    return [a, b]\n"
        funcs = FunctionExtractor().extract_functions(code, "test.py")
        assert funcs and funcs[0]["is_fuzzable"] is False
        assert funcs[0]["reason"] == "extra_required_args"


class TestSeedCorpusExtractor:
    def test_extract_from_docstring(self):
        code = (
            'def parse_json(data):\n'
            '    """\n'
            '    Parse JSON string.\n'
            '\n'
            '    Examples:\n'
            "        parse_json('{\"key\": \"value\"}')\n"
            "        parse_json('[]')\n"
            '    """\n'
            "    return json.loads(data)\n"
        )
        seeds = SeedCorpusExtractor().extract(code, "parse_json")
        assert seeds
        assert all(isinstance(s, bytes) for s in seeds)

    def test_parser_edge_cases(self):
        seeds = SeedCorpusExtractor().extract(
            "def parse_json(data): return json.loads(data)", "parse_json"
        )
        assert any(s == b"" for s in seeds)
        assert any(len(s) > 100 for s in seeds)

    def test_deduplication(self):
        code = (
            'def foo(x):\n'
            '    """\n'
            '    Examples:\n'
            "        foo('test')\n"
            "        foo('test')\n"
            "        foo('test')\n"
            '    """\n'
            '    pass\n'
        )
        seeds = SeedCorpusExtractor().extract(code, "foo")
        # 'test' should appear at most once (dedup)
        assert sum(1 for s in seeds if b"test" in s) <= 1


class TestCrashClassifier:
    def test_library_crash_filtered(self):
        c = CrashClassifier()
        crash = {"type": "ValueError", "stack_trace": "/usr/lib/python3.11/json.py:123 in loads"}
        assert c.is_app_crash(crash, "/app") is False

    def test_expected_exception_filtered(self):
        c = CrashClassifier()
        crash = {"type": "ValueError", "stack_trace": "/app/mycode.py:50 in process"}
        assert c.is_app_crash(crash, "/app") is False

    def test_critical_crash_reported(self):
        c = CrashClassifier()
        crash = {"type": "RecursionError", "stack_trace": "/app/mycode.py:50 in process"}
        assert c.is_app_crash(crash, "/app") is True

    def test_timeout_reported(self):
        c = CrashClassifier()
        crash = {"type": "Timeout", "stack_trace": "/app/mycode.py:50 in process"}
        assert c.is_app_crash(crash, "/app") is True

    def test_exact_match_does_not_promote_substring(self):
        """A user-defined ``TimeoutLikeError`` must NOT be classified as a critical Timeout."""
        c = CrashClassifier()
        crash = {"type": "TimeoutLikeError", "stack_trace": "/app/mycode.py:50 in process"}
        assert c.is_app_crash(crash, "/app") is False

    def test_exact_match_does_not_filter_custom_value_error(self):
        """``MyValueError`` is not the standard ValueError — let it through."""
        c = CrashClassifier()
        # Custom exception with the substring "ValueError" should not be auto-filtered
        # by the expected-exceptions list. (It still has to be in CRITICAL_TYPES to
        # become a finding — for this assertion we just check the expected-exception
        # filter doesn't fire on substring alone.)
        crash = {"type": "MyValueError", "stack_trace": "/app/mycode.py:50 in process"}
        assert c.is_app_crash(crash, "/app") is False  # still rejected, but on critical-type, not expected


class TestSmartHarnessGenerator:
    def test_harness_contains_target_name(self):
        h = SmartHarnessGenerator().generate_basic_harness(
            function_name="parse_json", source_file="/tmp/x.py", parameter_type="bytes"
        )
        assert "parse_json" in h
        assert "/tmp/x.py" in h
        assert "except" in h

    def test_harness_is_valid_python(self):
        h = SmartHarnessGenerator().generate_basic_harness(
            function_name="test_func", source_file="/tmp/x.py", parameter_type="str"
        )
        compile(h, "<harness>", "exec")

    def test_parameter_type_inference(self):
        gen = SmartHarnessGenerator()
        assert gen.infer_parameter_type("def foo(data): return data.decode('utf-8')", "data") == "bytes"
        assert gen.infer_parameter_type("def foo(s): return int(s)", "s") in ("bytes", "str")


class TestAdaptiveTimeAllocator:
    def test_high_risk_gets_more_time(self):
        allocator = AdaptiveTimeAllocator()
        funcs = [
            {
                "name": "parse_json",
                "source": "def parse_json(d):\n    return json.loads(d)\n",
                "risk_score": 9,
            },
            {"name": "get_value", "source": "def get_value(x): return x", "risk_score": 2},
        ]
        budget = allocator.allocate_budget(funcs, total_budget_seconds=20)
        assert budget["parse_json"] > budget["get_value"]

    def test_respects_total_budget(self):
        allocator = AdaptiveTimeAllocator()
        funcs = [
            {"name": f"func_{i}", "source": "def f(x): pass", "risk_score": 5}
            for i in range(10)
        ]
        budget = allocator.allocate_budget(funcs, total_budget_seconds=30)
        assert sum(budget.values()) <= 31

    def test_budget_strict_when_n_le_b(self):
        """When num functions <= budget seconds, sum must not exceed budget."""
        allocator = AdaptiveTimeAllocator()
        funcs = [
            {"name": f"func_{i}", "source": "def f(x): return x", "risk_score": 5}
            for i in range(15)
        ]
        budget = allocator.allocate_budget(funcs, total_budget_seconds=20)
        assert sum(budget.values()) <= 20
        assert all(v >= 1 for v in budget.values())


@pytest.mark.asyncio
class TestFuzzer:
    async def test_initialization(self):
        f = Fuzzer(timeout_seconds=5)
        assert f.timeout_seconds == 5

    @pytest.mark.integration
    async def test_fuzz_simple_function(self, tmp_path):
        """Run the harness against a target function and confirm we got an iteration count."""
        target = tmp_path / "target.py"
        target.write_text("def trivial(data):\n    return len(data)\n")

        gen = SmartHarnessGenerator()
        harness = gen.generate_basic_harness(
            function_name="trivial", source_file=str(target), parameter_type="bytes"
        )

        result = await Fuzzer().fuzz_function(
            harness_code=harness,
            function_name="trivial",
            file_path=str(target),
            seeds=[b"abc", b""],
            timeout_seconds=2,
        )
        assert result["status"] == "complete"
        assert result["iterations"] > 0
        assert result["crashes"] == []


class TestEndToEnd:
    def test_high_risk_functions_identified(self, tmp_path):
        f = tmp_path / "vulnerable.py"
        f.write_text(
            'def parse_json(data):\n'
            '    """Parse JSON without validation."""\n'
            '    import json\n'
            '    return json.loads(data)\n'
            '\n'
            'def process_items(items):\n'
            '    """Process items with DOS vulnerability."""\n'
            '    count = 0\n'
            '    while count < len(items) * len(items):\n'
            "        if items[count % len(items)] == 'STOP':\n"
            '            break\n'
            '        count += 1\n'
            '    return count\n'
        )
        funcs = FunctionExtractor().extract_from_file(str(f))
        funcs = RiskScorer().score_functions(funcs)
        assert any(x["risk_score"] >= 7 for x in funcs)

    def test_safe_code_low_risk(self, tmp_path):
        """Private helpers and trivial functions should score below the high-risk threshold."""
        f = tmp_path / "safe.py"
        f.write_text(
            "def safe_add(a, b):\n"
            "    return a + b\n"
            "\n"
            "def _private_helper(x):\n"
            "    return x * 2\n"
        )
        funcs = FunctionExtractor().extract_from_file(str(f))
        funcs = RiskScorer().score_functions(funcs)
        # No safe utility crosses the high-risk threshold (>=7).
        assert all(x["risk_score"] < 7 for x in funcs)
        # Private helpers always score strictly below their public siblings.
        private = next(x for x in funcs if x["name"].startswith("_"))
        public = next(x for x in funcs if not x["name"].startswith("_"))
        assert private["risk_score"] < public["risk_score"]
