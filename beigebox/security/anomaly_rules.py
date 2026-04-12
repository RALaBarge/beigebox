"""
Anomaly Detection Rules and Thresholds.

Configurable rule set for API anomaly detection. Each rule maps to a specific
attack pattern or behavioral anomaly.

Rules:
  1. request_rate_spike — >5 requests/min from single IP
  2. error_rate_spike — >30% error rate (401/403/5xx) in 5-min window
  3. model_switching — >8 distinct models in 5 minutes
  4. latency_anomaly — sub-baseline latency (timing attack)
  5. ua_instability — 4+ different User-Agent strings from same IP
  6. payload_size_anomaly — <50 chars or >100KB request body

Each rule has:
  - name: Rule identifier
  - severity: critical, high, medium, low
  - description: Human-readable explanation
  - default_action: warn, rate_limit, block
  - enabled: Whether to enforce this rule
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class RuleSeverity(str, Enum):
    """Severity levels for anomaly rules."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RuleAction(str, Enum):
    """Actions to take when rule is triggered."""
    WARN = "warn"
    RATE_LIMIT = "rate_limit"
    BLOCK = "block"


@dataclass
class AnomalyRule:
    """Definition of a single anomaly detection rule."""
    name: str
    description: str
    severity: RuleSeverity
    enabled: bool = True
    default_action: RuleAction = RuleAction.WARN
    threshold: Optional[float] = None  # Rule-specific threshold value


class RuleSet:
    """Collection of anomaly detection rules."""

    def __init__(self):
        """Initialize default rule set."""
        self.rules = {
            "request_rate_spike": AnomalyRule(
                name="request_rate_spike",
                description="High request rate from single IP (>5 requests/minute)",
                severity=RuleSeverity.HIGH,
                enabled=True,
                default_action=RuleAction.RATE_LIMIT,
                threshold=5,  # requests per minute
            ),
            "error_rate_spike": AnomalyRule(
                name="error_rate_spike",
                description="Elevated error rate (>30% 4xx/5xx in 5-min window)",
                severity=RuleSeverity.HIGH,
                enabled=True,
                default_action=RuleAction.WARN,
                threshold=0.30,  # 30%
            ),
            "model_switching": AnomalyRule(
                name="model_switching",
                description="Rapid model switching pattern (>8 distinct models in 5 min)",
                severity=RuleSeverity.MEDIUM,
                enabled=True,
                default_action=RuleAction.WARN,
                threshold=8,  # distinct models
            ),
            "latency_anomaly": AnomalyRule(
                name="latency_anomaly",
                description="Abnormal latency pattern (timing attack indicator)",
                severity=RuleSeverity.MEDIUM,
                enabled=True,
                default_action=RuleAction.WARN,
                threshold=3.0,  # z-score
            ),
            "ua_instability": AnomalyRule(
                name="ua_instability",
                description="Multiple User-Agent strings from same IP (4+)",
                severity=RuleSeverity.LOW,
                enabled=True,
                default_action=RuleAction.WARN,
                threshold=4,  # distinct UAs
            ),
            "payload_size_anomaly": AnomalyRule(
                name="payload_size_anomaly",
                description="Request payload size outside normal range (<50 chars or >100KB)",
                severity=RuleSeverity.LOW,
                enabled=True,
                default_action=RuleAction.WARN,
                threshold=None,
            ),
            "ip_instability": AnomalyRule(
                name="ip_instability",
                description="Multiple IPs in same conversation session (5+)",
                severity=RuleSeverity.MEDIUM,
                enabled=True,
                default_action=RuleAction.WARN,
                threshold=5,  # distinct IPs per conversation
            ),
        }

    def get_rule(self, name: str) -> Optional[AnomalyRule]:
        """Get a rule by name."""
        return self.rules.get(name)

    def enable_rule(self, name: str) -> bool:
        """Enable a rule."""
        if name in self.rules:
            self.rules[name].enabled = True
            return True
        return False

    def disable_rule(self, name: str) -> bool:
        """Disable a rule."""
        if name in self.rules:
            self.rules[name].enabled = False
            return True
        return False

    def set_action(self, name: str, action: RuleAction) -> bool:
        """Set the default action for a rule."""
        if name in self.rules:
            self.rules[name].default_action = action
            return True
        return False

    def get_all_rules(self) -> dict[str, AnomalyRule]:
        """Get all rules."""
        return dict(self.rules)

    def get_enabled_rules(self) -> dict[str, AnomalyRule]:
        """Get only enabled rules."""
        return {k: v for k, v in self.rules.items() if v.enabled}

    def get_critical_rules(self) -> dict[str, AnomalyRule]:
        """Get high-severity rules (critical + high)."""
        return {
            k: v for k, v in self.rules.items()
            if v.enabled and v.severity in (RuleSeverity.CRITICAL, RuleSeverity.HIGH)
        }

    def to_config_dict(self) -> dict:
        """Convert rule set to configuration dictionary."""
        return {
            name: {
                "enabled": rule.enabled,
                "severity": rule.severity.value,
                "action": rule.default_action.value,
                "threshold": rule.threshold,
            }
            for name, rule in self.rules.items()
        }


# Default global rule set
_DEFAULT_RULES = RuleSet()


def get_default_rules() -> RuleSet:
    """Get the default rule set."""
    return _DEFAULT_RULES


def apply_config_to_rules(cfg: dict) -> RuleSet:
    """
    Apply configuration dictionary to rule set.

    Config format:
        security:
          api_anomaly:
            rules:
              request_rate_spike:
                enabled: true
                action: rate_limit
                threshold: 5
    """
    ruleset = RuleSet()

    if not cfg:
        return ruleset

    for rule_name, rule_cfg in cfg.items():
        rule = ruleset.get_rule(rule_name)
        if not rule:
            continue

        if "enabled" in rule_cfg:
            rule.enabled = rule_cfg["enabled"]

        if "action" in rule_cfg:
            try:
                rule.default_action = RuleAction(rule_cfg["action"])
            except ValueError:
                pass  # Ignore invalid action values

        if "threshold" in rule_cfg and rule_cfg["threshold"] is not None:
            rule.threshold = rule_cfg["threshold"]

    return ruleset
