"""
Tests for MCP Parameter Validator (P1-B Security Hardening).

Covers all 5 rule types + edge cases + false positive handling:
  - WorkspaceFile: path traversal
  - NetworkAudit: RFC1918
  - CDP: URL scheme whitelist
  - PythonInterpreter: code injection
  - ApexAnalyzer: ReDoS
"""

import json
import tempfile
from pathlib import Path

import pytest

from beigebox.security.mcp_parameter_validator import (
    MCPValidationResult,
    ParameterValidator,
    ValidationIssue,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def validator(tmp_path):
    """Standard validator with a temp workspace root."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "out").mkdir()
    (ws / "in").mkdir()
    return ParameterValidator(workspace_root=str(ws))


@pytest.fixture
def permissive_validator(tmp_path):
    """Validator that allows localhost for CDP (dev mode)."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "out").mkdir()
    return ParameterValidator(workspace_root=str(ws), allow_localhost_cdp=True)


# ═══════════════════════════════════════════════════════════════════════════
# UNIT TESTS — WorkspaceFile (Path Traversal)
# ═══════════════════════════════════════════════════════════════════════════

class TestWorkspaceFile:

    @pytest.mark.unit
    def test_valid_read(self, validator):
        result = validator.validate("workspace_file", {
            "action": "read", "path": "workspace/out/report.md",
        })
        assert result.valid is True
        assert len(result.issues) == 0

    @pytest.mark.unit
    def test_valid_write(self, validator):
        result = validator.validate("workspace_file", {
            "action": "write",
            "path": "workspace/out/results/2026_04_12.json",
            "content": '{"key": "value"}',
        })
        assert result.valid is True

    @pytest.mark.unit
    def test_valid_list(self, validator):
        result = validator.validate("workspace_file", {"action": "list"})
        assert result.valid is True

    @pytest.mark.unit
    def test_block_directory_traversal(self, validator):
        result = validator.validate("workspace_file", {
            "action": "read", "path": "../../../../etc/passwd",
        })
        assert result.valid is False
        assert any(i.attack_type == "path_traversal" for i in result.issues)

    @pytest.mark.unit
    def test_block_url_encoded_traversal(self, validator):
        result = validator.validate("workspace_file", {
            "action": "read", "path": "%2e%2e%2f%2e%2e%2fetc/passwd",
        })
        assert result.valid is False
        assert any(i.attack_type == "path_traversal" for i in result.issues)

    @pytest.mark.unit
    def test_block_absolute_path(self, validator):
        result = validator.validate("workspace_file", {
            "action": "read", "path": "/etc/passwd",
        })
        assert result.valid is False
        assert any(i.attack_type == "path_traversal" for i in result.issues)

    @pytest.mark.unit
    def test_block_unc_path(self, validator):
        result = validator.validate("workspace_file", {
            "action": "read", "path": "\\\\attacker.com\\share\\secrets.txt",
        })
        assert result.valid is False
        assert any(i.attack_type == "path_traversal" for i in result.issues)

    @pytest.mark.unit
    def test_block_null_byte(self, validator):
        result = validator.validate("workspace_file", {
            "action": "read", "path": "workspace/legit.txt\x00.txt",
        })
        assert result.valid is False
        assert any(i.attack_type == "path_traversal" for i in result.issues)

    @pytest.mark.unit
    def test_block_invalid_action(self, validator):
        result = validator.validate("workspace_file", {
            "action": "delete", "path": "workspace/out/report.md",
        })
        assert result.valid is False
        assert any(i.attack_type == "invalid_action" for i in result.issues)

    @pytest.mark.unit
    def test_block_oversized_content(self, validator):
        result = validator.validate("workspace_file", {
            "action": "write",
            "path": "workspace/out/big.txt",
            "content": "x" * 70_000,
        })
        assert result.valid is False
        assert any(i.attack_type == "resource_exhaustion" for i in result.issues)

    @pytest.mark.unit
    def test_block_non_dict_input(self, validator):
        result = validator.validate("workspace_file", "not a dict")
        assert result.valid is False

    @pytest.mark.unit
    def test_json_string_input(self, validator):
        """String input that's valid JSON should be parsed."""
        result = validator.validate("workspace_file", json.dumps({
            "action": "read", "path": "workspace/out/report.md",
        }))
        assert result.valid is True

    @pytest.mark.unit
    def test_missing_path_for_read(self, validator):
        result = validator.validate("workspace_file", {"action": "read"})
        assert result.valid is False

    # False positive test
    @pytest.mark.unit
    def test_valid_path_with_subdirectories(self, validator):
        """Legitimate path with nested subdirs should pass."""
        result = validator.validate("workspace_file", {
            "action": "write",
            "path": "workspace/out/2026/04/12/data.json",
            "content": "{}",
        })
        assert result.valid is True


# ═══════════════════════════════════════════════════════════════════════════
# UNIT TESTS — NetworkAudit (RFC1918)
# ═══════════════════════════════════════════════════════════════════════════

class TestNetworkAudit:

    @pytest.mark.unit
    def test_valid_local_subnet(self, validator):
        result = validator.validate("network_audit", {
            "network": "192.168.1.0/24",
        })
        assert result.valid is True

    @pytest.mark.unit
    def test_valid_10_subnet(self, validator):
        result = validator.validate("network_audit", {
            "network": "10.0.50.0/24",
        })
        assert result.valid is True

    @pytest.mark.unit
    def test_valid_single_host(self, validator):
        result = validator.validate("network_audit", {
            "network": "192.168.1.100/32",
            "timeout_seconds": 10,
        })
        assert result.valid is True

    @pytest.mark.unit
    def test_block_public_internet(self, validator):
        result = validator.validate("network_audit", {
            "network": "0.0.0.0/0",
        })
        assert result.valid is False
        # Should fail on both CIDR too broad AND not RFC1918
        attack_types = {i.attack_type for i in result.issues}
        assert "resource_exhaustion" in attack_types or "ssrf" in attack_types

    @pytest.mark.unit
    def test_block_public_ip(self, validator):
        result = validator.validate("network_audit", {
            "network": "8.8.8.8/32",
        })
        assert result.valid is False
        assert any(i.attack_type == "ssrf" for i in result.issues)

    @pytest.mark.unit
    def test_block_broad_cidr(self, validator):
        result = validator.validate("network_audit", {
            "network": "10.0.0.0/8",
        })
        assert result.valid is False
        assert any(i.attack_type == "resource_exhaustion" for i in result.issues)

    @pytest.mark.unit
    def test_block_loopback_ip(self, validator):
        result = validator.validate("network_audit", {
            "ip": "127.0.0.1",
        })
        assert result.valid is False
        assert any(i.attack_type == "ssrf" for i in result.issues)

    @pytest.mark.unit
    def test_block_ipv6_loopback(self, validator):
        result = validator.validate("network_audit", {
            "ip": "::1",
        })
        assert result.valid is False

    @pytest.mark.unit
    def test_block_excessive_ports(self, validator):
        result = validator.validate("network_audit", {
            "network": "192.168.1.0/24",
            "ports": "1-65535",
        })
        assert result.valid is False
        assert any(i.attack_type == "resource_exhaustion" for i in result.issues)

    @pytest.mark.unit
    def test_block_excessive_timeout(self, validator):
        result = validator.validate("network_audit", {
            "network": "192.168.1.0/24",
            "timeout_seconds": 300,
        })
        assert result.valid is False
        assert any(i.attack_type == "resource_exhaustion" for i in result.issues)

    @pytest.mark.unit
    def test_block_non_private_ip(self, validator):
        result = validator.validate("network_audit", {"ip": "1.1.1.1"})
        assert result.valid is False

    @pytest.mark.unit
    def test_valid_named_ports(self, validator):
        result = validator.validate("network_audit", {
            "network": "192.168.1.0/24",
            "ports": "top1000",
        })
        assert result.valid is True

    # False positive test
    @pytest.mark.unit
    def test_valid_172_subnet(self, validator):
        """172.16.x.x is RFC1918 and should pass."""
        result = validator.validate("network_audit", {
            "network": "172.16.0.0/24",
        })
        assert result.valid is True

    @pytest.mark.unit
    def test_valid_specific_ports(self, validator):
        """Small port list should pass."""
        result = validator.validate("network_audit", {
            "network": "192.168.1.0/24",
            "ports": "22,80,443,8080",
        })
        assert result.valid is True


# ═══════════════════════════════════════════════════════════════════════════
# UNIT TESTS — CDP (URL Scheme Whitelist)
# ═══════════════════════════════════════════════════════════════════════════

class TestCDP:

    @pytest.mark.unit
    def test_valid_https_url(self, validator):
        result = validator.validate("cdp", {"url": "https://example.com/page"})
        assert result.valid is True

    @pytest.mark.unit
    def test_valid_http_url(self, validator):
        result = validator.validate("cdp", {"url": "http://example.com/search?q=test"})
        assert result.valid is True

    @pytest.mark.unit
    def test_valid_url_string_input(self, validator):
        """CDP can receive a plain URL string."""
        result = validator.validate("cdp", "https://example.com")
        assert result.valid is True

    @pytest.mark.unit
    def test_block_javascript_scheme(self, validator):
        result = validator.validate("cdp", {"url": "javascript:alert('xss')"})
        assert result.valid is False
        assert any(i.attack_type == "code_injection" for i in result.issues)

    @pytest.mark.unit
    def test_block_file_scheme(self, validator):
        result = validator.validate("cdp", {"url": "file:///etc/passwd"})
        assert result.valid is False

    @pytest.mark.unit
    def test_block_data_uri(self, validator):
        result = validator.validate("cdp", {
            "url": "data:text/html,<script>alert('xss')</script>",
        })
        assert result.valid is False

    @pytest.mark.unit
    def test_block_ftp_scheme(self, validator):
        result = validator.validate("cdp", {"url": "ftp://attacker.com/backdoor.exe"})
        assert result.valid is False

    @pytest.mark.unit
    def test_block_gopher_scheme(self, validator):
        result = validator.validate("cdp", {"url": "gopher://attacker.com/_test"})
        assert result.valid is False

    @pytest.mark.unit
    def test_block_ssrf_localhost(self, validator):
        result = validator.validate("cdp", {"url": "https://127.0.0.1:8000/admin"})
        assert result.valid is False
        assert any(i.attack_type == "ssrf" for i in result.issues)

    @pytest.mark.unit
    def test_block_ssrf_localhost_name(self, validator):
        result = validator.validate("cdp", {"url": "http://localhost:9222/json"})
        assert result.valid is False
        assert any(i.attack_type == "ssrf" for i in result.issues)

    @pytest.mark.unit
    def test_allow_localhost_when_configured(self, permissive_validator):
        """Dev mode: localhost allowed when allow_localhost_cdp=True."""
        result = permissive_validator.validate("cdp", {
            "url": "http://localhost:3000/dashboard",
        })
        assert result.valid is True

    @pytest.mark.unit
    def test_block_long_url(self, validator):
        result = validator.validate("cdp", {"url": "https://example.com/" + "a" * 2100})
        assert result.valid is False
        assert any(i.attack_type == "resource_exhaustion" for i in result.issues)

    # False positive test
    @pytest.mark.unit
    def test_valid_url_with_query_params(self, validator):
        """URLs with complex query params should pass."""
        result = validator.validate("cdp", {
            "url": "https://api.example.com/search?q=query&limit=10&offset=0",
        })
        assert result.valid is True


# ═══════════════════════════════════════════════════════════════════════════
# UNIT TESTS — PythonInterpreter (Code Injection)
# ═══════════════════════════════════════════════════════════════════════════

class TestPythonInterpreter:

    @pytest.mark.unit
    def test_valid_math(self, validator):
        result = validator.validate("python", "import math; result = math.sqrt(16)")
        assert result.valid is True

    @pytest.mark.unit
    def test_valid_json_processing(self, validator):
        result = validator.validate("python", 'import json; data = json.loads(\'{"key": "value"}\')')
        assert result.valid is True

    @pytest.mark.unit
    def test_valid_workspace_file_open(self, validator):
        """open() with workspace path should be allowed."""
        result = validator.validate(
            "python",
            "with open('workspace/out/data.json') as f: data = f.read()",
        )
        assert result.valid is True

    @pytest.mark.unit
    def test_block_os_system(self, validator):
        result = validator.validate("python", "import os; os.system('rm -rf /')")
        assert result.valid is False
        assert any(i.attack_type == "code_injection" for i in result.issues)

    @pytest.mark.unit
    def test_block_os_environ(self, validator):
        result = validator.validate("python", "import os; os.environ['OPENROUTER_API_KEY']")
        assert result.valid is False

    @pytest.mark.unit
    def test_block_subprocess(self, validator):
        result = validator.validate("python", "import subprocess; subprocess.call(['ls'])")
        assert result.valid is False

    @pytest.mark.unit
    def test_block_socket(self, validator):
        result = validator.validate(
            "python",
            "import socket; s = socket.socket(); s.connect(('attacker.com', 443))",
        )
        assert result.valid is False

    @pytest.mark.unit
    def test_block_eval(self, validator):
        result = validator.validate("python", "eval('__import__(\"os\").system(\"id\")')")
        assert result.valid is False
        assert any(i.attack_type == "code_injection" for i in result.issues)

    @pytest.mark.unit
    def test_block_exec(self, validator):
        result = validator.validate("python", "exec(compile(open('malicious.py').read(), 'x', 'exec'))")
        assert result.valid is False

    @pytest.mark.unit
    def test_block_dunder_import(self, validator):
        result = validator.validate("python", "__import__('os').system('id')")
        assert result.valid is False

    @pytest.mark.unit
    def test_block_dunder_subclasses(self, validator):
        result = validator.validate(
            "python",
            "''.__class__.__bases__[0].__subclasses__()",
        )
        assert result.valid is False
        assert any(i.attack_type == "code_injection" for i in result.issues)

    @pytest.mark.unit
    def test_block_file_open_outside_workspace(self, validator):
        result = validator.validate("python", "open('/etc/passwd', 'r').read()")
        assert result.valid is False

    @pytest.mark.unit
    def test_block_oversized_code(self, validator):
        result = validator.validate("python", "x = 1\n" * 20_000)
        assert result.valid is False
        assert any(i.attack_type == "resource_exhaustion" for i in result.issues)

    @pytest.mark.unit
    def test_block_empty_code(self, validator):
        result = validator.validate("python", "")
        assert result.valid is False

    # False positive tests
    @pytest.mark.unit
    def test_valid_long_but_safe_code(self, validator):
        """Long code under limit with safe modules should pass."""
        code = "import math\n" + "\n".join(
            f"x{i} = math.sqrt({i})" for i in range(100)
        )
        result = validator.validate("python", code)
        assert result.valid is True

    @pytest.mark.unit
    def test_valid_regex_module(self, validator):
        """re module is safe and should be allowed."""
        result = validator.validate("python", "import re; m = re.match(r'\\d+', '123')")
        assert result.valid is True

    @pytest.mark.unit
    def test_valid_collections_module(self, validator):
        result = validator.validate(
            "python",
            "from collections import Counter; c = Counter([1,2,2,3])",
        )
        assert result.valid is True


# ═══════════════════════════════════════════════════════════════════════════
# UNIT TESTS — ApexAnalyzer (ReDoS)
# ═══════════════════════════════════════════════════════════════════════════

class TestApexAnalyzer:

    @pytest.mark.unit
    def test_valid_simple_soql(self, validator):
        result = validator.validate("apex_analyzer", {
            "soql_query": "SELECT Id, Name FROM Account WHERE Industry = 'Technology'",
        })
        assert result.valid is True

    @pytest.mark.unit
    def test_valid_subquery(self, validator):
        result = validator.validate("apex_analyzer", {
            "soql_query": "SELECT Id, Name, (SELECT Email FROM Contacts) FROM Account LIMIT 100",
        })
        assert result.valid is True

    @pytest.mark.unit
    def test_block_nested_quantifier(self, validator):
        result = validator.validate("apex_analyzer", {
            "soql_query": "SELECT Id FROM Account WHERE Name MATCHES '(a+)+b'",
        })
        assert result.valid is False
        assert any(i.attack_type == "redos" for i in result.issues)

    @pytest.mark.unit
    def test_block_nested_quantifier_star(self, validator):
        result = validator.validate("apex_analyzer", {
            "soql_query": "Name MATCHES '(a*)*b'",
        })
        assert result.valid is False
        assert any(i.attack_type == "redos" for i in result.issues)

    @pytest.mark.unit
    def test_block_query_length_bomb(self, validator):
        result = validator.validate("apex_analyzer", {
            "soql_query": "SELECT " + ", ".join(["Id"] * 10000) + " FROM Account",
        })
        assert result.valid is False
        assert any(i.attack_type == "resource_exhaustion" for i in result.issues)

    @pytest.mark.unit
    def test_block_sql_injection(self, validator):
        result = validator.validate("apex_analyzer", {
            "soql_query": "SELECT Id FROM Account WHERE Id = '1' OR '1'='1'",
        })
        assert result.valid is False
        assert any(i.attack_type == "sql_injection" for i in result.issues)

    @pytest.mark.unit
    def test_string_input(self, validator):
        """ApexAnalyzer should accept plain string queries."""
        result = validator.validate("apex_analyzer", "SELECT Id FROM Account")
        assert result.valid is True

    # False positive test
    @pytest.mark.unit
    def test_valid_simple_regex(self, validator):
        """Simple regex patterns should not be flagged."""
        result = validator.validate("apex_analyzer", {
            "soql_query": "SELECT Id FROM Account WHERE Name MATCHES '^[a-zA-Z0-9]*$'",
        })
        assert result.valid is True


# ═══════════════════════════════════════════════════════════════════════════
# UNIT TESTS — Additional Tools
# ═══════════════════════════════════════════════════════════════════════════

class TestConfluenceCrawler:

    @pytest.mark.unit
    def test_valid_https(self, validator):
        result = validator.validate("confluence_crawler", {
            "url": "https://wiki.example.com/display/TEAM/Page",
        })
        assert result.valid is True

    @pytest.mark.unit
    def test_block_javascript(self, validator):
        result = validator.validate("confluence_crawler", {
            "url": "javascript:alert(1)",
        })
        assert result.valid is False


class TestWebScraper:

    @pytest.mark.unit
    def test_valid_url(self, validator):
        result = validator.validate("web_scraper", "https://example.com")
        assert result.valid is True

    @pytest.mark.unit
    def test_block_file_scheme(self, validator):
        result = validator.validate("web_scraper", "file:///etc/hosts")
        assert result.valid is False


class TestBlueTruth:

    @pytest.mark.unit
    def test_valid_command(self, validator):
        result = validator.validate("bluetruth", {"command": "scan"})
        assert result.valid is True

    @pytest.mark.unit
    def test_block_shell_injection(self, validator):
        result = validator.validate("bluetruth", {"command": "scan; rm -rf /"})
        assert result.valid is False
        assert any(i.attack_type == "code_injection" for i in result.issues)

    @pytest.mark.unit
    def test_block_backtick_injection(self, validator):
        result = validator.validate("bluetruth", {"command": "`whoami`"})
        assert result.valid is False


class TestBrowserbox:

    @pytest.mark.unit
    def test_valid_navigate(self, validator):
        result = validator.validate("browserbox", {
            "tool": "tabs.open", "input": "https://example.com",
        })
        assert result.valid is True

    @pytest.mark.unit
    def test_block_javascript_navigate(self, validator):
        result = validator.validate("browserbox", {
            "tool": "tabs.open", "input": "javascript:alert(1)",
        })
        assert result.valid is False


# ═══════════════════════════════════════════════════════════════════════════
# UNIT TESTS — Unknown Tools & Edge Cases
# ═══════════════════════════════════════════════════════════════════════════

class TestEdgeCases:

    @pytest.mark.unit
    def test_unknown_tool_passes(self, validator):
        """Unknown tools should pass validation (graceful degradation)."""
        result = validator.validate("some_future_tool", {"arg": "value"})
        assert result.valid is True

    @pytest.mark.unit
    def test_result_has_elapsed_ms(self, validator):
        result = validator.validate("workspace_file", {"action": "list"})
        assert result.elapsed_ms >= 0

    @pytest.mark.unit
    def test_result_to_dict(self, validator):
        result = validator.validate("workspace_file", {
            "action": "read", "path": "../../../../etc/passwd",
        })
        d = result.to_dict()
        assert "valid" in d
        assert "issues" in d
        assert isinstance(d["issues"], list)
        assert d["valid"] is False

    @pytest.mark.unit
    def test_batch_validation(self, validator):
        calls = [
            {"tool": "workspace_file", "params": {"action": "list"}},
            {"tool": "cdp", "params": {"url": "javascript:alert(1)"}},
            {"tool": "network_audit", "params": {"network": "192.168.1.0/24"}},
        ]
        results = validator.validate_batch(calls)
        assert len(results) == 3
        assert results[0].valid is True   # list is fine
        assert results[1].valid is False  # javascript scheme blocked
        assert results[2].valid is True   # valid subnet

    @pytest.mark.unit
    def test_tool_subcommand_routing(self, validator):
        """cdp.navigate should route to the cdp validator."""
        result = validator.validate("cdp.navigate", {"url": "javascript:x"})
        assert result.valid is False


# ═══════════════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — MCPValidatorTool wrapper
# ═══════════════════════════════════════════════════════════════════════════

class TestMCPValidatorTool:

    @pytest.mark.integration
    def test_tool_run_single(self, tmp_path, monkeypatch):
        """Test the tool wrapper in single validation mode."""
        monkeypatch.setattr(
            "beigebox.config.get_config",
            lambda: {
                "workspace": {"path": str(tmp_path / "workspace")},
                "security": {"mcp_validator": {"enabled": True}},
            },
        )
        ws = tmp_path / "workspace"
        ws.mkdir(exist_ok=True)
        (ws / "out").mkdir(exist_ok=True)

        from beigebox.tools.mcp_validator_tool import MCPValidatorTool
        tool = MCPValidatorTool()

        result = tool.run(json.dumps({
            "tool": "workspace_file",
            "params": {"action": "read", "path": "../../../../etc/passwd"},
        }))
        data = json.loads(result)
        assert data["valid"] is False
        assert len(data["issues"]) > 0

    @pytest.mark.integration
    def test_tool_run_batch(self, tmp_path, monkeypatch):
        """Test batch validation through the tool wrapper."""
        monkeypatch.setattr(
            "beigebox.config.get_config",
            lambda: {
                "workspace": {"path": str(tmp_path / "workspace")},
                "security": {"mcp_validator": {"enabled": True}},
            },
        )
        ws = tmp_path / "workspace"
        ws.mkdir(exist_ok=True)
        (ws / "out").mkdir(exist_ok=True)

        from beigebox.tools.mcp_validator_tool import MCPValidatorTool
        tool = MCPValidatorTool()

        result = tool.run(json.dumps({
            "batch": [
                {"tool": "workspace_file", "params": {"action": "list"}},
                {"tool": "python", "params": "import os; os.system('id')"},
            ],
        }))
        data = json.loads(result)
        assert data["batch_valid"] is False
        assert data["results"][0]["valid"] is True
        assert data["results"][1]["valid"] is False

    @pytest.mark.integration
    def test_tool_run_invalid_json(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "beigebox.config.get_config",
            lambda: {
                "workspace": {"path": str(tmp_path / "workspace")},
                "security": {"mcp_validator": {}},
            },
        )
        from beigebox.tools.mcp_validator_tool import MCPValidatorTool
        tool = MCPValidatorTool()
        result = tool.run("not json at all")
        data = json.loads(result)
        assert data["valid"] is False

    @pytest.mark.integration
    def test_validate_before_execution(self, tmp_path, monkeypatch):
        """Test direct validation method used by operator hook."""
        monkeypatch.setattr(
            "beigebox.config.get_config",
            lambda: {
                "workspace": {"path": str(tmp_path / "workspace")},
                "security": {"mcp_validator": {"enabled": True}},
            },
        )
        ws = tmp_path / "workspace"
        ws.mkdir(exist_ok=True)
        (ws / "out").mkdir(exist_ok=True)

        from beigebox.tools.mcp_validator_tool import MCPValidatorTool
        tool = MCPValidatorTool()

        vr = tool.validate_before_execution("python", "import os; os.system('id')")
        assert vr.valid is False
        assert isinstance(vr, MCPValidationResult)
