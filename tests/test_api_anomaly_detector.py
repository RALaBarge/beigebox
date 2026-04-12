"""
Tests for API Anomaly Detector (P1-C Security Hardening).

Covers:
  - Z-score calculation and threshold logic
  - Signal detection: request rate, error rate, model switching, latency, payload, UA
  - Sensitivity presets (low/medium/high)
  - Risk score computation
  - Recommended action mapping
  - Cold start handling (no baseline)
  - False positive handling (legitimate spikes)
  - SQLite baseline persistence
  - Full pipeline integration (record -> detect)
  - Tool wrapper (APIAnomalyDetectorTool)
  - Edge cases: empty sessions, single request, boundary values
"""

import json
import tempfile
import time

import pytest

from beigebox.security.anomaly_detector import (
    APIAnomalyDetector,
    AnomalyBaselinesDB,
    RequestRecord,
    SessionStats,
    SENSITIVITY_PRESETS,
    _compute_risk_score,
    _recommended_action,
)
from beigebox.security.anomaly_rules import RuleSet, RuleSeverity, RuleAction, apply_config_to_rules
from beigebox.tools.api_anomaly_detector_tool import APIAnomalyDetectorTool


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def detector():
    """Basic detector with medium sensitivity and low min_baseline for fast tests."""
    return APIAnomalyDetector(
        sensitivity="medium",
        min_baseline_size=5,
        window_seconds=300,
    )


@pytest.fixture
def detector_high():
    """High sensitivity detector."""
    return APIAnomalyDetector(
        sensitivity="high",
        min_baseline_size=5,
    )


@pytest.fixture
def detector_low():
    """Low sensitivity detector."""
    return APIAnomalyDetector(
        sensitivity="low",
        min_baseline_size=5,
    )


@pytest.fixture
def detector_with_db():
    """Detector with SQLite persistence."""
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        d = APIAnomalyDetector(
            sensitivity="medium",
            min_baseline_size=5,
            db_path=f.name,
        )
        yield d
        d.db.close()


@pytest.fixture
def tool(detector):
    """Tool wrapper around detector."""
    t = APIAnomalyDetectorTool(detector=detector)
    return t


def _feed_normal_traffic(detector, ip="10.0.0.1", count=20, model="gpt-4"):
    """Feed normal traffic pattern to establish baseline."""
    for i in range(count):
        detector.record_request(
            ip=ip,
            user_agent="Mozilla/5.0",
            api_key="key_abc",
            model=model,
            request_bytes=500 + (i * 10),
            status_code=200,
            latency_ms=100.0 + (i % 5) * 10,
            conversation_id="conv_1",
        )
        # Spread timestamps slightly to avoid rate spike on initial fill
        if hasattr(detector, '_sessions') and ip in detector._sessions:
            session = detector._sessions[ip]
            if session.request_history:
                session.request_history[-1].timestamp -= (count - i) * 5


# ═══════════════════════════════════════════════════════════════════════════
# UNIT TESTS
# ═══════════════════════════════════════════════════════════════════════════

class TestZScoreCalculation:
    """Unit tests for z-score logic."""

    @pytest.mark.unit
    def test_zscore_normal(self, detector):
        """Z-score of mean value is 0."""
        z = detector._zscore(100.0, 100.0, 10.0)
        assert z == 0.0

    @pytest.mark.unit
    def test_zscore_above_mean(self, detector):
        """Z-score above mean is positive."""
        z = detector._zscore(130.0, 100.0, 10.0)
        assert z == pytest.approx(3.0)

    @pytest.mark.unit
    def test_zscore_below_mean(self, detector):
        """Z-score below mean is negative."""
        z = detector._zscore(70.0, 100.0, 10.0)
        assert z == pytest.approx(-3.0)

    @pytest.mark.unit
    def test_zscore_zero_std(self, detector):
        """Z-score with zero std returns 0.0 (no division by zero)."""
        z = detector._zscore(150.0, 100.0, 0.0)
        assert z == 0.0

    @pytest.mark.unit
    def test_zscore_near_zero_std(self, detector):
        """Z-score with near-zero std returns 0.0."""
        z = detector._zscore(150.0, 100.0, 1e-9)
        assert z == 0.0

    @pytest.mark.unit
    def test_zscore_large_deviation(self, detector):
        """Large z-score for extreme outlier."""
        z = detector._zscore(200.0, 50.0, 10.0)
        assert z == pytest.approx(15.0)


class TestRiskScoreComputation:
    """Unit tests for risk score calculation."""

    @pytest.mark.unit
    def test_no_rules_zero_score(self):
        """No triggered rules = 0.0 risk."""
        assert _compute_risk_score([]) == 0.0

    @pytest.mark.unit
    def test_single_rule_score(self):
        """Single rule gives its weight."""
        score = _compute_risk_score(["request_rate_spike"])
        assert score == pytest.approx(0.30)

    @pytest.mark.unit
    def test_multiple_rules_compound(self):
        """Multiple rules add up."""
        score = _compute_risk_score(["request_rate_spike", "error_rate_spike"])
        assert score == pytest.approx(0.55)

    @pytest.mark.unit
    def test_risk_capped_at_one(self):
        """Risk score never exceeds 1.0."""
        all_rules = [
            "request_rate_spike", "error_rate_spike", "model_switching",
            "latency_anomaly", "ua_instability", "payload_size_anomaly",
            "ip_instability",
        ]
        score = _compute_risk_score(all_rules)
        assert score <= 1.0

    @pytest.mark.unit
    def test_unknown_rule_gets_default_weight(self):
        """Unknown rule gets 0.05 default weight."""
        score = _compute_risk_score(["unknown_rule"])
        assert score == pytest.approx(0.05)


class TestRecommendedAction:
    """Unit tests for action recommendation."""

    @pytest.mark.unit
    def test_low_risk_allow(self):
        assert _recommended_action(0.1, "warn") == "allow"

    @pytest.mark.unit
    def test_medium_risk_warn(self):
        assert _recommended_action(0.4, "warn") == "warn"

    @pytest.mark.unit
    def test_high_risk_rate_limit(self):
        assert _recommended_action(0.8, "warn") == "rate_limit"

    @pytest.mark.unit
    def test_block_mode_medium_risk(self):
        assert _recommended_action(0.4, "block") == "block"

    @pytest.mark.unit
    def test_rate_limit_mode(self):
        assert _recommended_action(0.4, "rate_limit") == "rate_limit"

    @pytest.mark.unit
    def test_zero_risk_always_allow(self):
        assert _recommended_action(0.0, "block") == "allow"


class TestSensitivityPresets:
    """Test that sensitivity presets configure thresholds correctly."""

    @pytest.mark.unit
    def test_low_sensitivity_thresholds(self, detector_low):
        assert detector_low.z_threshold == 4.0
        assert detector_low.request_rate_threshold == 10
        assert detector_low.model_switch_threshold == 12

    @pytest.mark.unit
    def test_medium_sensitivity_thresholds(self, detector):
        assert detector.z_threshold == 3.0
        assert detector.request_rate_threshold == 5
        assert detector.model_switch_threshold == 8

    @pytest.mark.unit
    def test_high_sensitivity_thresholds(self, detector_high):
        assert detector_high.z_threshold == 2.0
        assert detector_high.request_rate_threshold == 3
        assert detector_high.model_switch_threshold == 4

    @pytest.mark.unit
    def test_preset_keys_valid(self):
        for name, preset in SENSITIVITY_PRESETS.items():
            assert "z_threshold" in preset
            assert "request_rate_threshold" in preset
            assert "min_baseline_size" in preset


# ═══════════════════════════════════════════════════════════════════════════
# SIGNAL DETECTION TESTS
# ═══════════════════════════════════════════════════════════════════════════

class TestRequestRateDetection:
    """Test request rate spike detection."""

    @pytest.mark.unit
    def test_no_anomaly_on_first_request(self, detector):
        """First request is never anomalous."""
        is_anom, rules = detector.is_anomalous("10.0.0.1", "Mozilla/5.0")
        assert not is_anom
        assert rules == []

    @pytest.mark.unit
    def test_rate_spike_cold_start(self):
        """Rate spike triggers during cold start via simple threshold."""
        # Use high min_baseline to ensure cold start path
        d = APIAnomalyDetector(sensitivity="medium", min_baseline_size=100)
        ip = "10.0.0.99"
        # Rapidly add requests (all within 1 second) — exceeds threshold=5
        for i in range(10):
            d.record_request(
                ip=ip, user_agent="bot", api_key="k", model="m",
                request_bytes=100, status_code=200, latency_ms=50.0,
            )
        is_anom, rules = d.is_anomalous(ip, "bot")
        assert is_anom
        assert "request_rate_spike" in rules

    @pytest.mark.unit
    def test_normal_rate_not_flagged(self, detector):
        """Normal request rate is not flagged."""
        ip = "10.0.0.2"
        _feed_normal_traffic(detector, ip=ip, count=10)
        is_anom, rules = detector.is_anomalous(ip, "Mozilla/5.0")
        assert not is_anom


class TestErrorRateDetection:
    """Test error rate spike detection."""

    @pytest.mark.unit
    def test_high_error_rate_cold_start(self, detector):
        """High error rate triggers during cold start."""
        ip = "10.0.0.50"
        # 4 requests, 3 errors = 75% error rate
        for status in [401, 403, 500, 200]:
            detector.record_request(
                ip=ip, user_agent="ua", api_key="k", model="m",
                request_bytes=200, status_code=status, latency_ms=100.0,
            )
        is_anom, rules = detector.is_anomalous(ip, "ua")
        assert is_anom
        assert "error_rate_spike" in rules

    @pytest.mark.unit
    def test_low_error_rate_not_flagged(self, detector):
        """Low error rate is not flagged."""
        ip = "10.0.0.51"
        for _ in range(9):
            detector.record_request(
                ip=ip, user_agent="ua", api_key="k", model="m",
                request_bytes=200, status_code=200, latency_ms=100.0,
            )
        # 1 error out of 10 = 10% < 30% threshold
        detector.record_request(
            ip=ip, user_agent="ua", api_key="k", model="m",
            request_bytes=200, status_code=500, latency_ms=100.0,
        )
        is_anom, rules = detector.is_anomalous(ip, "ua")
        # Should not trigger error_rate_spike (10% < 30%)
        assert "error_rate_spike" not in rules


class TestModelSwitchingDetection:
    """Test model switching detection."""

    @pytest.mark.unit
    def test_excessive_model_switching(self, detector):
        """Too many model switches triggers alert."""
        ip = "10.0.0.60"
        for i in range(10):
            detector.record_request(
                ip=ip, user_agent="ua", api_key="k",
                model=f"model_{i}",
                request_bytes=200, status_code=200, latency_ms=100.0,
            )
        is_anom, rules = detector.is_anomalous(ip, "ua")
        assert "model_switching" in rules

    @pytest.mark.unit
    def test_normal_model_usage_not_flagged(self, detector):
        """Using 2-3 models is normal."""
        ip = "10.0.0.61"
        for model in ["gpt-4", "gpt-4", "gpt-3.5", "gpt-4"]:
            detector.record_request(
                ip=ip, user_agent="ua", api_key="k", model=model,
                request_bytes=200, status_code=200, latency_ms=100.0,
            )
        is_anom, rules = detector.is_anomalous(ip, "ua")
        assert "model_switching" not in rules


class TestLatencyAnomalyDetection:
    """Test latency anomaly detection."""

    @pytest.mark.unit
    def test_latency_anomaly_low(self, detector):
        """Sub-baseline latency triggers alert."""
        ip = "10.0.0.70"
        # Establish baseline: ~100ms with low variance
        for _ in range(10):
            detector.record_request(
                ip=ip, user_agent="ua", api_key="k", model="m",
                request_bytes=200, status_code=200, latency_ms=100.0,
            )
        # Now a suspiciously fast request
        detector.record_request(
            ip=ip, user_agent="ua", api_key="k", model="m",
            request_bytes=200, status_code=200, latency_ms=1.0,
        )
        is_anom, rules = detector.is_anomalous(ip, "ua")
        assert "latency_anomaly" in rules

    @pytest.mark.unit
    def test_latency_anomaly_high(self, detector):
        """Super-high latency also triggers alert."""
        ip = "10.0.0.71"
        for _ in range(10):
            detector.record_request(
                ip=ip, user_agent="ua", api_key="k", model="m",
                request_bytes=200, status_code=200, latency_ms=100.0,
            )
        detector.record_request(
            ip=ip, user_agent="ua", api_key="k", model="m",
            request_bytes=200, status_code=200, latency_ms=10000.0,
        )
        is_anom, rules = detector.is_anomalous(ip, "ua")
        assert "latency_anomaly" in rules

    @pytest.mark.unit
    def test_not_enough_history_no_latency_alert(self, detector):
        """Latency check needs 5+ samples."""
        ip = "10.0.0.72"
        detector.record_request(
            ip=ip, user_agent="ua", api_key="k", model="m",
            request_bytes=200, status_code=200, latency_ms=1.0,
        )
        is_anom, rules = detector.is_anomalous(ip, "ua")
        assert "latency_anomaly" not in rules


class TestPayloadSizeDetection:
    """Test payload size anomaly detection."""

    @pytest.mark.unit
    def test_tiny_payload_flagged(self, detector):
        """Very small payload is flagged."""
        ip = "10.0.0.80"
        _feed_normal_traffic(detector, ip=ip, count=10)
        is_anom, rules = detector.is_anomalous(ip, "ua", request_bytes=10)
        assert "payload_size_anomaly" in rules

    @pytest.mark.unit
    def test_huge_payload_flagged(self, detector):
        """Very large payload is flagged."""
        ip = "10.0.0.81"
        _feed_normal_traffic(detector, ip=ip, count=10)
        is_anom, rules = detector.is_anomalous(ip, "ua", request_bytes=200_000)
        assert "payload_size_anomaly" in rules

    @pytest.mark.unit
    def test_normal_payload_not_flagged(self, detector):
        """Normal-sized payload is not flagged."""
        ip = "10.0.0.82"
        _feed_normal_traffic(detector, ip=ip, count=10)
        is_anom, rules = detector.is_anomalous(ip, "ua", request_bytes=500)
        assert "payload_size_anomaly" not in rules


class TestUAInstabilityDetection:
    """Test User-Agent instability detection."""

    @pytest.mark.unit
    def test_multiple_uas_flagged(self, detector):
        """4+ different User-Agents triggers alert."""
        ip = "10.0.0.90"
        for ua in ["Chrome/1", "Firefox/1", "Safari/1", "Edge/1"]:
            detector.record_request(
                ip=ip, user_agent=ua, api_key="k", model="m",
                request_bytes=200, status_code=200, latency_ms=100.0,
            )
        is_anom, rules = detector.is_anomalous(ip, "Bot/1")
        assert "ua_instability" in rules

    @pytest.mark.unit
    def test_single_ua_not_flagged(self, detector):
        """Single User-Agent is normal."""
        ip = "10.0.0.91"
        for _ in range(5):
            detector.record_request(
                ip=ip, user_agent="Mozilla/5.0", api_key="k", model="m",
                request_bytes=200, status_code=200, latency_ms=100.0,
            )
        is_anom, rules = detector.is_anomalous(ip, "Mozilla/5.0")
        assert "ua_instability" not in rules


# ═══════════════════════════════════════════════════════════════════════════
# EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Edge case tests."""

    @pytest.mark.unit
    def test_empty_ip_not_anomalous(self, detector):
        """Empty IP always returns not anomalous."""
        is_anom, rules = detector.is_anomalous("", "ua")
        assert not is_anom

    @pytest.mark.unit
    def test_record_empty_ip_ignored(self, detector):
        """Recording with empty IP is silently ignored."""
        detector.record_request(
            ip="", user_agent="ua", api_key="k", model="m",
            request_bytes=200, status_code=200, latency_ms=100.0,
        )
        assert len(detector._sessions) == 0

    @pytest.mark.unit
    def test_record_empty_model_ignored(self, detector):
        """Recording with empty model is silently ignored."""
        detector.record_request(
            ip="10.0.0.1", user_agent="ua", api_key="k", model="",
            request_bytes=200, status_code=200, latency_ms=100.0,
        )
        assert len(detector._sessions) == 0

    @pytest.mark.unit
    def test_unknown_session_not_anomalous(self, detector):
        """Querying an IP with no recorded data returns not anomalous."""
        is_anom, rules = detector.is_anomalous("192.168.1.1", "ua")
        assert not is_anom

    @pytest.mark.unit
    def test_session_stats_empty(self, detector):
        """Stats for unknown IP returns empty dict."""
        stats = detector.get_session_stats("unknown_ip")
        assert stats == {}

    @pytest.mark.unit
    def test_get_anomaly_report_empty(self, detector):
        """Report with no sessions returns empty."""
        report = detector.get_anomaly_report()
        assert report["total_sessions"] == 0
        assert report["anomalous_count"] == 0
        assert report["sessions"] == []


# ═══════════════════════════════════════════════════════════════════════════
# FALSE POSITIVE HANDLING
# ═══════════════════════════════════════════════════════════════════════════

class TestFalsePositiveHandling:
    """Test that legitimate patterns don't trigger false positives."""

    @pytest.mark.unit
    def test_bulk_upload_not_flagged(self, detector):
        """Legitimate bulk upload (gradually increasing payloads) shouldn't trigger."""
        ip = "10.0.0.100"
        # Feed traffic with varied payload sizes to build a wide baseline
        for i in range(20):
            detector.record_request(
                ip=ip, user_agent="Mozilla/5.0", api_key="k", model="gpt-4",
                request_bytes=500 + i * 200,  # 500-4300 bytes range
                status_code=200, latency_ms=100.0,
            )
            if ip in detector._sessions:
                detector._sessions[ip].request_history[-1].timestamp -= (20 - i) * 5
        # Slightly above the range but not a z-score outlier
        is_anom, rules = detector.is_anomalous(ip, "Mozilla/5.0", request_bytes=5000)
        assert "payload_size_anomaly" not in rules

    @pytest.mark.unit
    def test_retry_pattern_not_flagged(self, detector):
        """A few retries (brief rate increase) shouldn't trigger with low sensitivity."""
        d = APIAnomalyDetector(sensitivity="low", min_baseline_size=5)
        ip = "10.0.0.101"
        _feed_normal_traffic(d, ip=ip, count=20)
        # 3 retries in quick succession — below low threshold of 10
        for _ in range(3):
            d.record_request(
                ip=ip, user_agent="Mozilla/5.0", api_key="k", model="gpt-4",
                request_bytes=500, status_code=200, latency_ms=100.0,
            )
        is_anom, rules = d.is_anomalous(ip, "Mozilla/5.0")
        assert "request_rate_spike" not in rules

    @pytest.mark.unit
    def test_model_selection_normal(self, detector):
        """Switching between 2-3 models is normal behavior."""
        ip = "10.0.0.102"
        models = ["gpt-4", "gpt-3.5-turbo", "claude-3"]
        for i in range(15):
            detector.record_request(
                ip=ip, user_agent="ua", api_key="k",
                model=models[i % 3],
                request_bytes=500, status_code=200, latency_ms=100.0,
            )
        is_anom, rules = detector.is_anomalous(ip, "ua")
        assert "model_switching" not in rules


# ═══════════════════════════════════════════════════════════════════════════
# INTEGRATION TESTS
# ═══════════════════════════════════════════════════════════════════════════

class TestAnalyzeMethod:
    """Integration tests for the analyze() method."""

    @pytest.mark.integration
    def test_analyze_clean_session(self, detector):
        """Analyze a clean session returns no anomalies."""
        ip = "10.0.0.200"
        _feed_normal_traffic(detector, ip=ip, count=10)
        result = detector.analyze(ip=ip)
        assert result["anomalies"] == []
        assert result["risk_score"] == 0.0
        assert result["recommended_action"] == "allow"
        assert "session_stats" in result

    @pytest.mark.integration
    def test_analyze_anomalous_session(self, detector):
        """Analyze an anomalous session returns rules and risk score."""
        ip = "10.0.0.201"
        # Create anomalous pattern: many models + high error rate
        for i in range(10):
            detector.record_request(
                ip=ip, user_agent="ua", api_key="k",
                model=f"model_{i}",
                request_bytes=200, status_code=401 if i % 2 == 0 else 200,
                latency_ms=100.0,
            )
        result = detector.analyze(ip=ip)
        assert len(result["anomalies"]) > 0
        assert result["risk_score"] > 0.0
        assert result["recommended_action"] in ("allow", "warn", "rate_limit", "block")

    @pytest.mark.integration
    def test_analyze_returns_baseline_status(self, detector):
        """Analyze includes baseline status."""
        ip = "10.0.0.202"
        detector.record_request(
            ip=ip, user_agent="ua", api_key="k", model="m",
            request_bytes=200, status_code=200, latency_ms=100.0,
        )
        result = detector.analyze(ip=ip)
        assert result["baseline_status"] == "cold_start"

    @pytest.mark.integration
    def test_analyze_all_sessions(self, detector):
        """Analyze with no IP returns report for all sessions."""
        _feed_normal_traffic(detector, ip="10.0.0.1", count=5)
        _feed_normal_traffic(detector, ip="10.0.0.2", count=5)
        result = detector.analyze()
        assert "total_sessions" in result
        assert result["total_sessions"] >= 2


class TestFullPipeline:
    """Integration tests: record requests -> detect anomalies."""

    @pytest.mark.integration
    def test_pipeline_normal_then_attack(self, detector):
        """Normal baseline followed by attack pattern gets detected."""
        ip = "10.0.0.210"
        # Phase 1: Normal traffic
        _feed_normal_traffic(detector, ip=ip, count=20)

        # Phase 2: Attack pattern (rapid fire, many errors, model switching)
        for i in range(15):
            detector.record_request(
                ip=ip, user_agent="ua", api_key="k",
                model=f"attack_model_{i}",
                request_bytes=200, status_code=401,
                latency_ms=100.0,
            )

        is_anom, rules = detector.is_anomalous(ip, "ua")
        assert is_anom
        assert len(rules) >= 1  # At least one rule triggers

    @pytest.mark.integration
    def test_pipeline_conversation_tracking(self, detector):
        """Conversation-IP mapping is tracked."""
        detector.record_request(
            ip="10.0.0.1", user_agent="ua", api_key="k", model="m",
            request_bytes=200, status_code=200, latency_ms=100.0,
            conversation_id="conv_abc",
        )
        detector.record_request(
            ip="10.0.0.2", user_agent="ua", api_key="k", model="m",
            request_bytes=200, status_code=200, latency_ms=100.0,
            conversation_id="conv_abc",
        )
        assert len(detector._conversation_ips.get("conv_abc", set())) == 2

    @pytest.mark.integration
    def test_session_stats_populated(self, detector):
        """Session stats reflect recorded data."""
        ip = "10.0.0.220"
        _feed_normal_traffic(detector, ip=ip, count=10)
        stats = detector.get_session_stats(ip)
        assert stats["ip"] == ip
        assert stats["total_requests"] == 10
        assert stats["distinct_models"] == 1
        assert stats["mean_latency_ms"] > 0


# ═══════════════════════════════════════════════════════════════════════════
# SQLITE PERSISTENCE TESTS
# ═══════════════════════════════════════════════════════════════════════════

class TestBaselinePersistence:
    """Test SQLite baseline storage."""

    @pytest.mark.integration
    def test_save_and_load_baseline(self, detector_with_db):
        """Baselines are saved and can be reloaded."""
        ip = "10.0.0.300"
        _feed_normal_traffic(detector_with_db, ip=ip, count=10)

        # Force save
        session = detector_with_db._sessions[ip]
        detector_with_db.db.save_baseline(ip, session)

        # Load
        saved = detector_with_db.db.load_baseline(ip)
        assert saved is not None
        assert saved["ip"] == ip
        assert saved["request_count"] == 10

    @pytest.mark.integration
    def test_event_recording(self, detector_with_db):
        """Anomaly events are recorded to SQLite."""
        detector_with_db.db.record_event(
            ip="10.0.0.1",
            rules=["request_rate_spike", "error_rate_spike"],
            risk_score=0.55,
            action="warn",
            meta={"user_agent": "test"},
        )
        events = detector_with_db.db.get_events()
        assert len(events) == 1
        assert events[0]["risk_score"] == 0.55
        assert "request_rate_spike" in events[0]["rules"]

    @pytest.mark.integration
    def test_event_filtering_by_ip(self, detector_with_db):
        """Events can be filtered by IP."""
        detector_with_db.db.record_event("10.0.0.1", ["r1"], 0.3, "warn")
        detector_with_db.db.record_event("10.0.0.2", ["r2"], 0.5, "warn")
        events = detector_with_db.db.get_events(ip="10.0.0.1")
        assert len(events) == 1
        assert events[0]["ip"] == "10.0.0.1"

    @pytest.mark.integration
    def test_get_all_baselines(self, detector_with_db):
        """All baselines can be retrieved."""
        _feed_normal_traffic(detector_with_db, ip="10.0.0.1", count=5)
        _feed_normal_traffic(detector_with_db, ip="10.0.0.2", count=5)
        detector_with_db.db.save_baseline("10.0.0.1", detector_with_db._sessions["10.0.0.1"])
        detector_with_db.db.save_baseline("10.0.0.2", detector_with_db._sessions["10.0.0.2"])
        baselines = detector_with_db.db.get_all_baselines()
        assert len(baselines) == 2

    @pytest.mark.integration
    def test_baseline_hydration_on_new_session(self, detector_with_db):
        """When a session is created, persisted baseline is loaded."""
        ip = "10.0.0.310"
        # Create and persist a baseline
        _feed_normal_traffic(detector_with_db, ip=ip, count=15)
        session = detector_with_db._sessions[ip]
        detector_with_db.db.save_baseline(ip, session)

        # Remove from memory
        del detector_with_db._sessions[ip]

        # Record a new request — should hydrate from DB
        detector_with_db.record_request(
            ip=ip, user_agent="ua", api_key="k", model="m",
            request_bytes=200, status_code=200, latency_ms=100.0,
        )
        new_session = detector_with_db._sessions[ip]
        # request_count should be 15 (from DB) + 1 (new request)
        assert new_session.request_count == 16

    @pytest.mark.integration
    def test_db_no_path_noop(self):
        """DB with empty path doesn't crash."""
        db = AnomalyBaselinesDB("")
        db.save_baseline("x", SessionStats(ip="x"))
        assert db.load_baseline("x") is None
        assert db.get_events() == []
        db.close()


# ═══════════════════════════════════════════════════════════════════════════
# TOOL WRAPPER TESTS
# ═══════════════════════════════════════════════════════════════════════════

class TestAPIAnomalyDetectorTool:
    """Tests for the tool wrapper."""

    @pytest.mark.unit
    def test_no_detector_error(self):
        """Tool without detector returns error."""
        t = APIAnomalyDetectorTool(detector=None)
        result = json.loads(t.run("report"))
        assert "error" in result

    @pytest.mark.unit
    def test_empty_command_error(self, tool):
        """Empty input returns error."""
        result = json.loads(tool.run(""))
        assert "error" in result

    @pytest.mark.unit
    def test_unknown_command_error(self, tool):
        """Unknown command returns error."""
        result = json.loads(tool.run("foobar"))
        assert "error" in result

    @pytest.mark.unit
    def test_analyze_requires_ip(self, tool):
        """analyze without ip returns error."""
        result = json.loads(tool.run("analyze"))
        assert "error" in result

    @pytest.mark.unit
    def test_stats_requires_ip(self, tool):
        """stats without ip returns error."""
        result = json.loads(tool.run("stats"))
        assert "error" in result

    @pytest.mark.integration
    def test_tool_analyze(self, tool, detector):
        """Tool analyze returns valid result."""
        _feed_normal_traffic(detector, ip="10.0.0.1", count=10)
        result = json.loads(tool.run("analyze ip=10.0.0.1"))
        assert "anomalies" in result
        assert "risk_score" in result
        assert "recommended_action" in result

    @pytest.mark.integration
    def test_tool_report(self, tool, detector):
        """Tool report returns valid result."""
        _feed_normal_traffic(detector, ip="10.0.0.1", count=5)
        result = json.loads(tool.run("report"))
        assert "total_sessions" in result

    @pytest.mark.integration
    def test_tool_stats(self, tool, detector):
        """Tool stats returns session data."""
        _feed_normal_traffic(detector, ip="10.0.0.5", count=5)
        result = json.loads(tool.run("stats ip=10.0.0.5"))
        assert result["ip"] == "10.0.0.5"
        assert result["total_requests"] == 5

    @pytest.mark.integration
    def test_tool_history(self, tool):
        """Tool history returns events list."""
        result = json.loads(tool.run("history"))
        assert "events" in result
        assert "count" in result

    @pytest.mark.integration
    def test_tool_baselines(self, tool):
        """Tool baselines returns baselines list."""
        result = json.loads(tool.run("baselines"))
        assert "baselines" in result

    @pytest.mark.unit
    def test_set_detector_late_bind(self):
        """Late-binding detector works."""
        t = APIAnomalyDetectorTool(detector=None)
        d = APIAnomalyDetector(sensitivity="medium", min_baseline_size=5)
        t.set_detector(d)
        _feed_normal_traffic(d, ip="10.0.0.1", count=5)
        result = json.loads(t.run("stats ip=10.0.0.1"))
        assert result["total_requests"] == 5

    @pytest.mark.unit
    def test_parse_kwargs(self):
        """Keyword argument parsing works."""
        kwargs = APIAnomalyDetectorTool._parse_kwargs(["ip=10.0.0.1", "limit=20"])
        assert kwargs["ip"] == "10.0.0.1"
        assert kwargs["limit"] == "20"


# ═══════════════════════════════════════════════════════════════════════════
# ANOMALY RULES TESTS
# ═══════════════════════════════════════════════════════════════════════════

class TestAnomalyRules:
    """Tests for anomaly_rules.py."""

    @pytest.mark.unit
    def test_default_rules_complete(self):
        """Default rule set has all expected rules."""
        rs = RuleSet()
        expected = {
            "request_rate_spike", "error_rate_spike", "model_switching",
            "latency_anomaly", "ua_instability", "payload_size_anomaly",
            "ip_instability",
        }
        assert set(rs.rules.keys()) == expected

    @pytest.mark.unit
    def test_enable_disable_rule(self):
        rs = RuleSet()
        rs.disable_rule("latency_anomaly")
        assert not rs.rules["latency_anomaly"].enabled
        rs.enable_rule("latency_anomaly")
        assert rs.rules["latency_anomaly"].enabled

    @pytest.mark.unit
    def test_apply_config(self):
        cfg = {
            "request_rate_spike": {"enabled": False, "threshold": 20, "action": "block"},
        }
        rs = apply_config_to_rules(cfg)
        rule = rs.get_rule("request_rate_spike")
        assert not rule.enabled
        assert rule.threshold == 20
        assert rule.default_action == RuleAction.BLOCK

    @pytest.mark.unit
    def test_get_critical_rules(self):
        rs = RuleSet()
        critical = rs.get_critical_rules()
        for name, rule in critical.items():
            assert rule.severity in (RuleSeverity.CRITICAL, RuleSeverity.HIGH)


# ═══════════════════════════════════════════════════════════════════════════
# ANOMALY REPORT TESTS
# ═══════════════════════════════════════════════════════════════════════════

class TestAnomalyReport:
    """Tests for anomaly report generation."""

    @pytest.mark.integration
    def test_report_includes_risk_score(self, detector):
        """Report entries include risk_score and recommended_action."""
        ip = "10.0.0.400"
        for i in range(15):
            detector.record_request(
                ip=ip, user_agent="ua", api_key="k",
                model=f"model_{i}",
                request_bytes=200, status_code=401,
                latency_ms=100.0,
            )
        report = detector.get_anomaly_report()
        if report["anomalous_count"] > 0:
            session = report["sessions"][0]
            assert "risk_score" in session
            assert "recommended_action" in session
            assert session["risk_score"] > 0

    @pytest.mark.integration
    def test_report_sensitivity_included(self, detector):
        """Report includes sensitivity and detection_mode."""
        report = detector.get_anomaly_report()
        assert report["sensitivity"] == "medium"
        assert report["detection_mode"] == "warn"
