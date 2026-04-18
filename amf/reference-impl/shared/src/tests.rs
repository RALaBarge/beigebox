#[cfg(test)]
mod integration_tests {
    use crate::{Agent, Archive, Message, RingAgentState};
    use std::time::{SystemTime, UNIX_EPOCH};

    fn now_ms() -> u64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis() as u64
    }

    #[test]
    fn test_ring_agent_processes_message_and_logs() {
        let archive = Archive::new_inmemory();
        let mut ring_a = RingAgentState::new(Agent::RingA, archive);
        ring_a.session_key_present = true;

        let msg = Message::new(
            now_ms(),
            Agent::DMZ,
            1,
            "send".to_string(),
            "test payload".to_string(),
            vec![0; 64],
        );

        ring_a.enqueue_message(msg);
        assert_eq!(ring_a.queue_depth(), 1);

        let processed = ring_a.tick().expect("Tick should succeed");
        assert_eq!(processed, 1);
        assert_eq!(ring_a.queue_depth(), 0);
        assert_eq!(ring_a.last_dmz_nonce_seen, 1);
        assert_eq!(ring_a.tick_number, 1);
    }

    #[test]
    fn test_replay_protection_rejects_old_nonce() {
        let archive = Archive::new_inmemory();
        let mut ring_a = RingAgentState::new(Agent::RingA, archive);
        ring_a.session_key_present = true;

        let msg1 = Message::new(
            now_ms(),
            Agent::DMZ,
            1,
            "send".to_string(),
            "payload1".to_string(),
            vec![0; 64],
        );
        ring_a.enqueue_message(msg1);
        ring_a.tick().expect("First tick");
        assert_eq!(ring_a.last_dmz_nonce_seen, 1);

        let msg2 = Message::new(
            now_ms(),
            Agent::DMZ,
            1,
            "send".to_string(),
            "payload2".to_string(),
            vec![0; 64],
        );
        ring_a.enqueue_message(msg2);
        ring_a.tick().expect("Second tick");

        assert_eq!(ring_a.policy_violations_count, 1);
        assert_eq!(ring_a.last_dmz_nonce_seen, 1);
    }

    #[test]
    fn test_nonce_monotonicity() {
        let archive = Archive::new_inmemory();
        let mut ring_a = RingAgentState::new(Agent::RingA, archive);
        ring_a.session_key_present = true;

        for nonce in 1..=5 {
            let msg = Message::new(
                now_ms(),
                Agent::DMZ,
                nonce,
                "send".to_string(),
                format!("payload{}", nonce),
                vec![0; 64],
            );
            ring_a.enqueue_message(msg);
            ring_a.tick().expect("Tick should succeed");
        }

        assert_eq!(ring_a.last_dmz_nonce_seen, 5);
        assert_eq!(ring_a.policy_violations_count, 0);
    }

    #[test]
    fn test_policy_default_deny() {
        let archive = Archive::new_inmemory();
        let mut ring_b = RingAgentState::new(Agent::RingB, archive);
        ring_b.session_key_present = true;

        let msg = Message::new(
            now_ms(),
            Agent::RingA,
            1,
            "send".to_string(),
            "payload".to_string(),
            vec![0; 64],
        );
        ring_b.enqueue_message(msg);
        ring_b.tick().expect("Tick should succeed");

        assert_eq!(ring_b.policy_violations_count, 1);
    }

    #[test]
    fn test_heartbeat_reflects_state() {
        let archive = Archive::new_inmemory();
        let mut ring_c = RingAgentState::new(Agent::RingC, archive);
        ring_c.session_key_present = true;

        for nonce in 1..=3 {
            let msg = Message::new(
                now_ms(),
                Agent::DMZ,
                nonce,
                "send".to_string(),
                "payload".to_string(),
                vec![0; 64],
            );
            ring_c.enqueue_message(msg);
            ring_c.tick().expect("Tick should succeed");
        }

        let hb = ring_c.heartbeat().expect("Heartbeat should succeed");
        assert_eq!(hb.tick_number, 3);
        assert_eq!(hb.agent, Agent::RingC);
        assert_eq!(hb.last_dmz_nonce_seen, 3);
        assert_eq!(hb.session_key_present, true);
    }
}
