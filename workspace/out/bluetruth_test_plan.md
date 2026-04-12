# bluTruth Integration Test Plan

**Status**: Ready to Execute  
**Created**: 2026-04-12  
**Goal**: Comprehensive testing of bluTruth Bluetooth diagnostic platform using BeigeBox agents

---

## Phase 1: Tool Verification (Standalone)

Run the test suite to verify BlueTruthTool is working:

```bash
pytest tests/test_bluetruth_scenarios.py -v
```

**Expected**: All 17 tests pass
- ✅ 5 basic operations (connect, disconnect, rssi, encrypt, auth)
- ✅ 3 query operations (by device, by source, limit)
- ✅ 1 device tracking test
- ✅ 3 edge case tests
- ✅ 3 full scenario tests
- ✅ 2 database integrity tests

---

## Phase 2: Agent-Driven Testing

Use operator to exercise bluTruth with multiple scenarios. Copy each scenario to `input.md` in workspace/in/, then run operator.

### Scenario A: Single Device Lifecycle

```
Using the bluetruth tool, test a single Bluetooth headphone device through its complete lifecycle:
1. Simulate device connection (device=AA:BB:CC:DD:EE:FF, name=HeadphonesPro)
2. Simulate RSSI signal strength changes: -50dBm, -65dBm, -80dBm
3. Simulate encryption setup
4. Simulate disconnection
5. Query all events for this device
6. Report total event count and event types observed
```

**Tool calls needed**: `simulate_device` (5 calls), `query_events` (1 call)

### Scenario B: Multiple Concurrent Devices

```
Test bluTruth's handling of multiple devices simultaneously:
1. Connect 5 different devices (addresses: AA:BB:CC:DD:EE:01 through AA:BB:CC:DD:EE:05)
2. For each device, simulate RSSI changes and encryption
3. Disconnect devices 1 and 3
4. List all discovered devices
5. Query events for each device independently
6. Verify total event counts across all devices
```

**Tool calls needed**: `simulate_device` (multiple), `list_devices`, `query_events`

### Scenario C: Edge Cases & Error Conditions

```
Test bluTruth's resilience to edge cases:
1. Rapidly connect/disconnect the same device 3 times
2. Query with invalid device address (verify graceful handling)
3. Inject raw HCI events directly using inject_event command
4. Check summary statistics for all injected events
5. Verify no data corruption occurred
```

**Tool calls needed**: `simulate_device`, `inject_event`, `query_events`, `summary`

### Scenario D: Correlation Engine Validation

```
Test event correlation across different sources:
1. Simulate device lifecycle with mixed HCI and DBUS events
2. Query correlations to verify events are grouped by time window
3. Check that related events have the same group_id
4. Verify correlation count matches actual related events
```

**Tool calls needed**: `simulate_device`, `query_correlations`, `summary`

### Scenario E: Pattern Rule Detection

```
Test bluTruth's rule engine for anomaly detection:
1. Create a scenario with normal device behavior
2. Create a scenario with rapid disconnects (potential RF interference)
3. Create a scenario with authentication failures
4. Check rule_status to see which patterns were detected
5. Verify severity levels (INFO vs WARN vs ERROR)
```

**Tool calls needed**: `simulate_device`, `rule_status`, `summary`

---

## Phase 3: Performance & Stress Testing

### Stress Test: 100+ Events

```
Test bluTruth's performance with large event volumes:
1. Simulate 10 devices with 5 lifecycle events each (50 events)
2. Inject 50 raw HCI events
3. Query all events with various filters
4. Measure response times
5. Verify database consistency
```

**Expected**: Queries return results in <1s, no data loss

---

## Phase 4: Validation Checklist

After running all scenarios, verify:

- [ ] All device connection scenarios completed successfully
- [ ] RSSI changes are tracked correctly
- [ ] Encryption events are recorded
- [ ] Multiple devices don't interfere with each other
- [ ] Query operations return correct filtered results
- [ ] Device list shows all discovered devices with event counts
- [ ] Correlation engine links related events
- [ ] Pattern rules fire for expected conditions
- [ ] No database corruption detected
- [ ] Tool error handling works (invalid inputs gracefully rejected)

---

## Phase 5: Test Report Generation

After all scenarios complete, generate summary:

```
BLUETRUTH TEST SUMMARY
======================
- Total scenarios executed: 5
- Total devices tested: 15+
- Total events injected: 500+
- Query operations: 50+
- Pattern rules checked: 24
- Performance: All queries <1s
- Database integrity: ✅ Verified
- Error handling: ✅ Robust

Ready for release: YES / NO
```

---

## How to Run Each Scenario

1. **Setup**: Ensure bluTruth is running
   ```bash
   # In a separate terminal:
   sudo blutruth serve --host 127.0.0.1 --port 8484
   ```

2. **Configure BeigeBox**: Enable bluetruth tool
   ```yaml
   # config.yaml
   tools:
     bluetruth:
       enabled: true
       api_url: "http://localhost:8484"
   ```

3. **Run Operator**: For each scenario, run:
   ```bash
   # Put scenario text in workspace/in/
   echo "[scenario text]" > workspace/in/input.md
   
   # Then invoke operator via API or CLI
   curl -X POST http://localhost:8001/v1/agent \
     -H "Content-Type: application/json" \
     -d '{"mode": "operator", "max_iterations": 10}'
   ```

4. **Monitor**: Watch operator tool calls
   ```bash
   tail -f logs/beigebox.log | grep bluetruth
   ```

5. **Validate**: Check operator output for completeness and correctness

---

## Integration with CI/CD

Once validated manually, add to test suite:

```bash
# Run bluTruth tests in CI
pytest tests/test_bluetruth_scenarios.py -v --junitxml=junit.xml

# Run agent-driven tests (requires bluTruth service)
pytest tests/test_bluetruth_agent_scenarios.py -v --junitxml=junit-agent.xml
```

---

## Next Steps

1. ✅ BlueTruthTool implemented and registered in BeigeBox
2. ✅ 17 unit tests passing
3. 🔄 Run Phase 1-2 scenarios with operator
4. 📋 Generate test report
5. 📦 Prepare for PyPI/Brew release
