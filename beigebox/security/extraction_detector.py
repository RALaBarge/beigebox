"""
Model Extraction Attack Detector — OWASP LLM10:2025 Prevention

Detects and prevents model extraction attacks through specialized analysis:

  - Query Diversity Analysis: Detects systematic probing of model behavior through
    unusual token entropy and prompt diversity patterns.
  - Instruction Pattern Detection: Identifies systematic command injection and
    function call probing attempts.
  - Token Distribution Analysis: Monitors for queries designed to probe logit
    distributions and extract softmax probabilities.
  - Prompt Inversion Detection: Detects attempts to reconstruct system prompts
    through direct or obfuscated queries.

Target Detection Rates:
  - Functional extraction: >80% TPR
  - Prompt inversion: >75% TPR
  - Training data extraction: >70% TPR
  - False positive rate: <2% on legitimate traffic

Algorithm:
  - Per-session baseline established in first 20 messages
  - Metrics tracked in rolling 100-message window
  - 2.5 sigma deviation triggers alert
  - Weighted multi-layer scoring (diversity 30%, instructions 30%, token_variance 20%, inversion 20%)
  - Risk levels: low (0.0-0.3), medium (0.3-0.6), high (0.6-0.8), critical (0.8-1.0)

Performance: ~5ms per request (entropy calc + pattern matching), acceptable for security layer.
"""

import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


# ── Risk Levels ─────────────────────────────────────────────────────────────

class RiskLevel(str, Enum):
    """Extraction attack risk classification."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ── Data Classes ────────────────────────────────────────────────────────────

@dataclass
class ExtractionRiskScore:
    """Result of extraction risk analysis."""
    risk_level: RiskLevel
    confidence: float  # 0.0-1.0
    triggers: list[str]  # Which patterns fired
    reason: str  # Human-readable explanation
    score: float = 0.0  # Composite risk score 0.0-1.0


@dataclass
class SessionMetrics:
    """Per-session metrics for extraction detection."""
    session_id: str
    user_id: str = ""
    query_tokens: deque = field(default_factory=lambda: deque(maxlen=100))
    query_lengths: deque = field(default_factory=lambda: deque(maxlen=100))
    instruction_keywords: deque = field(default_factory=lambda: deque(maxlen=100))
    response_tokens: deque = field(default_factory=lambda: deque(maxlen=100))
    token_variance_history: deque = field(default_factory=lambda: deque(maxlen=50))
    inversion_attempt_count: int = 0
    baseline_established: bool = False
    baseline_count: int = 0
    created_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


# ── Extraction Detector ────────────────────────────────────────────────────

class ExtractionDetector:
    """
    Specialized detector for OWASP LLM10:2025 (Model Extraction) attacks.

    Detects four main extraction vectors:
    1. Query Diversity Analysis (detect systematic probing)
    2. Instruction Pattern Detection (command injection)
    3. Token Distribution Analysis (logit probing)
    4. Prompt Inversion Detection (system prompt reconstruction)

    Per-session tracking with rolling baseline + 2.5 sigma deviation detection.
    """

    # Inversion attempt patterns
    INVERSION_PATTERNS = [
        r"what are your (system\s+)?instructions?",
        r"reveal (your )?system prompt",
        r"show (me )?your base prompt",
        r"tell me (your )?(core\s+)?instructions?",
        r"tell me (your )?system prompt",
        r"what is your system (message|prompt|instruction)",
        r"give (me )?your (system\s+)?(prompt|instruction)",
        r"what (was|are) you (instructed|told) to do",
        r"what (is|was) your (primary\s+)?objective",
        r"how were you (constructed|built|designed)",
        r"what (are|is) (your|the) (initial\s+)?system (prompt|message)",
        r"extract.*?(system|initial).*?(prompt|instruction|message)",
        r"print.*?(system|prompt|instruction)",
        r"debug.*?(system|prompt)",
    ]

    # Command/instruction keywords
    COMMAND_KEYWORDS = {
        "call", "execute", "invoke", "function", "tool", "method", "run",
        "eval", "exec", "import", "load", "plugin", "extension", "hook",
        "api", "endpoint", "route", "service", "rpc", "procedure",
    }

    def __init__(
        self,
        diversity_threshold: float = 2.5,
        instruction_frequency_threshold: int = 10,
        token_variance_threshold: float = 0.01,
        inversion_attempt_threshold: int = 3,
        baseline_window: int = 20,
        analysis_window: int = 100,
        risk_scoring_weights: Optional[dict] = None,
    ):
        """
        Initialize extraction detector.

        Args:
            diversity_threshold: Std deviations above baseline to flag (token entropy)
            instruction_frequency_threshold: Max instruction patterns in analysis window
            token_variance_threshold: Min variance in token distribution (< = suspicious)
            inversion_attempt_threshold: Max inversion attempts per session
            baseline_window: First N messages to establish baseline
            analysis_window: Rolling window for pattern analysis
            risk_scoring_weights: Dict of (diversity, instructions, token_variance, inversion)
        """
        self.diversity_threshold = diversity_threshold
        self.instruction_frequency_threshold = instruction_frequency_threshold
        self.token_variance_threshold = token_variance_threshold
        self.inversion_attempt_threshold = inversion_attempt_threshold
        self.baseline_window = baseline_window
        self.analysis_window = analysis_window

        if risk_scoring_weights is None:
            risk_scoring_weights = {
                "diversity": 0.25,
                "instructions": 0.25,
                "token_variance": 0.25,
                "inversion": 0.25,
            }
        self.risk_weights = risk_scoring_weights

        # Pre-compile inversion patterns
        self.inversion_regex = [re.compile(p, re.IGNORECASE) for p in self.INVERSION_PATTERNS]

        # Session tracking
        self._lock = Lock()
        self._sessions: dict[str, SessionMetrics] = {}

        logger.info(
            "ExtractionDetector initialized ("
            "diversity_threshold=%.1f, instruction_threshold=%d, "
            "token_variance_threshold=%.4f, inversion_threshold=%d, "
            "baseline_window=%d, analysis_window=%d)",
            diversity_threshold,
            instruction_frequency_threshold,
            token_variance_threshold,
            inversion_attempt_threshold,
            baseline_window,
            analysis_window,
        )

    def track_session(self, session_id: str, user_id: str = "") -> None:
        """Initialize session tracking."""
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = SessionMetrics(session_id=session_id, user_id=user_id)

    def check_request(
        self, session_id: str, user_id: str, prompt: str, model: str
    ) -> ExtractionRiskScore:
        """
        Analyze incoming request for extraction indicators.

        Returns ExtractionRiskScore with detected patterns.
        """
        if not session_id or not prompt:
            return ExtractionRiskScore(
                risk_level=RiskLevel.LOW,
                confidence=0.0,
                triggers=[],
                reason="Insufficient data",
            )

        self.track_session(session_id, user_id)

        with self._lock:
            session = self._sessions[session_id]
            session.last_seen = time.time()

            triggers = []
            scores = {}

            # Layer 1: Query Diversity Analysis
            diversity_score = self._analyze_query_diversity(session, prompt)
            if diversity_score > 0.5:
                triggers.append("high_query_diversity")
            scores["diversity"] = diversity_score

            # Layer 2: Instruction Pattern Detection
            instruction_score = self._analyze_instruction_patterns(session, prompt)
            if instruction_score > 0.5:
                triggers.append("instruction_pattern_detected")
            scores["instructions"] = instruction_score

            # Layer 4: Prompt Inversion Detection
            inversion_score = self._detect_prompt_inversion(session, prompt)
            if inversion_score > 0.5:
                triggers.append("inversion_attempt_detected")
            scores["inversion"] = inversion_score

            # Compute composite risk
            risk_score = self._compute_risk_score(scores)

            # Inversion attempts are critical security threat - escalate risk
            if "inversion_attempt_detected" in triggers:
                risk_score = max(risk_score, 0.85)  # At least CRITICAL on first detection

            risk_level = self._score_to_level(risk_score)

            reason = self._generate_reason(triggers, scores)

            return ExtractionRiskScore(
                risk_level=risk_level,
                confidence=min(1.0, len(triggers) / 4),  # 0-1 based on triggers
                triggers=triggers,
                reason=reason,
                score=risk_score,
            )

    def check_response(
        self, session_id: str, response: str, tokens_used: int = 0
    ) -> ExtractionRiskScore:
        """
        Analyze response for extraction signals.

        Checks for unusual response patterns that might indicate extraction attacks.
        """
        if not session_id or not response:
            return ExtractionRiskScore(
                risk_level=RiskLevel.LOW,
                confidence=0.0,
                triggers=[],
                reason="Insufficient data",
            )

        self.track_session(session_id)

        with self._lock:
            session = self._sessions[session_id]
            session.last_seen = time.time()

            triggers = []
            scores = {}

            # Layer 3: Token Distribution Analysis
            token_score = self._analyze_token_distribution(session, response, tokens_used)
            if token_score > 0.5:
                triggers.append("suspicious_token_distribution")
            scores["token_variance"] = token_score

            # Layer 1: Response Diversity
            diversity_score = self._analyze_response_diversity(session, response)
            if diversity_score > 0.5:
                triggers.append("response_diversity_anomaly")
            scores["diversity"] = diversity_score

            risk_score = self._compute_risk_score(scores)
            risk_level = self._score_to_level(risk_score)

            reason = self._generate_reason(triggers, scores)

            return ExtractionRiskScore(
                risk_level=risk_level,
                confidence=min(1.0, len(triggers) / 4),
                triggers=triggers,
                reason=reason,
                score=risk_score,
            )

    def analyze_pattern(self, session_id: str) -> dict:
        """
        Full session-level pattern analysis.

        Returns detailed breakdown of extraction risk indicators.
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return {
                    "session_id": session_id,
                    "status": "unknown_session",
                    "extraction_risk_score": 0.0,
                    "pattern_breakdown": {},
                    "recommendations": [],
                }

            # Analyze all accumulated metrics
            breakdown = {
                "total_queries": len(session.query_tokens),
                "baseline_established": session.baseline_established,
                "inversion_attempts": session.inversion_attempt_count,
                "mean_query_length": float(np.mean(session.query_lengths)) if session.query_lengths else 0.0,
                "std_query_length": float(np.std(session.query_lengths)) if len(session.query_lengths) > 1 else 0.0,
                "instruction_pattern_frequency": sum(session.instruction_keywords) / max(1, len(session.instruction_keywords)),
            }

            # Calculate overall risk
            scores = {
                "diversity": breakdown["std_query_length"] / max(1.0, breakdown["mean_query_length"]),
                "instructions": breakdown["instruction_pattern_frequency"],
                "token_variance": len(session.token_variance_history) > 0 and min(session.token_variance_history),
                "inversion": min(1.0, session.inversion_attempt_count / max(1.0, self.inversion_attempt_threshold)),
            }

            overall_risk = self._compute_risk_score(scores)
            risk_level = self._score_to_level(overall_risk)

            # Recommendations
            recommendations = []
            if session.inversion_attempt_count >= self.inversion_attempt_threshold:
                recommendations.append("CRITICAL: Multiple prompt inversion attempts detected. Consider rate limiting or blocking.")
            if scores["instructions"] > 0.7:
                recommendations.append("HIGH: Excessive instruction/command patterns. Verify legitimate use case.")
            if scores["diversity"] > self.diversity_threshold:
                recommendations.append("MEDIUM: Unusual query diversity. Monitor for sustained pattern.")

            return {
                "session_id": session_id,
                "user_id": session.user_id,
                "status": "active",
                "extraction_risk_score": overall_risk,
                "risk_level": risk_level.value,
                "pattern_breakdown": breakdown,
                "recommendations": recommendations,
                "created_at": session.created_at,
                "last_seen": session.last_seen,
            }

    # ───────────────────────────────────────────────────────────────────────
    # Layer 1: Query Diversity Analysis
    # ───────────────────────────────────────────────────────────────────────

    def _analyze_query_diversity(self, session: SessionMetrics, prompt: str) -> float:
        """
        Detect unusual query diversity (extraction probes many behaviors).

        Returns risk score 0.0-1.0.
        """
        # Tokenize and estimate unique tokens
        tokens = self._tokenize_prompt(prompt)
        unique_ratio = len(set(tokens)) / max(1, len(tokens))

        session.query_tokens.append(unique_ratio)
        session.query_lengths.append(len(tokens))

        # Need baseline to compare
        if len(session.query_tokens) < self.baseline_window:
            session.baseline_count += 1
            if session.baseline_count >= self.baseline_window:
                session.baseline_established = True
            return 0.0

        # Calculate entropy of token diversity
        recent_diversity = list(session.query_tokens)[-self.analysis_window:]
        if not recent_diversity:
            return 0.0

        mean_diversity = float(np.mean(recent_diversity))
        std_diversity = float(np.std(recent_diversity))

        if std_diversity < 1e-6:
            return 0.0

        # Current query vs baseline
        current_z = (unique_ratio - mean_diversity) / std_diversity

        # High z-score = unusual diversity
        if current_z > self.diversity_threshold:
            logger.warning(
                "Extraction: high_query_diversity (session=%s, z=%.2f, unique_ratio=%.2f)",
                session.session_id, current_z, unique_ratio,
            )
            return min(1.0, abs(current_z) / (self.diversity_threshold * 2))

        return 0.0

    def _analyze_response_diversity(self, session: SessionMetrics, response: str) -> float:
        """Analyze response for unusual diversity patterns."""
        tokens = self._tokenize_prompt(response)
        unique_ratio = len(set(tokens)) / max(1, len(tokens))

        session.response_tokens.append(unique_ratio)

        if len(session.response_tokens) < 5:
            return 0.0

        recent = list(session.response_tokens)[-20:]
        mean_resp = float(np.mean(recent))
        std_resp = float(np.std(recent))

        if std_resp < 1e-6:
            return 0.0

        # Very consistent response diversity = suspicious
        if std_resp < 0.05:
            return 0.5

        return 0.0

    # ───────────────────────────────────────────────────────────────────────
    # Layer 2: Command/Instruction Pattern Detection
    # ───────────────────────────────────────────────────────────────────────

    def _analyze_instruction_patterns(self, session: SessionMetrics, prompt: str) -> float:
        """
        Detect systematic command injection and function call probing.

        Returns risk score 0.0-1.0.
        """
        prompt_lower = prompt.lower()

        # Count instruction keywords
        keyword_count = sum(
            1 for kw in self.COMMAND_KEYWORDS
            if re.search(rf"\b{kw}\b", prompt_lower)
        )

        session.instruction_keywords.append(keyword_count > 0)

        if len(session.instruction_keywords) < self.baseline_window:
            return 0.0

        # Check frequency in recent window
        recent = list(session.instruction_keywords)[-self.analysis_window:]
        instruction_frequency = sum(recent) / len(recent)

        if instruction_frequency > (self.instruction_frequency_threshold / self.analysis_window):
            logger.warning(
                "Extraction: instruction_pattern (session=%s, frequency=%.2f)",
                session.session_id, instruction_frequency,
            )
            return min(1.0, instruction_frequency * 2)

        return 0.0

    # ───────────────────────────────────────────────────────────────────────
    # Layer 3: Token Distribution Analysis
    # ───────────────────────────────────────────────────────────────────────

    def _analyze_token_distribution(
        self, session: SessionMetrics, response: str, tokens_used: int
    ) -> float:
        """
        Detect queries designed to probe logit distributions.

        Returns risk score 0.0-1.0.
        """
        if tokens_used <= 0:
            tokens_used = len(self._tokenize_prompt(response))

        # Very short responses with many tokens = suspicious
        resp_length = len(response)
        if resp_length > 0 and tokens_used / resp_length > 0.5:
            # High token density = possible logit probing
            variance = tokens_used / max(1, resp_length)
            session.token_variance_history.append(variance)

            if len(session.token_variance_history) >= 5:
                recent_variance = list(session.token_variance_history)[-20:]
                min_variance = min(recent_variance)

                if min_variance < self.token_variance_threshold:
                    logger.warning(
                        "Extraction: token_distribution_anomaly (session=%s, variance=%.4f)",
                        session.session_id, min_variance,
                    )
                    return min(1.0, (self.token_variance_threshold - min_variance) / self.token_variance_threshold)

        return 0.0

    # ───────────────────────────────────────────────────────────────────────
    # Layer 4: Prompt Inversion Detection
    # ───────────────────────────────────────────────────────────────────────

    def _detect_prompt_inversion(self, session: SessionMetrics, prompt: str) -> float:
        """
        Detect attempts to reconstruct system prompt.

        Returns risk score 0.0-1.0.
        """
        # Check against inversion patterns
        is_inversion = any(
            regex.search(prompt) for regex in self.inversion_regex
        )

        if is_inversion:
            session.inversion_attempt_count += 1
            logger.warning(
                "Extraction: prompt_inversion_attempt (session=%s, count=%d)",
                session.session_id, session.inversion_attempt_count,
            )

            # Single inversion attempt = 0.7 risk, escalates to 1.0 at threshold
            if session.inversion_attempt_count >= self.inversion_attempt_threshold:
                return 1.0
            else:
                # Escalate: 0.7 at count=1, up to 0.95 at count=threshold-1
                return 0.7 + (min(session.inversion_attempt_count, self.inversion_attempt_threshold - 1) / self.inversion_attempt_threshold) * 0.25

        return 0.0

    # ───────────────────────────────────────────────────────────────────────
    # Scoring and Utilities
    # ───────────────────────────────────────────────────────────────────────

    def _compute_risk_score(self, scores: dict) -> float:
        """Compute weighted composite risk score."""
        total_score = 0.0
        total_weight = 0.0

        for key, weight in self.risk_weights.items():
            score = scores.get(key, 0.0)
            if isinstance(score, bool):
                score = 1.0 if score else 0.0
            elif isinstance(score, (int, float)):
                score = min(1.0, float(score))
            total_score += score * weight
            total_weight += weight

        if total_weight > 0:
            return min(1.0, total_score / total_weight)
        return 0.0

    def _score_to_level(self, score: float) -> RiskLevel:
        """Convert numeric score to risk level."""
        if score >= 0.8:
            return RiskLevel.CRITICAL
        elif score >= 0.6:
            return RiskLevel.HIGH
        elif score >= 0.3:
            return RiskLevel.MEDIUM
        else:
            return RiskLevel.LOW

    def _generate_reason(self, triggers: list[str], scores: dict) -> str:
        """Generate human-readable reason for risk assessment."""
        if not triggers:
            return "No extraction indicators detected."

        reason_parts = []
        for trigger in triggers:
            if trigger == "high_query_diversity":
                reason_parts.append("Unusual query diversity detected (possible systematic probing)")
            elif trigger == "instruction_pattern_detected":
                reason_parts.append("Excessive instruction/command patterns detected")
            elif trigger == "inversion_attempt_detected":
                reason_parts.append("Prompt inversion attempt detected")
            elif trigger == "suspicious_token_distribution":
                reason_parts.append("Suspicious token distribution patterns detected")
            elif trigger == "response_diversity_anomaly":
                reason_parts.append("Response diversity anomaly detected")

        return ". ".join(reason_parts) + "."

    def _tokenize_prompt(self, text: str) -> list[str]:
        """Simple tokenization (~4 chars per token for estimation)."""
        if not text:
            return []
        # Split on whitespace and punctuation
        tokens = re.findall(r"\b\w+\b", text.lower())
        return tokens

    def cleanup_stale_sessions(self, ttl_seconds: int = 1800) -> None:
        """Remove sessions with no recent activity."""
        with self._lock:
            now = time.time()
            stale = [
                sid for sid, sess in self._sessions.items()
                if now - sess.last_seen > ttl_seconds
            ]
            for sid in stale:
                del self._sessions[sid]
            if stale:
                logger.debug("Cleaned up %d stale extraction detector sessions", len(stale))

    def is_baseline_established(self, session_id: str) -> bool:
        """Public probe for proxy-side observability decisions.

        Avoids reaching into ``_sessions`` from outside (Grok 2026-04-26).
        """
        with self._lock:
            session = self._sessions.get(session_id)
            return bool(session and session.baseline_established)

    def get_session_stats(self, session_id: str) -> dict:
        """Get current stats for a session."""
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return {}
            return {
                "session_id": session_id,
                "total_queries": len(session.query_tokens),
                "baseline_established": session.baseline_established,
                "inversion_attempts": session.inversion_attempt_count,
                "created_at": session.created_at,
                "last_seen": session.last_seen,
            }
