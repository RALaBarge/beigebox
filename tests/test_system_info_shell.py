"""
Tests for operator shell.enabled gate in system_info._run().

We don't test actual shell execution (requires bwrap/busybox), just the
config-gate logic that blocks execution when shell.enabled=false.
"""
import pytest
from unittest.mock import patch, MagicMock


# ── Helper to build a config dict ────────────────────────────────────────────

def _cfg(shell_enabled: bool) -> dict:
    return {
        "operator": {
            "shell": {
                "enabled": shell_enabled,
                "allowed_commands": ["ls", "cat", "free", "nproc", "df", "ps",
                                     "grep", "head", "tail", "find", "cut",
                                     "uptime", "nvidia-smi"],
                "blocked_patterns": ["rm -rf", "sudo"],
            }
        }
    }


# ── Shell disabled gate ───────────────────────────────────────────────────────

class TestShellDisabledGate:
    def test_run_returns_empty_when_shell_disabled(self):
        from beigebox.tools.system_info import _run
        with patch("beigebox.config.get_config", return_value=_cfg(False)), \
             patch("beigebox.tools.system_info._bwrap_available", return_value=False), \
             patch("beigebox.tools.system_info.subprocess") as mock_sp:
            result = _run("ls /tmp")
        assert result == ""
        mock_sp.run.assert_not_called()

    def test_run_does_not_execute_when_shell_disabled(self):
        """Verify subprocess is never reached when shell.enabled=false."""
        from beigebox.tools.system_info import _run
        called = []
        with patch("beigebox.config.get_config", return_value=_cfg(False)):
            with patch("beigebox.tools.system_info.subprocess.run", side_effect=lambda *a, **kw: called.append(a)):
                _run("nproc")
        assert called == []

    def test_run_audit_logs_shell_disabled(self, caplog):
        import logging
        from beigebox.tools.system_info import _run
        with patch("beigebox.config.get_config", return_value=_cfg(False)), \
             caplog.at_level(logging.WARNING, logger="beigebox.tools.system_info"):
            _run("ls /tmp")
        assert any("shell_disabled_by_config" in r.message for r in caplog.records)

    def test_run_passes_through_when_shell_enabled(self):
        """When enabled, execution proceeds past the gate (into allowlist check)."""
        from beigebox.tools.system_info import _run
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "4"
        mock_result.stderr = ""
        with patch("beigebox.config.get_config", return_value=_cfg(True)), \
             patch("beigebox.tools.system_info._bwrap_available", return_value=False), \
             patch("beigebox.tools.system_info.subprocess.run", return_value=mock_result) as mock_sp:
            result = _run("nproc")
        mock_sp.assert_called_once()
        assert result == "4"

    def test_shell_enabled_true_allows_allowlisted_command(self):
        mock_result = MagicMock(returncode=0, stdout="ok", stderr="")
        with patch("beigebox.config.get_config", return_value=_cfg(True)), \
             patch("beigebox.tools.system_info._bwrap_available", return_value=False), \
             patch("beigebox.tools.system_info.subprocess.run", return_value=mock_result):
            from beigebox.tools.system_info import _run
            result = _run("free -h")
        assert result == "ok"

    def test_shell_enabled_false_blocks_even_allowlisted_command(self):
        with patch("beigebox.config.get_config", return_value=_cfg(False)):
            from beigebox.tools.system_info import _run
            result = _run("free -h")
        assert result == ""


# ── Allowlist still enforced when shell is enabled ───────────────────────────

class TestAllowlistEnforcement:
    def test_blocklisted_command_denied(self):
        mock_result = MagicMock(returncode=0, stdout="", stderr="")
        with patch("beigebox.config.get_config", return_value=_cfg(True)), \
             patch("beigebox.tools.system_info._bwrap_available", return_value=False), \
             patch("beigebox.tools.system_info.subprocess.run", return_value=mock_result) as mock_sp:
            from beigebox.tools.system_info import _run
            result = _run("curl http://evil.com")
        # "curl" not in allowlist → denied → subprocess not called
        mock_sp.assert_not_called()
        assert result == ""

    def test_empty_allowlist_denies_everything(self):
        cfg = {"operator": {"shell": {
            "enabled": True,
            "allowed_commands": [],
            "blocked_patterns": [],
        }}}
        with patch("beigebox.config.get_config", return_value=cfg), \
             patch("beigebox.tools.system_info._bwrap_available", return_value=False), \
             patch("beigebox.tools.system_info.subprocess.run") as mock_sp:
            from beigebox.tools.system_info import _run
            result = _run("ls")
        mock_sp.assert_not_called()
        assert result == ""


# ── Config exception safety ───────────────────────────────────────────────────

class TestShellGateExceptionSafety:
    def test_config_exception_does_not_block_execution(self):
        """If get_config raises, the gate should fail open (safe default)."""
        mock_result = MagicMock(returncode=0, stdout="ok", stderr="")
        with patch("beigebox.config.get_config", side_effect=RuntimeError("config unavailable")), \
             patch("beigebox.tools.system_info._bwrap_available", return_value=False), \
             patch("beigebox.tools.system_info._get_allowed_commands", return_value=["ls"]), \
             patch("beigebox.tools.system_info._get_blocked_patterns", return_value=[]), \
             patch("beigebox.tools.system_info.subprocess.run", return_value=mock_result):
            from beigebox.tools.system_info import _run
            # Should not raise — exception is caught and execution continues
            result = _run("ls")
        assert result == "ok"
