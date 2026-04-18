use crate::data::{Agent, AllowRule, Decision};
use std::collections::HashSet;

/// Static policy store (immutable after initialization)
#[derive(Debug, Clone)]
pub struct PolicyStore {
    rules: HashSet<AllowRule>,
}

impl PolicyStore {
    /// Create a new policy store with hardcoded rules
    pub fn new() -> Self {
        let mut rules = HashSet::new();

        // DMZ can send to all ring agents
        rules.insert(AllowRule::new(Agent::DMZ, "send".to_string(), Agent::RingA));
        rules.insert(AllowRule::new(Agent::DMZ, "send".to_string(), Agent::RingB));
        rules.insert(AllowRule::new(Agent::DMZ, "send".to_string(), Agent::RingC));

        // Ring agents can receive from DMZ
        rules.insert(AllowRule::new(Agent::RingA, "receive".to_string(), Agent::DMZ));
        rules.insert(AllowRule::new(Agent::RingB, "receive".to_string(), Agent::DMZ));
        rules.insert(AllowRule::new(Agent::RingC, "receive".to_string(), Agent::DMZ));

        // Ring agents can communicate with each other (ticking)
        for agent in [Agent::RingA, Agent::RingB, Agent::RingC].iter() {
            for other in [Agent::RingA, Agent::RingB, Agent::RingC].iter() {
                if agent != other {
                    rules.insert(AllowRule::new(*agent, "tick".to_string(), *other));
                }
            }
        }

        PolicyStore { rules }
    }

    /// Check if a rule exists (default-deny)
    pub fn rule_exists(&self, subject: Agent, action: &str, object: Agent) -> bool {
        self.rules.contains(&AllowRule::new(subject, action.to_string(), object))
    }

    /// Evaluate policy: return ALLOWED or DENIED
    pub fn evaluate(&self, subject: Agent, action: &str, object: Agent) -> Decision {
        if self.rule_exists(subject, action, object) {
            Decision::ALLOWED
        } else {
            Decision::DENIED
        }
    }
}

impl Default for PolicyStore {
    fn default() -> Self {
        Self::new()
    }
}

/// Check if a rule exists in a policy store
pub fn RuleExists(rules: &HashSet<AllowRule>, subject: Agent, action: &str, object: Agent) -> bool {
    rules.contains(&AllowRule::new(subject, action.to_string(), object))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_policy_dmz_send() {
        let policy = PolicyStore::new();
        assert_eq!(
            policy.evaluate(Agent::DMZ, "send", Agent::RingA),
            Decision::ALLOWED
        );
    }

    #[test]
    fn test_policy_default_deny() {
        let policy = PolicyStore::new();
        assert_eq!(
            policy.evaluate(Agent::RingA, "send", Agent::RingB),
            Decision::DENIED
        );
    }

    #[test]
    fn test_policy_no_delegate() {
        let policy = PolicyStore::new();
        assert_eq!(
            policy.evaluate(Agent::DMZ, "delegate", Agent::RingA),
            Decision::DENIED
        );
    }
}
