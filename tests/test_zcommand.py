"""Tests for z-command parser."""

import pytest
from beigebox.agents.zcommand import parse_z_command


class TestZCommandParsing:
    def test_no_z_prefix(self):
        cmd = parse_z_command("What is the weather?")
        assert not cmd.active
        assert cmd.message == "What is the weather?"

    def test_simple_route(self):
        cmd = parse_z_command("z: simple How are you?")
        assert cmd.active
        assert cmd.route == "fast"
        assert cmd.message == "How are you?"

    def test_complex_route(self):
        cmd = parse_z_command("z: complex Design a distributed system")
        assert cmd.active
        assert cmd.route == "large"
        assert cmd.message == "Design a distributed system"

    def test_code_route(self):
        cmd = parse_z_command("z: code Write a binary tree in Rust")
        assert cmd.active
        assert cmd.route == "code"

    def test_specific_model(self):
        cmd = parse_z_command("z: qwen3:32b Explain quantum physics")
        assert cmd.active
        assert cmd.model == "qwen3:32b"
        assert cmd.message == "Explain quantum physics"

    def test_tool_search(self):
        cmd = parse_z_command("z: search What happened today?")
        assert cmd.active
        assert "web_search" in cmd.tools
        assert cmd.message == "What happened today?"

    def test_tool_calc(self):
        cmd = parse_z_command("z: calc 2**16 + 3**10")
        assert cmd.active
        assert "calculator" in cmd.tools
        assert cmd.tool_input == "2**16 + 3**10"

    def test_tool_memory(self):
        cmd = parse_z_command("z: memory docker networking")
        assert cmd.active
        assert "memory" in cmd.tools

    def test_chaining(self):
        cmd = parse_z_command("z: complex,search What's new in AI?")
        assert cmd.active
        assert cmd.route == "large"
        assert "web_search" in cmd.tools

    def test_help(self):
        cmd = parse_z_command("z: help")
        assert cmd.active
        assert cmd.is_help

    def test_case_insensitive(self):
        cmd = parse_z_command("Z: COMPLEX build me something")
        assert cmd.active
        assert cmd.route == "large"

    def test_whitespace_tolerance(self):
        cmd = parse_z_command("  z:   simple   hello")
        assert cmd.active
        assert cmd.route == "fast"
        assert cmd.message == "hello"

    def test_no_message_after_directive(self):
        cmd = parse_z_command("z: sysinfo")
        assert cmd.active
        assert "system_info" in cmd.tools
        assert cmd.message == ""


# ── z: fork parsing ───────────────────────────────────────────────────────────

class TestZForkParsing:
    def test_fork_sets_is_fork(self):
        cmd = parse_z_command("z: fork")
        assert cmd.active
        assert cmd.is_fork is True

    def test_fork_is_not_route(self):
        cmd = parse_z_command("z: fork")
        assert cmd.route == ""
        assert cmd.tools == []

    def test_fork_trailing_message(self):
        """Any text after 'fork' becomes the message."""
        cmd = parse_z_command("z: fork start fresh on the auth refactor")
        assert cmd.is_fork is True
        assert "fresh" in cmd.message

    def test_fork_raw_directives(self):
        cmd = parse_z_command("z: fork")
        assert cmd.raw_directives == "fork"

    def test_non_fork_does_not_set_is_fork(self):
        cmd = parse_z_command("z: simple hello")
        assert cmd.is_fork is False

    def test_no_z_prefix_is_not_fork(self):
        cmd = parse_z_command("fork this idea please")
        assert cmd.is_fork is False
        assert cmd.active is False
