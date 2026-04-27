"""Tests for the static skill (beigebox.skills.static).

Subprocess invocations are stubbed via ``asyncio.create_subprocess_exec``
monkey-patching, so the tests run network-free and don't require ruff /
semgrep to be installed in the CI environment.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from beigebox.skills.static import run_static
from beigebox.skills.static.runners import (
    _classify_ruff,
    _classify_semgrep,
    run_ruff,
    run_semgrep,
)


# ---------------------------------------------------------------------------
# Severity / type classification
# ---------------------------------------------------------------------------


def test_classify_ruff_syntax_error_is_high():
    assert _classify_ruff("E9") == ("high", "logic_error")
    assert _classify_ruff("E999") == ("high", "logic_error")


def test_classify_ruff_security_default_medium():
    # An S-rule not in the high-severity bump list defaults to medium/security.
    assert _classify_ruff("S101") == ("medium", "security")


def test_classify_ruff_security_high_bump():
    # eval, pickle, exec, weak crypto, etc. bump to high.
    assert _classify_ruff("S301") == ("high", "security")  # pickle
    assert _classify_ruff("S307") == ("high", "security")  # eval
    assert _classify_ruff("S102") == ("high", "security")  # exec
    assert _classify_ruff("S602") == ("high", "security")  # subprocess shell=True


def test_classify_ruff_pyflakes_medium_logic():
    assert _classify_ruff("F401") == ("medium", "logic_error")
    assert _classify_ruff("F841") == ("medium", "logic_error")


def test_classify_ruff_style_low():
    assert _classify_ruff("UP006") == ("low", "style")
    assert _classify_ruff("E501") == ("low", "style")


def test_classify_ruff_unknown_falls_through_to_other():
    assert _classify_ruff("ZZ999") == ("low", "other")
    assert _classify_ruff("") == ("low", "other")


def test_classify_semgrep_severity_map():
    assert _classify_semgrep("ERROR", {"category": "security"}) == ("high", "security")
    assert _classify_semgrep("WARNING", {"category": "correctness"}) == ("medium", "logic_error")
    assert _classify_semgrep("INFO", {"category": "performance"}) == ("low", "resource_leak")


def test_classify_semgrep_unknown_severity_defaults_low():
    assert _classify_semgrep("WHAT", {}) == ("low", "other")


# ---------------------------------------------------------------------------
# run_ruff — subprocess stubbed
# ---------------------------------------------------------------------------


class _FakeProc:
    def __init__(self, stdout: bytes, stderr: bytes = b""):
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        return self._stdout, self._stderr


def _patch_subprocess(monkeypatch, target_module: str, stdout: bytes, stderr: bytes = b""):
    """Patch ``asyncio.create_subprocess_exec`` inside the runners module."""
    async def _fake_create(*args, **kwargs):
        return _FakeProc(stdout, stderr)
    monkeypatch.setattr(f"{target_module}.asyncio.create_subprocess_exec", _fake_create)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "a.py").write_text("import os\n")
    return tmp_path


@pytest.mark.asyncio
async def test_run_ruff_parses_findings(monkeypatch, repo):
    payload = json.dumps([
        {
            "code": "S301",
            "filename": str(repo / "a.py"),
            "location": {"row": 5, "column": 8},
            "message": "Use of pickle is unsafe",
            "url": "https://example/S301",
        },
        {
            "code": "F401",
            "filename": str(repo / "a.py"),
            "location": {"row": 1, "column": 1},
            "message": "'os' imported but unused",
            "url": "",
        },
    ]).encode()
    _patch_subprocess(monkeypatch, "beigebox.skills.static.runners", payload)

    result = await run_ruff(repo)
    assert result["error"] is None
    assert len(result["findings"]) == 2
    s301 = next(f for f in result["findings"] if f["rule_id"] == "S301")
    assert s301["severity"] == "high"
    assert s301["type"] == "security"
    f401 = next(f for f in result["findings"] if f["rule_id"] == "F401")
    assert f401["severity"] == "medium"
    assert f401["type"] == "logic_error"


@pytest.mark.asyncio
async def test_run_ruff_empty_output_zero_findings(monkeypatch, repo):
    _patch_subprocess(monkeypatch, "beigebox.skills.static.runners", b"")
    result = await run_ruff(repo)
    assert result["error"] is None
    assert result["findings"] == []


@pytest.mark.asyncio
async def test_run_ruff_bad_json_returns_error(monkeypatch, repo):
    _patch_subprocess(monkeypatch, "beigebox.skills.static.runners", b"not json", b"some stderr")
    result = await run_ruff(repo)
    assert result["error"] is not None
    assert "json parse failed" in result["error"]
    assert result["findings"] == []


@pytest.mark.asyncio
async def test_run_ruff_missing_binary_returns_error(monkeypatch, repo):
    monkeypatch.setattr("beigebox.skills.static.runners.shutil.which", lambda _: None)
    result = await run_ruff(repo)
    assert result["error"] is not None
    assert "ruff" in result["error"]


# ---------------------------------------------------------------------------
# run_semgrep — subprocess stubbed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_semgrep_parses_findings(monkeypatch, repo):
    payload = json.dumps({
        "results": [
            {
                "check_id": "python.lang.security.audit.eval-detected.eval-detected",
                "path": str(repo / "a.py"),
                "start": {"line": 10, "col": 4},
                "end": {"line": 10, "col": 12},
                "extra": {
                    "severity": "ERROR",
                    "message": "Detected use of eval(); RCE risk on untrusted input.",
                    "metadata": {
                        "category": "security",
                        "cwe": "CWE-95",
                        "references": ["https://owasp.org/eval"],
                    },
                },
            },
            {
                "check_id": "python.flask.best-practice.use-blueprint",
                "path": str(repo / "a.py"),
                "start": {"line": 22, "col": 1},
                "end": {"line": 22, "col": 12},
                "extra": {
                    "severity": "INFO",
                    "message": "Prefer Blueprint",
                    "metadata": {"category": "best-practice"},
                },
            },
        ],
        "errors": [],
    }).encode()
    _patch_subprocess(monkeypatch, "beigebox.skills.static.runners", payload)

    result = await run_semgrep(repo)
    assert result["error"] is None
    assert len(result["findings"]) == 2
    high = next(f for f in result["findings"] if f["severity"] == "high")
    assert high["type"] == "security"
    assert "eval-detected" in high["rule_id"]
    low = next(f for f in result["findings"] if f["severity"] == "low")
    assert low["type"] == "logic_error"


@pytest.mark.asyncio
async def test_run_semgrep_missing_binary_returns_error(monkeypatch, repo):
    monkeypatch.setattr("beigebox.skills.static.runners.shutil.which", lambda _: None)
    result = await run_semgrep(repo)
    assert result["error"] is not None


# ---------------------------------------------------------------------------
# run_static — full pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_static_merges_and_sorts(monkeypatch, repo):
    """Findings from both runners are merged and sorted by severity."""
    ruff_payload = json.dumps([
        {
            "code": "S301",
            "filename": str(repo / "a.py"),
            "location": {"row": 5, "column": 8},
            "message": "pickle is unsafe",
            "url": "",
        },
        {
            "code": "F401",
            "filename": str(repo / "a.py"),
            "location": {"row": 1, "column": 1},
            "message": "unused import",
            "url": "",
        },
    ]).encode()
    semgrep_payload = json.dumps({
        "results": [
            {
                "check_id": "python.eval-detected",
                "path": str(repo / "a.py"),
                "start": {"line": 30, "col": 1},
                "end": {"line": 30, "col": 10},
                "extra": {
                    "severity": "ERROR",
                    "message": "eval is dangerous",
                    "metadata": {"category": "security"},
                },
            },
        ],
        "errors": [],
    }).encode()

    # Patch one async call that returns ruff payload first, then semgrep
    # payload — order depends on how run_static schedules. We can patch each
    # runner directly instead.
    async def fake_run_ruff(*a, **kw):
        return {
            "findings": [
                {"tool": "ruff", "rule_id": "S301", "severity": "high", "type": "security",
                 "file": str(repo / "a.py"), "line": 5, "column": 8,
                 "message": "pickle is unsafe", "url": ""},
                {"tool": "ruff", "rule_id": "F401", "severity": "medium", "type": "logic_error",
                 "file": str(repo / "a.py"), "line": 1, "column": 1,
                 "message": "unused import", "url": ""},
            ],
            "stats": {"duration_seconds": 0.1, "raw_count": 2},
            "error": None,
        }

    async def fake_run_semgrep(*a, **kw):
        return {
            "findings": [
                {"tool": "semgrep", "rule_id": "python.eval-detected", "severity": "high", "type": "security",
                 "file": str(repo / "a.py"), "line": 30, "column": 1,
                 "message": "eval is dangerous", "url": ""},
            ],
            "stats": {"duration_seconds": 1.2, "raw_count": 1},
            "error": None,
        }

    monkeypatch.setattr("beigebox.skills.static.pipeline.run_ruff", fake_run_ruff)
    monkeypatch.setattr("beigebox.skills.static.pipeline.run_semgrep", fake_run_semgrep)

    result = await run_static(repo)
    assert result["stats"]["total_findings"] == 3
    assert result["stats"]["ruff_count"] == 2
    assert result["stats"]["semgrep_count"] == 1
    # First two are 'high', third is 'medium'
    sevs = [f["severity"] for f in result["findings"]]
    assert sevs == ["high", "high", "medium"]


@pytest.mark.asyncio
async def test_run_static_runner_error_does_not_kill_other(monkeypatch, repo):
    async def boom(*a, **kw):
        raise RuntimeError("ruff blew up")

    async def fake_semgrep(*a, **kw):
        return {
            "findings": [{"tool": "semgrep", "rule_id": "x", "severity": "low",
                         "type": "other", "file": str(repo / "a.py"),
                         "line": 1, "column": 1, "message": "x", "url": ""}],
            "stats": {"duration_seconds": 0.5, "raw_count": 1},
            "error": None,
        }

    monkeypatch.setattr("beigebox.skills.static.pipeline.run_ruff", boom)
    monkeypatch.setattr("beigebox.skills.static.pipeline.run_semgrep", fake_semgrep)

    result = await run_static(repo)
    assert result["stats"]["ruff_error"] is not None
    assert "RuntimeError" in result["stats"]["ruff_error"]
    assert result["stats"]["total_findings"] == 1  # the semgrep finding survived


@pytest.mark.asyncio
async def test_run_static_finding_shape(monkeypatch, repo):
    async def fake_run_ruff(*a, **kw):
        return {
            "findings": [{
                "tool": "ruff", "rule_id": "S301", "severity": "high", "type": "security",
                "file": str(repo / "a.py"), "line": 5, "column": 8,
                "message": "pickle is unsafe", "url": "https://example",
            }],
            "stats": {"duration_seconds": 0.1, "raw_count": 1},
            "error": None,
        }

    async def fake_run_semgrep(*a, **kw):
        return {"findings": [], "stats": {"duration_seconds": 0.0, "raw_count": 0}, "error": None}

    monkeypatch.setattr("beigebox.skills.static.pipeline.run_ruff", fake_run_ruff)
    monkeypatch.setattr("beigebox.skills.static.pipeline.run_semgrep", fake_run_semgrep)

    result = await run_static(repo)
    assert result["findings"][0]["finding_id"].startswith("static_")
    f = result["findings"][0]
    # Garlicpress-shape fields
    assert set(f).issuperset({
        "finding_id", "severity", "type", "location",
        "description", "evidence", "traceability", "static_meta",
    })
    assert f["traceability"]["file"] == "a.py"  # made relative to repo
    assert f["location"] == "a.py:5"
    assert "S301" in f["description"]
    assert f["static_meta"]["tool"] == "ruff"


@pytest.mark.asyncio
async def test_run_static_dedupe_same_tool_same_rule_same_loc(monkeypatch, repo):
    async def fake_run_ruff(*a, **kw):
        # Same finding twice — should dedupe.
        return {
            "findings": [
                {"tool": "ruff", "rule_id": "S301", "severity": "high", "type": "security",
                 "file": str(repo / "a.py"), "line": 5, "column": 8, "message": "x", "url": ""},
                {"tool": "ruff", "rule_id": "S301", "severity": "high", "type": "security",
                 "file": str(repo / "a.py"), "line": 5, "column": 8, "message": "x", "url": ""},
            ],
            "stats": {"duration_seconds": 0.1, "raw_count": 2},
            "error": None,
        }

    async def fake_run_semgrep(*a, **kw):
        return {"findings": [], "stats": {"duration_seconds": 0.0, "raw_count": 0}, "error": None}

    monkeypatch.setattr("beigebox.skills.static.pipeline.run_ruff", fake_run_ruff)
    monkeypatch.setattr("beigebox.skills.static.pipeline.run_semgrep", fake_run_semgrep)

    result = await run_static(repo)
    assert result["stats"]["total_findings"] == 1


@pytest.mark.asyncio
async def test_run_static_disabled_runners_returns_empty():
    result = await run_static("/tmp", enable_ruff=False, enable_semgrep=False)
    assert result["stats"]["total_findings"] == 0
    assert result["raw_results"] == {}
