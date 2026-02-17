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
