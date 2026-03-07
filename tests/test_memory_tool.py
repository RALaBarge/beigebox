"""
Tests for beigebox/tools/memory.py — MemoryTool with query preprocessing.
"""

import pytest
from unittest.mock import MagicMock, patch

from beigebox.tools.memory import MemoryTool


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _mock_vs(results=None):
    vs = MagicMock()
    vs.search.return_value = results or []
    return vs


def _hit(content="some content", distance=0.2, role="user", model="llama3.2:3b"):
    return {"content": content, "distance": distance, "metadata": {"role": role, "model": model}}


# ── Basic run ─────────────────────────────────────────────────────────────────

class TestMemoryToolRun:
    def test_no_vector_store_returns_unavailable(self):
        tool = MemoryTool(vector_store=None)
        result = tool.run("anything")
        assert "unavailable" in result.lower()

    def test_no_results_returns_not_found(self):
        tool = MemoryTool(vector_store=_mock_vs([]))
        result = tool.run("tell me about docker")
        assert "No relevant" in result

    def test_results_below_min_score_filtered(self):
        # distance=0.9 → score=0.1, below default min_score=0.3
        vs = _mock_vs([_hit(distance=0.9)])
        tool = MemoryTool(vector_store=vs, min_score=0.3)
        result = tool.run("query")
        assert "No sufficiently" in result

    def test_results_above_min_score_shown(self):
        vs = _mock_vs([_hit(content="docker networking trick", distance=0.1)])
        tool = MemoryTool(vector_store=vs, min_score=0.3)
        result = tool.run("docker")
        assert "docker networking trick" in result

    def test_long_content_truncated(self):
        long_content = "x" * 500
        vs = _mock_vs([_hit(content=long_content, distance=0.1)])
        tool = MemoryTool(vector_store=vs, min_score=0.0)
        result = tool.run("q")
        assert "..." in result

    def test_max_results_passed_to_search(self):
        vs = _mock_vs([])
        tool = MemoryTool(vector_store=vs, max_results=7)
        tool.run("q")
        vs.search.assert_called_once_with("q", n_results=7)

    def test_search_exception_returns_error_message(self):
        vs = MagicMock()
        vs.search.side_effect = RuntimeError("db exploded")
        tool = MemoryTool(vector_store=vs)
        result = tool.run("q")
        assert "failed" in result.lower()

    def test_empty_query_passes_through(self):
        vs = _mock_vs([])
        tool = MemoryTool(vector_store=vs)
        tool.run("")
        vs.search.assert_called_once()


# ── Preprocess disabled ───────────────────────────────────────────────────────

class TestPreprocessDisabled:
    def test_disabled_by_default(self):
        vs = _mock_vs([])
        tool = MemoryTool(vector_store=vs)
        assert tool.query_preprocess is False

    def test_disabled_raw_query_used(self):
        vs = _mock_vs([])
        tool = MemoryTool(vector_store=vs, query_preprocess=False)
        tool.run("what did we decide about the database")
        vs.search.assert_called_once_with("what did we decide about the database", n_results=3)

    def test_no_model_disables_preprocess(self):
        vs = _mock_vs([])
        tool = MemoryTool(vector_store=vs, query_preprocess=True, query_preprocess_model="")
        assert tool.query_preprocess is False


# ── Preprocess enabled ────────────────────────────────────────────────────────

class TestPreprocessEnabled:
    def _tool_with_preprocess(self, mock_response="database, decision, schema"):
        import httpx
        vs = _mock_vs([])
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": mock_response}}]
        }
        with patch("beigebox.tools.memory.httpx.post", return_value=mock_resp):
            tool = MemoryTool(
                vector_store=vs,
                query_preprocess=True,
                query_preprocess_model="llama3.2:3b",
                backend_url="http://localhost:11434",
            )
        return tool, vs, mock_resp

    def test_preprocess_true_when_model_set(self):
        vs = _mock_vs([])
        tool = MemoryTool(
            vector_store=vs,
            query_preprocess=True,
            query_preprocess_model="llama3.2:3b",
        )
        assert tool.query_preprocess is True

    def test_preprocessed_query_used_for_search(self):
        vs = _mock_vs([])
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "database, decision, schema"}}]
        }
        tool = MemoryTool(
            vector_store=vs,
            query_preprocess=True,
            query_preprocess_model="llama3.2:3b",
        )
        with patch("beigebox.tools.memory.httpx.post", return_value=mock_resp):
            tool.run("what did we decide about the database last week")
        vs.search.assert_called_once_with("database, decision, schema", n_results=3)

    def test_fallback_to_raw_query_on_http_error(self):
        vs = _mock_vs([])
        tool = MemoryTool(
            vector_store=vs,
            query_preprocess=True,
            query_preprocess_model="llama3.2:3b",
        )
        with patch("beigebox.tools.memory.httpx.post", side_effect=Exception("timeout")):
            tool.run("original query")
        vs.search.assert_called_once_with("original query", n_results=3)

    def test_fallback_on_empty_keywords(self):
        vs = _mock_vs([])
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": ""}}]
        }
        tool = MemoryTool(
            vector_store=vs,
            query_preprocess=True,
            query_preprocess_model="llama3.2:3b",
        )
        with patch("beigebox.tools.memory.httpx.post", return_value=mock_resp):
            tool.run("original query")
        vs.search.assert_called_once_with("original query", n_results=3)
