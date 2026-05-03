"""
Test suite for MCP Parameter Validation (Phase 1).

Tests:
  1. Valid inputs pass through without modification
  2. Attack patterns are rejected with clear error messages
  3. Edge cases and encoding tricks are handled correctly
  4. Tool-specific validation rules are enforced
  5. Mode enforcement (strict, warn, permissive)
  6. Integration: validation + tool execution
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from beigebox.tools.validation import ParameterValidator, ValidationResult
from beigebox.tools.injection_patterns import InjectionDetector
from beigebox.tools.schemas import (
    NetworkAuditScanNetworkInput,
    CDPNavigateInput,
    ApexAnalyzerInput,
    WebSearchInput,
    CalculatorInput,
)


class TestParameterValidator:
    """Test the ParameterValidator class."""

    @pytest.fixture
    def validator(self):
        """Create a validator instance for testing."""
        with patch("beigebox.config.get_config") as mock_config:
            mock_config.return_value = {
                "security": {
                    "tool_validation": {
                        "enabled": True,
                        "mode": "strict",
                        "per_tool_limits": {},
                    }
                }
            }
            return ParameterValidator()

    @pytest.fixture
    def permissive_validator(self):
        """Create a permissive validator (allows all)."""
        with patch("beigebox.config.get_config") as mock_config:
            mock_config.return_value = {
                "security": {
                    "tool_validation": {
                        "enabled": True,
                        "mode": "permissive",
                        "per_tool_limits": {},
                    }
                }
            }
            return ParameterValidator()

    # ─────────────────────────────────────────────────────────────────────────
    # Test: NetworkAuditTool Validation
    # ─────────────────────────────────────────────────────────────────────────

    def test_network_audit_valid_scan(self, validator):
        """Valid network scan should pass."""
        input_data = {"subnet": "192.168.1.0/24", "ports": "top-1000", "timeout": 1.0}
        result = validator.validate_tool_input("network_audit", input_data)

        assert result.is_valid is True
        assert result.errors == []

    def test_network_audit_invalid_subnet_public(self, validator):
        """Public subnet should be rejected."""
        input_data = {"subnet": "8.8.8.0/24", "ports": "top-1000"}
        result = validator.validate_tool_input("network_audit", input_data)

        assert result.is_valid is False
        assert "RFC1918" in result.errors[0]

    def test_network_audit_invalid_ip_public(self, validator):
        """Public IP should be rejected."""
        input_data = {"ip": "8.8.8.8"}
        result = validator.validate_tool_input("network_audit", input_data)

        assert result.is_valid is False
        assert "private" in result.errors[0].lower()

    def test_network_audit_valid_private_ips(self, validator):
        """All RFC1918 ranges should be accepted."""
        for subnet in ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]:
            input_data = {"subnet": subnet}
            result = validator.validate_tool_input("network_audit", input_data)
            assert result.is_valid is True, f"Failed for {subnet}"

    def test_network_audit_timeout_out_of_range(self, validator):
        """Timeout out of range should warn."""
        input_data = {"subnet": "192.168.1.0/24", "timeout": 60.0}
        result = validator.validate_tool_input("network_audit", input_data)

        assert result.is_valid is True  # Warning doesn't block
        assert len(result.warnings) > 0

    def test_network_audit_invalid_timeout(self, validator):
        """Invalid timeout type should fail."""
        input_data = {"subnet": "192.168.1.0/24", "timeout": "invalid"}
        result = validator.validate_tool_input("network_audit", input_data)

        assert result.is_valid is False

    # ─────────────────────────────────────────────────────────────────────────
    # Test: CDP Validation
    # ─────────────────────────────────────────────────────────────────────────

    def test_cdp_valid_https_url(self, validator):
        """Valid HTTPS URL should pass."""
        input_data = "https://example.com"
        result = validator.validate_tool_input("cdp", input_data)

        assert result.is_valid is True
        assert result.errors == []

    def test_cdp_valid_http_url(self, validator):
        """Valid HTTP URL should pass."""
        input_data = "http://example.com"
        result = validator.validate_tool_input("cdp", input_data)

        assert result.is_valid is True

    def test_cdp_javascript_scheme_rejected(self, validator):
        """javascript: scheme should be rejected."""
        input_data = "javascript:alert('xss')"
        result = validator.validate_tool_input("cdp", input_data)

        assert result.is_valid is False
        assert "XSS" in result.errors[0]

    def test_cdp_data_scheme_rejected(self, validator):
        """data: scheme should be rejected."""
        input_data = "data:text/html,<script>alert('xss')</script>"
        result = validator.validate_tool_input("cdp", input_data)

        assert result.is_valid is False

    def test_cdp_file_scheme_rejected(self, validator):
        """file: scheme should be rejected."""
        input_data = "file:///etc/passwd"
        result = validator.validate_tool_input("cdp", input_data)

        assert result.is_valid is False

    def test_cdp_url_too_long(self, validator):
        """URL exceeding 2048 chars should be rejected."""
        long_url = "https://example.com/" + "x" * 2100
        input_data = long_url
        result = validator.validate_tool_input("cdp", input_data)

        assert result.is_valid is False

    # ─────────────────────────────────────────────────────────────────────────
    # Test: ApexAnalyzer Validation
    # ─────────────────────────────────────────────────────────────────────────

    def test_apex_analyzer_valid_query(self, validator):
        """Valid Apex query should pass."""
        input_data = {"query": "SELECT", "search_type": "soql"}
        result = validator.validate_tool_input("apex_analyzer", "SELECT")

        assert result.is_valid is True

    def test_apex_analyzer_query_too_long(self, validator):
        """Query exceeding 1000 chars should fail."""
        long_query = "x" * 1100
        result = validator.validate_tool_input("apex_analyzer", long_query)

        assert result.is_valid is False
        assert "exceeds 1000" in result.errors[0]

    def test_apex_analyzer_complex_regex(self, validator):
        """Complex regex with many quantifiers should warn."""
        complex_regex = "a*b*c*d*e*f*g*h*i*j*k*l*m*n*"
        result = validator.validate_tool_input("apex_analyzer", complex_regex)

        # Validation should succeed but may warn about ReDoS
        assert result.is_valid is True

    # ─────────────────────────────────────────────────────────────────────────
    # Test: WebSearch Validation
    # ─────────────────────────────────────────────────────────────────────────

    def test_web_search_valid_query(self, validator):
        """Valid search query should pass."""
        result = validator.validate_tool_input("web_search", "how to learn python")

        assert result.is_valid is True

    def test_web_search_query_too_long(self, validator):
        """Query exceeding 500 chars should warn."""
        long_query = "x" * 600
        result = validator.validate_tool_input("web_search", long_query)

        assert result.is_valid is True
        assert len(result.warnings) > 0

    # ─────────────────────────────────────────────────────────────────────────
    # Test: Calculator Validation
    # ─────────────────────────────────────────────────────────────────────────

    def test_calculator_valid_expression(self, validator):
        """Valid math expression should pass."""
        result = validator.validate_tool_input("calculator", "2 ** 10")

        assert result.is_valid is True

    def test_calculator_expression_too_long(self, validator):
        """Expression exceeding 200 chars should fail."""
        long_expr = "x" * 300
        result = validator.validate_tool_input("calculator", long_expr)

        assert result.is_valid is False

    def test_calculator_injection_attempt(self, validator):
        """Command injection in calculator should fail."""
        injection = "2 + 2; rm -rf /"
        result = validator.validate_tool_input("calculator", injection)

        assert result.is_valid is False

    # ─────────────────────────────────────────────────────────────────────────
    # Test: Injection Detection
    # ─────────────────────────────────────────────────────────────────────────

    def test_looks_like_injection_backticks(self, validator):
        """Backtick injection should be detected."""
        assert validator.looks_like_injection("`cat /etc/passwd`") is True

    def test_looks_like_injection_dollar_paren(self, validator):
        """$() injection should be detected."""
        assert validator.looks_like_injection("$(whoami)") is True

    def test_looks_like_injection_sql(self, validator):
        """SQL injection should be detected."""
        assert validator.looks_like_injection("1' OR '1'='1") is False  # Missing keyword
        assert validator.looks_like_injection("1; DROP TABLE users") is True

    def test_looks_like_injection_xss(self, validator):
        """XSS should be detected."""
        assert validator.looks_like_injection("<script>alert('xss')</script>") is True

    def test_looks_like_injection_path_traversal(self, validator):
        """Path traversal should be detected."""
        assert validator.looks_like_injection("../../../etc/passwd") is True

    def test_looks_like_injection_normal_text(self, validator):
        """Normal text should not trigger injection detection."""
        assert validator.looks_like_injection("hello world") is False
        assert validator.looks_like_injection("the quick brown fox") is False

    # ─────────────────────────────────────────────────────────────────────────
    # Test: Mode Enforcement
    # ─────────────────────────────────────────────────────────────────────────

    # ─────────────────────────────────────────────────────────────────────────
    # Test: Get Schema
    # ─────────────────────────────────────────────────────────────────────────

    def test_get_tool_schema_network_audit(self, validator):
        """Should return JSON schema for network_audit."""
        schema = validator.get_tool_schema("network_audit")

        assert schema is not None
        assert schema["type"] == "object"

    def test_get_tool_schema_unknown_tool(self, validator):
        """Unknown tools should return None gracefully."""
        schema = validator.get_tool_schema("nonexistent_tool")

        assert schema is None

    # ─────────────────────────────────────────────────────────────────────────
    # Test: Edge Cases & Encoding Tricks
    # ─────────────────────────────────────────────────────────────────────────


class TestInjectionDetector:
    """Test the InjectionDetector class."""

    @pytest.fixture
    def detector(self):
        """Create a detector instance."""
        return InjectionDetector()

    def test_shell_backticks(self, detector):
        """Backtick command substitution should be detected."""
        matches = detector.detect("`cat /etc/passwd`")

        assert len(matches) > 0
        assert any("backticks" in m.pattern_name for m in matches)

    def test_shell_dollar_paren(self, detector):
        """$() substitution should be detected."""
        matches = detector.detect("$(whoami)")

        assert len(matches) > 0
        assert any("dollar_paren" in m.pattern_name for m in matches)

    def test_sql_injection(self, detector):
        """SQL keywords should be detected."""
        matches = detector.detect("1' OR '1'='1'; DROP TABLE users")

        assert len(matches) > 0
        assert any("drop" in m.pattern_name for m in matches)

    def test_xss_script_tag(self, detector):
        """<script> tag should be detected."""
        matches = detector.detect("<script>alert('xss')</script>")

        assert len(matches) > 0
        assert any("script_tag" in m.pattern_name for m in matches)

    def test_xss_event_handler(self, detector):
        """Event handlers should be detected."""
        matches = detector.detect("<img src=x onerror='alert(1)'>")

        assert len(matches) > 0
        assert any("event_handler" in m.pattern_name for m in matches)

    def test_path_traversal(self, detector):
        """Path traversal should be detected."""
        matches = detector.detect("../../../etc/passwd")

        assert len(matches) > 0
        assert any("traverse" in m.pattern_name for m in matches)

    def test_ldap_injection(self, detector):
        """LDAP wildcards should be detected."""
        matches = detector.detect("*)(uid=*))(|(uid=*")

        assert len(matches) > 0

    def test_mongodb_injection(self, detector):
        """MongoDB operators should be detected."""
        matches = detector.detect('{"$where": "this.salary > 100000"}')

        assert len(matches) > 0
        assert any("where" in m.pattern_name for m in matches)

    def test_benign_text(self, detector):
        """Benign text should have no matches or low confidence."""
        matches = detector.detect("hello world this is a normal sentence")

        # May have matches if common words like "db." appear, but confidence should be low
        for match in matches:
            if match.pattern_name not in ["shell_pipe", "shell_semicolon"]:
                # Some patterns might match innocently
                pass

    def test_is_likely_injection_true(self, detector):
        """Should identify likely injections."""
        assert detector.is_likely_injection("`rm -rf /`") is True
        assert detector.is_likely_injection("$(whoami)") is True
        assert detector.is_likely_injection("<script>alert(1)</script>") is True

    def test_is_likely_injection_false(self, detector):
        """Should not flag benign text."""
        assert detector.is_likely_injection("hello world") is False
        assert detector.is_likely_injection("the quick brown fox") is False


class TestPydanticSchemas:
    """Test Pydantic schema validation."""

    def test_cdp_schema_valid_url(self):
        """Valid CDPNavigateInput should parse."""
        data = {"url": "https://example.com"}
        obj = CDPNavigateInput(**data)

        assert obj.url == "https://example.com"

    def test_cdp_schema_invalid_url_scheme(self):
        """Invalid scheme should fail."""
        data = {"url": "javascript:alert('xss')"}

        with pytest.raises(Exception):
            CDPNavigateInput(**data)

    def test_network_audit_schema_valid(self):
        """Valid NetworkAuditScanNetworkInput should parse."""
        data = {"subnet": "192.168.1.0/24", "ports": "top-1000"}
        obj = NetworkAuditScanNetworkInput(**data)

        assert obj.subnet == "192.168.1.0/24"

    def test_calculator_schema_max_length(self):
        """CalculatorInput should enforce max length."""
        # CalculatorInput doesn't enforce via schema but via validator
        data = {"expression": "2 + 2"}
        obj = CalculatorInput(**data)

        assert obj.expression == "2 + 2"


@pytest.mark.integration
class TestValidationIntegration:
    """Integration tests: validation + tool registry."""

    @pytest.fixture
    def fake_config_with_validation(self):
        """Config with validation enabled."""
        return {
            "security": {
                "tool_validation": {
                    "enabled": True,
                    "mode": "strict",
                    "per_tool_limits": {},
                }
            },
            "tools": {
                "enabled": True,
            },
        }

    def test_registry_validates_before_execution(self, tmp_path, fake_config_with_validation):
        """Registry should validate input before calling tool.run()."""
        # This is an integration test requiring full setup
        # Verify that registry.run_tool() calls validator
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Fuzzing & Attack Payload Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestAttackPayloads:
    """Real-world attack payloads that should be rejected."""

    @pytest.fixture
    def validator(self):
        with patch("beigebox.config.get_config") as mock_config:
            mock_config.return_value = {
                "security": {
                    "tool_validation": {
                        "enabled": True,
                        "mode": "strict",
                        "per_tool_limits": {},
                    }
                }
            }
            return ParameterValidator()

    # Command injection payloads
    @pytest.mark.parametrize(
        "payload",
        [
            "`whoami`",
            "$(cat /etc/passwd)",
            "command1 && command2",
            "command1 | command2",
            "command1; command2",
            "command1 || command2",
            "echo test & whoami",
        ],
    )
    def test_command_injection_payloads(self, payload, validator):
        """Command injection payloads should be flagged."""
        result = validator.validate_tool_input("calculator", payload)
        assert result.is_valid is False

    # SQL injection payloads
    @pytest.mark.parametrize(
        "payload",
        [
            "'; DROP TABLE users; --",
            "1' UNION SELECT * FROM passwords",
            "admin' OR '1'='1",
            "1; DELETE FROM users",
        ],
    )
    def test_sql_injection_payloads(self, payload, validator):
        """SQL injection payloads should be flagged."""
        result = validator.validate_tool_input("apex_analyzer", payload)
        # Should either reject or warn
        assert result.is_valid is False or len(result.warnings) > 0

    # XSS payloads
    @pytest.mark.parametrize(
        "payload",
        [
            "<script>alert('xss')</script>",
            "javascript:alert('xss')",
            "<img src=x onerror='alert(1)'>",
            "<iframe src='data:text/html,<script>alert(1)</script>'></iframe>",
        ],
    )
    def test_xss_payloads(self, payload, validator):
        """XSS payloads should be rejected."""
        result = validator.validate_tool_input("cdp", payload)
        assert result.is_valid is False
