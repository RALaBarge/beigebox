"""
Injection Attack Pattern Detection

Heuristics for common injection vectors:
  - Command injection: shell metacharacters, $(), backticks, &&, |, ;
  - Path traversal: ../, ..\\, UNC paths, symlinks
  - SQL injection: SELECT, DROP, UNION, etc.
  - LDAP injection: wildcard, parentheses
  - NoSQL injection: MongoDB operators ($where, $regex, etc.)
  - XSS: javascript:, <script>, onerror=, etc.
  - XXE: <?xml, <!ENTITY, etc.
  - Template injection: {{, {%, etc.

Not intended as perfect detection (impossible), but as heuristic warnings.
Primary defense is schema validation + sandboxing.
"""

import re
from dataclasses import dataclass
from typing import Optional, List


@dataclass
class InjectionMatch:
    """A potential injection detected."""
    pattern_name: str
    severity: str  # LOW, MEDIUM, HIGH, CRITICAL
    match: str
    position: int
    confidence: float  # 0.0-1.0


class InjectionDetector:
    """Detect common injection patterns in strings."""

    def __init__(self):
        self._init_patterns()

    def _init_patterns(self):
        """Initialize all injection pattern detectors."""
        # Command injection: shell metacharacters
        self.patterns = {
            "shell_backticks": {
                "regex": re.compile(r"`[^`]*`"),
                "severity": "HIGH",
                "description": "Backtick command substitution",
            },
            "shell_dollar_paren": {
                "regex": re.compile(r"\$\([^)]*\)"),
                "severity": "HIGH",
                "description": "$(command) substitution",
            },
            "shell_dollar_brace": {
                "regex": re.compile(r"\$\{[^}]*\}"),
                "severity": "MEDIUM",
                "description": "Variable expansion (may be harmless)",
            },
            "shell_ampersand_ampersand": {
                "regex": re.compile(r"&&"),
                "severity": "HIGH",
                "description": "Command chaining (&&)",
            },
            "shell_pipe": {
                "regex": re.compile(r"\|(?!\|)"),
                "severity": "HIGH",
                "description": "Pipe operator (|)",
            },
            "shell_pipe_pipe": {
                "regex": re.compile(r"\|\|"),
                "severity": "HIGH",
                "description": "Logical OR (||) for command chaining",
            },
            "shell_semicolon": {
                "regex": re.compile(r";(?!=)"),
                "severity": "HIGH",
                "description": "Statement separator (;)",
            },
            "shell_redirect_in": {
                "regex": re.compile(r"<\("),
                "severity": "MEDIUM",
                "description": "Process substitution <()",
            },
            "shell_redirect_out": {
                "regex": re.compile(r"[^<>]>[^=>]|^>"),
                "severity": "MEDIUM",
                "description": "Output redirection (>)",
            },
            "shell_background": {
                "regex": re.compile(r"&$"),
                "severity": "MEDIUM",
                "description": "Background execution (&)",
            },
            # SQL Injection
            "sql_select": {
                "regex": re.compile(r"\bSELECT\b", re.IGNORECASE),
                "severity": "HIGH",
                "description": "SQL SELECT statement",
            },
            "sql_drop": {
                "regex": re.compile(r"\bDROP\b", re.IGNORECASE),
                "severity": "CRITICAL",
                "description": "SQL DROP statement",
            },
            "sql_delete": {
                "regex": re.compile(r"\bDELETE\b", re.IGNORECASE),
                "severity": "CRITICAL",
                "description": "SQL DELETE statement",
            },
            "sql_insert": {
                "regex": re.compile(r"\bINSERT\b", re.IGNORECASE),
                "severity": "HIGH",
                "description": "SQL INSERT statement",
            },
            "sql_update": {
                "regex": re.compile(r"\bUPDATE\b", re.IGNORECASE),
                "severity": "HIGH",
                "description": "SQL UPDATE statement",
            },
            "sql_union": {
                "regex": re.compile(r"\bUNION\b", re.IGNORECASE),
                "severity": "HIGH",
                "description": "SQL UNION (query stacking)",
            },
            "sql_exec": {
                "regex": re.compile(r"\b(EXEC|EXECUTE)\b", re.IGNORECASE),
                "severity": "CRITICAL",
                "description": "SQL procedure execution",
            },
            # Path Traversal
            "path_traverse_unix": {
                "regex": re.compile(r"\.\./"),
                "severity": "HIGH",
                "description": "Unix path traversal (../)",
            },
            "path_traverse_windows": {
                "regex": re.compile(r"\.\.\$"),
                "severity": "HIGH",
                "description": "Windows path traversal (..) ",
            },
            "path_unc": {
                "regex": re.compile(r"\\\\[^\\]+\\[^\\]+"),
                "severity": "MEDIUM",
                "description": "UNC path (\\\\server\\share)",
            },
            "path_abs_windows": {
                "regex": re.compile(r"^[a-zA-Z]:\\"),
                "severity": "MEDIUM",
                "description": "Windows absolute path",
            },
            # LDAP Injection
            "ldap_wildcard": {
                "regex": re.compile(r"\*"),
                "severity": "LOW",
                "description": "LDAP wildcard (*) — context dependent",
            },
            "ldap_parentheses": {
                "regex": re.compile(r"[()&|]"),
                "severity": "MEDIUM",
                "description": "LDAP filter metacharacters",
            },
            # NoSQL Injection
            "nosql_where": {
                "regex": re.compile(r"\$where", re.IGNORECASE),
                "severity": "CRITICAL",
                "description": "MongoDB $where operator",
            },
            "nosql_regex": {
                "regex": re.compile(r"\$regex", re.IGNORECASE),
                "severity": "MEDIUM",
                "description": "MongoDB $regex operator",
            },
            "nosql_ne": {
                "regex": re.compile(r"\$ne", re.IGNORECASE),
                "severity": "MEDIUM",
                "description": "MongoDB $ne (not equal) operator",
            },
            "nosql_nin": {
                "regex": re.compile(r"\$nin", re.IGNORECASE),
                "severity": "MEDIUM",
                "description": "MongoDB $nin (not in) operator",
            },
            # XSS
            "xss_script_tag": {
                "regex": re.compile(r"<\s*script", re.IGNORECASE),
                "severity": "CRITICAL",
                "description": "HTML <script> tag",
            },
            "xss_iframe": {
                "regex": re.compile(r"<\s*iframe", re.IGNORECASE),
                "severity": "CRITICAL",
                "description": "HTML <iframe> tag",
            },
            "xss_event_handler": {
                "regex": re.compile(r"\bon\w+\s*=", re.IGNORECASE),
                "severity": "HIGH",
                "description": "HTML event handler (onclick, onerror, etc.)",
            },
            "xss_javascript_scheme": {
                "regex": re.compile(r"javascript\s*:", re.IGNORECASE),
                "severity": "CRITICAL",
                "description": "javascript: URL scheme",
            },
            "xss_data_scheme": {
                "regex": re.compile(r"data\s*:", re.IGNORECASE),
                "severity": "HIGH",
                "description": "data: URL scheme",
            },
            "xss_embed": {
                "regex": re.compile(r"<\s*embed", re.IGNORECASE),
                "severity": "HIGH",
                "description": "HTML <embed> tag",
            },
            # XXE / XML
            "xxe_entity": {
                "regex": re.compile(r"<!ENTITY", re.IGNORECASE),
                "severity": "CRITICAL",
                "description": "XML entity definition",
            },
            "xxe_doctype": {
                "regex": re.compile(r"<!DOCTYPE", re.IGNORECASE),
                "severity": "MEDIUM",
                "description": "XML DOCTYPE declaration",
            },
            # Template Injection
            "template_jinja": {
                "regex": re.compile(r"{{.*?}}"),
                "severity": "MEDIUM",
                "description": "Jinja2 template syntax ({{ ... }})",
            },
            "template_jinja_if": {
                "regex": re.compile(r"{%.*?%}"),
                "severity": "MEDIUM",
                "description": "Jinja2 control flow ({% ... %})",
            },
            # Code Injection
            "python_eval": {
                "regex": re.compile(r"\beval\s*\(", re.IGNORECASE),
                "severity": "CRITICAL",
                "description": "Python eval()",
            },
            "python_exec": {
                "regex": re.compile(r"\bexec\s*\(", re.IGNORECASE),
                "severity": "CRITICAL",
                "description": "Python exec()",
            },
            "python_import": {
                "regex": re.compile(r"\b__import__\s*\(", re.IGNORECASE),
                "severity": "HIGH",
                "description": "Python __import__()",
            },
        }

    def detect(self, value: str, max_matches: int = 5) -> List[InjectionMatch]:
        """
        Detect injection patterns in a string.

        Args:
            value: String to analyze
            max_matches: Max matches to return (prevent spam)

        Returns:
            List of InjectionMatch objects, sorted by severity
        """
        matches = []

        for pattern_name, pattern_info in self.patterns.items():
            regex = pattern_info["regex"]
            for match in regex.finditer(value):
                matches.append(
                    InjectionMatch(
                        pattern_name=pattern_name,
                        severity=pattern_info["severity"],
                        match=match.group(),
                        position=match.start(),
                        confidence=self._confidence_for_pattern(pattern_name, value),
                    )
                )

        # Sort by severity (CRITICAL > HIGH > MEDIUM > LOW)
        severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        matches.sort(key=lambda m: severity_order.get(m.severity, 4))

        return matches[:max_matches]

    def _confidence_for_pattern(self, pattern_name: str, context: str) -> float:
        """
        Estimate confidence in injection (0.0–1.0).
        Context matters: SELECT in a database query is normal, but in file paths is suspicious.
        """
        # Base confidence by pattern (highly context-dependent)
        base_confidence = {
            "shell_backticks": 0.9,
            "shell_dollar_paren": 0.9,
            "shell_ampersand_ampersand": 0.7,
            "shell_pipe": 0.6,  # pipes appear in legitimate data
            "shell_semicolon": 0.5,  # semicolons are common
            "sql_select": 0.4,  # very common word
            "sql_drop": 0.95,
            "sql_delete": 0.85,
            "sql_exec": 0.95,
            "path_traverse_unix": 0.95,
            "path_traverse_windows": 0.95,
            "xss_script_tag": 0.99,
            "xss_javascript_scheme": 0.99,
            "xxe_entity": 0.99,
            "python_eval": 0.95,
            "python_exec": 0.95,
            "nosql_where": 0.95,
            "ldap_wildcard": 0.1,  # very common, low confidence
            "template_jinja": 0.5,  # could be legitimate output
        }

        confidence = base_confidence.get(pattern_name, 0.5)

        # Reduce confidence if pattern appears in comments-like context
        if context.strip().startswith("#") or context.strip().startswith("//"):
            confidence *= 0.3

        # Increase confidence if multiple injection patterns appear
        if context.count("&&") > 0 or context.count("|") > 0:
            confidence = min(confidence * 1.2, 1.0)

        return confidence

    def get_severity_summary(self, value: str) -> Optional[str]:
        """Return highest severity if any pattern matched, else None."""
        matches = self.detect(value, max_matches=1)
        if matches:
            return matches[0].severity
        return None

    def is_likely_injection(self, value: str, confidence_threshold: float = 0.7) -> bool:
        """Quick check: does this look like an injection attempt?"""
        matches = self.detect(value, max_matches=1)
        return len(matches) > 0 and matches[0].confidence >= confidence_threshold
