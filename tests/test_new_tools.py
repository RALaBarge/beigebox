"""
Tests for calculator, datetime, system_info, and memory tools.
"""

import pytest
from beigebox.tools.calculator import CalculatorTool
from beigebox.tools.datetime_tool import DateTimeTool
from beigebox.tools.system_info import SystemInfoTool
from beigebox.tools.memory import MemoryTool


class TestCalculator:
    def test_basic_addition(self):
        calc = CalculatorTool()
        result = calc.run("2 + 3")
        assert "5" in result

    def test_multiplication(self):
        calc = CalculatorTool()
        result = calc.run("7 * 8")
        assert "56" in result

    def test_exponentiation(self):
        calc = CalculatorTool()
        result = calc.run("2 ** 16")
        assert "65536" in result

    def test_caret_as_power(self):
        calc = CalculatorTool()
        result = calc.run("2^10")
        assert "1024" in result

    def test_division(self):
        calc = CalculatorTool()
        result = calc.run("100 / 4")
        assert "25" in result

    def test_parentheses(self):
        calc = CalculatorTool()
        result = calc.run("(3 + 4) * 2")
        assert "14" in result

    def test_invalid_expression(self):
        calc = CalculatorTool()
        result = calc.run("hello world")
        assert "Could not evaluate" in result

    def test_division_by_zero(self):
        calc = CalculatorTool()
        result = calc.run("1 / 0")
        assert "Could not evaluate" in result


class TestDateTime:
    def test_returns_local_time(self):
        dt = DateTimeTool(local_tz_offset=-5.0)
        result = dt.run("what time is it")
        assert "Local time:" in result
        assert "UTC time:" in result

    def test_timezone_query(self):
        dt = DateTimeTool()
        result = dt.run("time in tokyo")
        assert "JST" in result or "TOKYO" in result

    def test_ann_arbor(self):
        dt = DateTimeTool()
        result = dt.run("what time in ann arbor")
        assert "ANN ARBOR" in result


class TestSystemInfo:
    def test_returns_something(self):
        si = SystemInfoTool()
        result = si.run("")
        # Should at least get CPU or disk info even in a container
        assert len(result) > 0


class TestMemory:
    def test_no_vector_store(self):
        mem = MemoryTool(vector_store=None)
        result = mem.run("test query")
        assert "unavailable" in result

    def test_with_mock_store(self):
        """Memory tool works with a mock vector store."""

        class MockVectorStore:
            def search(self, query, n_results=3, role_filter=None):
                return [{
                    "id": "test1",
                    "content": "We discussed Docker networking and port mapping.",
                    "metadata": {"role": "assistant", "model": "qwen3:32b", "conversation_id": "abc123", "timestamp": "2025-01-01"},
                    "distance": 0.2,  # score = 0.8
                }]

        mem = MemoryTool(vector_store=MockVectorStore(), max_results=3, min_score=0.3)
        result = mem.run("docker networking")
        assert "Docker networking" in result
        assert "ASSISTANT" in result

    def test_low_score_filtered(self):
        """Low-score results are filtered out."""

        class MockVectorStore:
            def search(self, query, n_results=3, role_filter=None):
                return [{
                    "id": "test1",
                    "content": "Irrelevant content.",
                    "metadata": {"role": "user", "model": "test", "conversation_id": "abc", "timestamp": ""},
                    "distance": 0.9,  # score = 0.1, below min_score
                }]

        mem = MemoryTool(vector_store=MockVectorStore(), min_score=0.3)
        result = mem.run("something")
        assert "No sufficiently relevant" in result
