"""
Guardrails — input/output filtering for safety and compliance.

Checks every request before it reaches the backend and every response
before it reaches the client. Blocks or redacts based on configured rules.

Config (config.yaml):
  guardrails:
    enabled: false
    input:
      block_keywords: []        # exact word/phrase blocklist (case-insensitive)
      block_patterns: []        # regex patterns; match = block
      pii_detection: false      # detect emails, phones, SSNs, credit cards in input
      prompt_injection: false   # detect common jailbreak/injection phrases
      max_length: 0             # 0 = no limit; N = block if user message > N chars
      topic_blocklist: []       # topic keyword groups e.g. ["weapons", "gambling"]
    output:
      pii_redaction: false      # redact PII from output before sending to client
      block_patterns: []        # regex patterns; match = replace with block_message
      block_message: "[Response blocked by guardrails]"

Wire event: guardrail_block — emitted on every block
  source=guardrails, meta includes rule_name, reason, direction
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ── Built-in PII patterns ──────────────────────────────────────────────────
_PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("email",       re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b')),
    ("phone",       re.compile(r'\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b')),
    ("ssn",         re.compile(r'\b\d{3}-\d{2}-\d{4}\b')),
    ("credit_card", re.compile(r'\b(?:\d[ -]?){13,16}\b')),
    ("ip_address",  re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b')),
]

# ── Common prompt injection signatures ─────────────────────────────────────
_INJECTION_PHRASES = [
    r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?",
    r"disregard\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?",
    r"forget\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?",
    r"you\s+are\s+now\s+(?:a\s+)?(?:dan|jailbreak|unrestricted|free)",
    r"act\s+as\s+(?:if\s+you\s+(?:are|were)\s+)?(?:an?\s+)?(?:evil|uncensored|unrestricted)",
    r"do\s+anything\s+now",
    r"jailbreak",
    r"pretend\s+you\s+have\s+no\s+restrictions?",
    r"bypass\s+(?:your\s+)?(?:safety|content|ethical)\s+(?:filters?|guidelines?|rules?|restrictions?)",
    r"developer\s+mode",
    r"system\s+prompt\s*[:=]",
    r"<\s*/?system\s*>",
]
_INJECTION_PATTERNS = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in _INJECTION_PHRASES]


# ── Result type ───────────────────────────────────────────────────────────
@dataclass
class GuardrailResult:
    allowed: bool
    reason: str = ""
    rule_name: str = ""

    @classmethod
    def ok(cls) -> "GuardrailResult":
        return cls(allowed=True)

    @classmethod
    def block(cls, reason: str, rule_name: str = "") -> "GuardrailResult":
        return cls(allowed=False, reason=reason, rule_name=rule_name)


# ── Main class ────────────────────────────────────────────────────────────
class Guardrails:
    """
    Input/output guardrail engine.

    check_input(messages) → GuardrailResult
    check_output(text)    → (GuardrailResult, str)  — str is possibly-redacted text
    """

    def __init__(self, cfg: dict):
        gr_cfg = cfg.get("guardrails", {})
        self.enabled: bool = gr_cfg.get("enabled", False)
        in_cfg = gr_cfg.get("input", {})
        out_cfg = gr_cfg.get("output", {})

        # Input settings
        self._block_keywords: list[str] = [k.lower() for k in in_cfg.get("block_keywords", [])]
        self._block_patterns_in: list[re.Pattern] = [
            re.compile(p, re.IGNORECASE | re.DOTALL) for p in in_cfg.get("block_patterns", [])
        ]
        self._pii_detection: bool = in_cfg.get("pii_detection", False)
        self._prompt_injection: bool = in_cfg.get("prompt_injection", False)
        self._max_length: int = in_cfg.get("max_length", 0)
        self._topic_blocklist: list[str] = [t.lower() for t in in_cfg.get("topic_blocklist", [])]

        # Output settings
        self._pii_redaction: bool = out_cfg.get("pii_redaction", False)
        self._block_patterns_out: list[re.Pattern] = [
            re.compile(p, re.IGNORECASE | re.DOTALL) for p in out_cfg.get("block_patterns", [])
        ]
        self._block_message: str = out_cfg.get(
            "block_message", "[Response blocked by guardrails]"
        )

        if self.enabled:
            logger.info(
                "Guardrails: enabled — pii_detection=%s prompt_injection=%s "
                "keywords=%d patterns_in=%d patterns_out=%d pii_redaction=%s",
                self._pii_detection, self._prompt_injection,
                len(self._block_keywords), len(self._block_patterns_in),
                len(self._block_patterns_out), self._pii_redaction,
            )

    # ── Input check ───────────────────────────────────────────────────────
    def check_input(self, messages: list[dict]) -> GuardrailResult:
        """
        Check incoming messages. Returns GuardrailResult(allowed=False) to block.
        Only user-role messages are checked — system messages are BeigeBox-generated.
        """
        if not self.enabled:
            return GuardrailResult.ok()

        user_text = " ".join(
            m.get("content", "") for m in messages
            if m.get("role") == "user" and isinstance(m.get("content"), str)
        )
        if not user_text:
            return GuardrailResult.ok()

        # Max length
        if self._max_length and len(user_text) > self._max_length:
            return GuardrailResult.block(
                f"Input exceeds max length ({len(user_text)} > {self._max_length})",
                rule_name="max_length",
            )

        lower = user_text.lower()

        # Keyword blocklist
        for kw in self._block_keywords:
            if kw in lower:
                return GuardrailResult.block(
                    f"Blocked keyword: {kw!r}",
                    rule_name="keyword_blocklist",
                )

        # Topic blocklist
        for topic in self._topic_blocklist:
            if topic in lower:
                return GuardrailResult.block(
                    f"Blocked topic: {topic!r}",
                    rule_name="topic_blocklist",
                )

        # Regex patterns
        for pat in self._block_patterns_in:
            if pat.search(user_text):
                return GuardrailResult.block(
                    f"Input matches blocked pattern: {pat.pattern[:60]}",
                    rule_name="block_pattern",
                )

        # PII detection
        if self._pii_detection:
            for name, pat in _PII_PATTERNS:
                if pat.search(user_text):
                    return GuardrailResult.block(
                        f"PII detected in input: {name}",
                        rule_name=f"pii_{name}",
                    )

        # Prompt injection
        if self._prompt_injection:
            for pat in _INJECTION_PATTERNS:
                if pat.search(user_text):
                    return GuardrailResult.block(
                        "Potential prompt injection detected",
                        rule_name="prompt_injection",
                    )

        return GuardrailResult.ok()

    # ── Output check / redaction ───────────────────────────────────────────
    def check_output(self, text: str) -> tuple[GuardrailResult, str]:
        """
        Check and optionally redact output text.
        Returns (GuardrailResult, processed_text).
        If allowed=False the caller should use the returned (blocked) text directly.
        """
        if not self.enabled or not text:
            return GuardrailResult.ok(), text

        # Regex block patterns
        for pat in self._block_patterns_out:
            if pat.search(text):
                return (
                    GuardrailResult.block(
                        f"Output matches blocked pattern: {pat.pattern[:60]}",
                        rule_name="output_block_pattern",
                    ),
                    self._block_message,
                )

        # PII redaction — modifies text in-place rather than blocking
        if self._pii_redaction:
            for name, pat in _PII_PATTERNS:
                replacement = f"[{name.upper()}_REDACTED]"
                text, n = pat.subn(replacement, text)
                if n:
                    logger.debug(
                        "Guardrails: redacted %d %s instance(s) from output", n, name
                    )

        return GuardrailResult.ok(), text
