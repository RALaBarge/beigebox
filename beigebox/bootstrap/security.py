"""Security detection modules bootstrap.

Builds:
  - ExtractionDetector       (OWASP LLM10:2025; opt-out via config)
  - EnhancedInjectionGuard   (semantic + pattern detection)
  - RAGContentScanner        (pre-embed poisoning detection)

``audit_logger`` and ``honeypot_manager`` are intentionally not built
here — they were guarded behind always-None placeholders in the previous
lifespan and remain disabled until a dedicated commit revives them. The
AppState dataclass already defaults them to None.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SecurityBundle:
    extraction_detector: Any  # ExtractionDetector | None
    injection_guard: Any  # EnhancedInjectionGuard | None
    rag_scanner: Any  # RAGContentScanner | None


def build_security(cfg: dict) -> SecurityBundle:
    sec_cfg = cfg.get("security", {})

    # Model Extraction Attack Detection (OWASP LLM10:2025)
    from beigebox.security.extraction_detector import ExtractionDetector
    extraction_cfg = sec_cfg.get("extraction_detection", {})
    extraction_detector = None
    if extraction_cfg.get("enabled", True):
        extraction_detector = ExtractionDetector(
            diversity_threshold=extraction_cfg.get("diversity_threshold", 2.5),
            instruction_frequency_threshold=extraction_cfg.get(
                "instruction_frequency_threshold", 10
            ),
            token_variance_threshold=extraction_cfg.get("token_variance_threshold", 0.01),
            inversion_attempt_threshold=extraction_cfg.get(
                "inversion_attempt_threshold", 3
            ),
            baseline_window=extraction_cfg.get("baseline_window", 20),
            analysis_window=extraction_cfg.get("analysis_window", 100),
        )
        logger.info("Model extraction detection: ENABLED")
    else:
        logger.info("Model extraction detection: disabled")

    # Security Audit & Detection Modules (P1 Security Hardening)
    from beigebox.security.enhanced_injection_guard import EnhancedInjectionGuard
    from beigebox.security.rag_content_scanner import RAGContentScanner

    # Enhanced Injection Guard (semantic + pattern detection)
    injection_guard = (
        EnhancedInjectionGuard()
        if sec_cfg.get("injection_guard", {}).get("enabled", True)
        else None
    )
    if injection_guard:
        logger.info(
            "Enhanced Injection Guard: initialized with semantic + pattern detection"
        )

    # RAG Content Scanner (pre-embed poisoning detection)
    rag_scanner = (
        RAGContentScanner()
        if sec_cfg.get("rag_scanner", {}).get("enabled", True)
        else None
    )
    if rag_scanner:
        logger.info("RAG Content Scanner: initialized for pre-embed detection")

    return SecurityBundle(
        extraction_detector=extraction_detector,
        injection_guard=injection_guard,
        rag_scanner=rag_scanner,
    )


__all__ = ["SecurityBundle", "build_security"]
