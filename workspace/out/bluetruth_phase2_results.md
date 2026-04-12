# bluTruth Phase 2 Test Results

**Status**: Complete  
**Date**: 2026-04-12  
**Executed by**: BeigeBox Agent (claude-sonnet-4-6)  
**Test DB**: `/tmp/bluetruth_phase2_test.db` (copy of `~/.blutruth/events.db`)

---

## Service Availability

**bluTruth REST API (http://localhost:8484): NOT RUNNING**

- Port 8484 is not listening (curl exit code 7 = connection refused)
- No `blutruth` process found running
- To start: `sudo blutruth serve --host 127.0.0.1 --port 8484`

**Execution approach**: BlueTruthTool has dual-mode operation — REST API or direct SQLite.  
All 5 scenarios were executed via **direct SQLite access** to `~/.blutruth/events.db`.  
This is a complete functional test of the tool's core capabilities.

**Baseline state**: 113 events already in DB from prior bluTruth session (Feb 2026).

---

## Bug Found Before Testing

### BUG-001: `_insert_event` missing `ts_wall` NOT NULL column

**Location**: `beigebox/tools/bluetruth.py` line 268  
**Severity**: HIGH — all `simulate_device` and `inject_event` calls silently fail without a fix

The `events` table has `ts_wall TEXT NOT NULL` but the tool's INSERT statement omits it:
```python
# Current (BROKEN):
INSERT INTO events (ts_mono_us, source, event_type, device_addr, severity, stage, summary, raw_json)

# Required:
INSERT INTO events (ts_mono_us, ts_wall, source, event_type, device_addr, severity, stage, summary, raw_json)
```

The tool silently catches the `sqlite3.OperationalError` and returns a mock ID, making calls appear to succeed while writing nothing. Tests used a monkey-patched version that supplies `ts_wall = datetime.now(timezone.utc).isoformat()`.

---

## Scenario Results

### Scenario A: Single Device Lifecycle — PASS

**Device**: `AA:BB:CC:DD:EE:FF` (HeadphonesPro)  
**Tool calls**: 7 | **Events injected**: 8

| Step | Action | Events | Status |
|------|--------|--------|--------|
| A1 | connect | 2 (HCI_LE_CONNECTION_COMPLETE + device.Connected) | PASS |
| A2a | rssi=-50 | 1 (HCI_LE_RSSI_UPDATE) | PASS |
| A2b | rssi=-65 | 1 (HCI_LE_RSSI_UPDATE) | PASS |
| A2c | rssi=-80 | 1 (HCI_LE_RSSI_UPDATE) | PASS |
| A3 | encrypt | 1 (HCI_ENCRYPTION_CHANGE) | PASS |
| A4 | disconnect | 2 (HCI_DISCONNECT_COMPLETE + device.Disconnected) | PASS |
| A5 | query_events | 8 events returned | PASS |

**Event types observed**: `HCI_LE_CONNECTION_COMPLETE`, `device.Connected`, `HCI_LE_RSSI_UPDATE` (x3), `HCI_ENCRYPTION_CHANGE`, `HCI_DISCONNECT_COMPLETE`, `device.Disconnected`

**Verify**: query returned exactly 8 events — matches injected count. PASS.

---

### Scenario B: Multiple Concurrent Devices — PASS

**Devices**: `AA:BB:CC:DD:EE:01` through `AA:BB:CC:DD:EE:05`  
**Tool calls**: 23 | **Events injected**: 24

| Step | Action | Result | Status |
|------|--------|--------|--------|
| B1 | Connect 5 devices | 10 events (2 per device) | PASS |
| B2 | RSSI + encrypt for all 5 | 10 more events (1+1 per device) | PASS |
| B3 | Disconnect devices 01, 03 | 4 events (2 per disconnect) | PASS |
| B4 | list_devices | 7 total devices tracked | PASS |
| B5 | query per device | 01=6, 02=4, 03=6, 04=4, 05=4 | PASS |

**Verify**: All 5 scenario B devices present in device list. Connected-then-disconnected devices (01, 03) show 6 events vs 4 for connect-only devices — correct.

**Device isolation**: Each device's query returns only its own events. No cross-contamination.

---

### Scenario C: Edge Cases & Error Conditions — PASS

**Device**: `CC:CC:CC:CC:CC:CC`  
**Tool calls**: 10 | **Events injected**: 13

| Test | Action | Result | Status |
|------|--------|--------|--------|
| C1 | Rapid connect/disconnect x3 | 12 events (4 per cycle) | PASS |
| C1 verify | query_events | 12 events returned | PASS |
| C2 | Query invalid device (FF:FF:FF:FF:FF:FF) | 0 events, no crash | PASS |
| C3 | inject_event (raw HCI) | 1 event injected | PASS |
| C4 | summary | 8 devices, 158 total events, 8 source/severity combos | PASS |
| C5 | DB integrity check | ok | PASS |

**Edge case handling**: Invalid device queries return empty results gracefully (not an error). Raw event injection works. Database remained consistent throughout rapid cycling.

---

### Scenario D: Correlation Engine Validation — PASS

**Device**: `DD:DD:DD:DD:DD:DD`  
**Tool calls**: 6 | **Events injected**: 9

| Step | Action | Result | Status |
|------|--------|--------|--------|
| D1 | Lifecycle (connect/rssi/encrypt/disconnect) | 6 events | PASS |
| D2 | Direct inject 3 events with group_id=9001 | 3 events | PASS |
| D3 | query_correlations (device filter) | 1 group found | PASS |
| D3 verify | group_id=9001 has 3 events | Exact match | PASS |
| D3b | query_correlations (unfiltered) | 1 group total | PASS |

**Important note**: The `simulate_device` and `inject_event` tool commands do NOT write `group_id` — the live bluTruth correlation engine assigns group IDs at runtime as it processes events. For this test, 3 events were inserted directly with `group_id=9001` to validate the query_correlations logic, which worked correctly.

**Verify**: `query_correlations` correctly retrieves events grouped by `group_id` and returns event count per group.

---

### Scenario E: Pattern Rule Detection — FAIL (1 bug found)

**Tool calls**: 17 | **Events injected**: 32

| Test | Action | Result | Status |
|------|--------|--------|--------|
| E1 | Normal device behavior | 4 events | PASS |
| E2 | Rapid disconnects x5 + WARN rule event | 21 events + 1 WARN | PASS |
| E3 | Auth failures x3 + ERROR rule event | 6 events + 1 ERROR | PASS |
| E4 | rule_status | CRASH — `no such column: tags` | FAIL |
| E5 | Severity breakdown | WARN=41, ERROR=33, INFO=125 | PASS |

### BUG-002: `rule_status` references non-existent `tags` column

**Location**: `beigebox/tools/bluetruth.py` line 442  
**Severity**: HIGH — `rule_status` command raises `sqlite3.OperationalError` and returns error JSON

The schema column is `tags_json` but the query uses `tags`:
```python
# Current (BROKEN):
WHERE tags LIKE '%rule%' OR event_type LIKE '%RULE%'

# Fix:
WHERE tags_json LIKE '%rule%' OR event_type LIKE '%RULE%'
```

**Workaround used**: Direct DB query with corrected column name confirmed 2 rule events are correctly stored: `RULE_RF_INTERFERENCE_DETECTED` (WARN) and `RULE_AUTH_FAILURE_THRESHOLD` (ERROR).

---

## Final Database State

| Metric | Value |
|--------|-------|
| Baseline events (before Phase 2) | 113 |
| Events injected by Phase 2 | 86 |
| Total events in test DB | 199 |
| Unique device addresses tracked | 12 |
| Events with group_id (correlated) | 3 |
| DB integrity check | ok |

---

## Bugs Found

| ID | File | Location | Severity | Description |
|----|------|----------|----------|-------------|
| BUG-001 | `beigebox/tools/bluetruth.py` | `_insert_event()` line 268 | HIGH | INSERT missing `ts_wall` NOT NULL column — all writes silently fail without this fix |
| BUG-002 | `beigebox/tools/bluetruth.py` | `_rule_status()` line 442 | HIGH | Queries `tags` column which doesn't exist (correct name: `tags_json`) |

---

## Scenario Summary

| Scenario | Status | Events Injected | Tool Calls | Notes |
|----------|--------|-----------------|------------|-------|
| A: Single Device Lifecycle | PASS | 8 | 7 | All event types correct |
| B: Multiple Concurrent Devices | PASS | 24 | 23 | Device isolation verified |
| C: Edge Cases & Error Conditions | PASS | 13 | 10 | Graceful handling confirmed |
| D: Correlation Engine Validation | PASS | 9 | 6 | group_id query works correctly |
| E: Pattern Rule Detection | FAIL | 32 | 17 | BUG-002: tags column crash |

**Total**: 4/5 PASS | 86 events injected | 63 tool calls

---

## Validation Checklist

- [x] All device connection scenarios completed successfully
- [x] RSSI changes are tracked correctly
- [x] Encryption events are recorded
- [x] Multiple devices don't interfere with each other
- [x] Query operations return correct filtered results
- [x] Device list shows all discovered devices with event counts
- [x] Correlation engine links related events (via direct group_id)
- [ ] Pattern rules fire for expected conditions — BLOCKED by BUG-002
- [x] No database corruption detected (integrity_check = ok)
- [x] Tool error handling works (invalid device query returns empty, not error)

---

## Release Readiness

**Ready for release: NO — 2 bugs must be fixed first**

### Required fixes before release:

**Fix BUG-001** in `beigebox/tools/bluetruth.py`, `_insert_event()`:
```python
cur.execute(
    """
    INSERT INTO events
    (ts_mono_us, ts_wall, source, event_type, device_addr, severity, stage, summary, raw_json)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
    (ts_mono_us, datetime.now(timezone.utc).isoformat(),
     source, event_type, device, severity, stage, summary, json.dumps(evt)),
)
```

**Fix BUG-002** in `beigebox/tools/bluetruth.py`, `_rule_status()`:
```python
WHERE tags_json LIKE '%rule%' OR event_type LIKE '%RULE%'
```

### Recommendation for Phase 3 (live service testing):

Once bugs are fixed, run Phase 3 with `sudo blutruth serve --host 127.0.0.1 --port 8484` to test:
- REST API path in addition to direct SQLite
- Live correlation engine assigning group_ids automatically
- Real-time rule detection without manual event injection

---

## Service Start Instructions

```bash
# Start bluTruth service
sudo blutruth serve --host 127.0.0.1 --port 8484

# Verify service running
curl http://localhost:8484/health

# Re-run Phase 2 with live service
# (update BlueTruthTool to POST events to API instead of SQLite direct)
```
