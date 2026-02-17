"""
Tests for the hooks system.
"""

import pytest
import tempfile
from pathlib import Path
from beigebox.hooks import HookManager


@pytest.fixture
def hooks_dir(tmp_path):
    """Create a temp hooks directory with test hooks."""
    # Hook that uppercases the model name
    (tmp_path / "test_hook.py").write_text('''
def pre_request(body, context):
    model = body.get("model", "")
    if model:
        body["model"] = model.upper()
    return body

def post_response(body, response, context):
    response["_hook_applied"] = True
    return response
''')

    # Hook with only pre_request
    (tmp_path / "pre_only.py").write_text('''
def pre_request(body, context):
    body["_pre_only"] = True
    return body
''')

    # Broken hook that raises
    (tmp_path / "broken.py").write_text('''
def pre_request(body, context):
    raise ValueError("intentional error")
    return body
''')

    return str(tmp_path)


def test_load_hooks_from_directory(hooks_dir):
    """Hooks are loaded from a directory."""
    mgr = HookManager(hooks_dir=hooks_dir)
    names = mgr.list_hooks()
    assert "broken" in names
    assert "pre_only" in names
    assert "test_hook" in names


def test_pre_request_hooks(hooks_dir):
    """Pre-request hooks modify the body."""
    mgr = HookManager(hooks_dir=hooks_dir)
    body = {"model": "qwen3:32b", "messages": []}
    result = mgr.run_pre_request(body, {})
    # test_hook uppercases model, pre_only adds _pre_only
    assert result["model"] == "QWEN3:32B"
    assert result.get("_pre_only") is True


def test_broken_hook_doesnt_break_pipeline(hooks_dir):
    """A broken hook is skipped, others still run."""
    mgr = HookManager(hooks_dir=hooks_dir)
    body = {"model": "test", "messages": []}
    # broken.py raises, but pre_only and test_hook should still run
    result = mgr.run_pre_request(body, {})
    assert result.get("_pre_only") is True


def test_post_response_hooks(hooks_dir):
    """Post-response hooks modify the response."""
    mgr = HookManager(hooks_dir=hooks_dir)
    body = {"model": "test"}
    response = {"choices": []}
    result = mgr.run_post_response(body, response, {})
    assert result.get("_hook_applied") is True


def test_empty_hooks_dir():
    """No hooks loaded from nonexistent directory."""
    mgr = HookManager(hooks_dir="/nonexistent/path")
    assert mgr.list_hooks() == []
    # Should be a no-op
    body = {"model": "test"}
    assert mgr.run_pre_request(body, {}) == body


def test_skips_underscore_files(tmp_path):
    """Files starting with _ are skipped."""
    (tmp_path / "__init__.py").write_text("# skip")
    (tmp_path / "_private.py").write_text("def pre_request(b, c): return b")
    (tmp_path / "real.py").write_text("def pre_request(b, c): b['real'] = True; return b")

    mgr = HookManager(hooks_dir=str(tmp_path))
    assert "real" in mgr.list_hooks()
    assert "_private" not in mgr.list_hooks()
    assert "__init__" not in mgr.list_hooks()
