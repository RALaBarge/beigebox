# BlueTruth — Bluetooth Diagnostic Tool

Comprehensive Bluetooth device discovery, security assessment, and threat detection for embedded in BeigeBox agents.

## Installation

### Option 1: Homebrew (macOS/Linux)
```bash
brew tap RALaBarge/homebrew-beigebox
brew install bluetruth
bluetruth --version
bluetruth --help
```

### Option 2: PyPI (Python environments)
```bash
pip install bluetruth
```

### Option 3: From source (development)
```bash
pip install -e .
```

## Quick Start

### Start the BlueTruth collector
```bash
# Linux (requires root for HCI access)
sudo bluetruth serve --port 8484

# macOS (uses native Bluetooth framework)
bluetruth serve --port 8484
```

### Query discovered devices
```bash
# List all devices
bluetruth list

# Search by address
bluetruth search AA:BB:CC:DD:EE:FF

# Export data
bluetruth export devices.json
```

### Use in BeigeBox agents
BlueTruth integrates as a tool in the BeigeBox operator. In your BeigeBox config:

```yaml
tools:
  registry:
    - name: bluetruth
      enabled: true
      api_url: http://localhost:8484  # or direct SQLite
      db_path: ~/.bluetruth/events.db
```

Then in your agent:
```python
# Agent calls bluetruth tool
{
  "tool": "bluetruth",
  "input": "simulate_device action=connect device=AA:BB:CC:DD:EE:FF"
}
```

## Features

| Feature | What it does |
|---|---|
| **Device Discovery** | Scans Bluetooth environment, lists all discoverable devices |
| **Security Assessment** | Analyzes encryption, pairing status, signal strength |
| **Threat Detection** | Identifies suspicious patterns (spoofing, jamming, replay) |
| **Event Log** | SQLite database of all Bluetooth events with timestamps |
| **REST API** | JSON API for integration with external tools |
| **Mock Simulation** | Simulate device lifecycle for testing agent responses |
| **Correlation** | Links related events across devices |

## Configuration

### Environment Variables

```bash
BLUETRUTH_PORT=8484                  # REST API port
BLUETRUTH_DB_PATH=~/.bluetruth/events.db  # Event database
BLUETRUTH_LOG_LEVEL=INFO             # Logging level
BLUETRUTH_HCI_DEVICE=hci0            # Linux HCI device
```

### Config File

Create `~/.bluetruth/config.yaml`:
```yaml
server:
  host: 0.0.0.0
  port: 8484
  
logging:
  level: INFO
  file: ~/.bluetruth/bluetruth.log

detection:
  patterns:
    - name: "spoofing"
      enabled: true
    - name: "jamming"
      enabled: true
    - name: "replay"
      enabled: true

storage:
  db_path: ~/.bluetruth/events.db
  retention_days: 30
```

## Commands

### CLI

```bash
bluetruth serve              # Start collector daemon
bluetruth list               # List discovered devices
bluetruth search <addr>      # Find device by address
bluetruth events <addr>      # Show events for device
bluetruth threat <addr>      # Threat assessment for device
bluetruth export <file>      # Export to JSON
bluetruth stats              # Show statistics
bluetruth --version          # Show version
```

### Docker

```bash
# Start container (Linux)
docker run -d --privileged \
  -p 8484:8484 \
  -v ~/.bluetruth:/root/.bluetruth \
  ralabarge/bluetruth:0.2.0

# macOS (native Bluetooth via socket)
docker run -d \
  -p 8484:8484 \
  -v /var/run/usbmuxd:/var/run/usbmuxd \
  ralabarge/bluetruth:0.2.0
```

## API Reference

### REST Endpoints

**List devices**
```bash
curl http://localhost:8484/api/devices
```

**Get device details**
```bash
curl http://localhost:8484/api/devices/AA:BB:CC:DD:EE:FF
```

**Query events**
```bash
curl http://localhost:8484/api/events?device=AA:BB:CC:DD:EE:FF&type=CONNECT
```

**Threat assessment**
```bash
curl http://localhost:8484/api/threats?device=AA:BB:CC:DD:EE:FF
```

## Troubleshooting

### No devices discovered (Linux)

1. Check Bluetooth is enabled:
   ```bash
   sudo bluetoothctl show
   ```

2. Check HCI device:
   ```bash
   hcitool dev
   ```

3. Run with elevated privileges:
   ```bash
   sudo bluetruth serve
   ```

### Permission denied errors

**Linux:** BlueTruth requires `CAP_NET_ADMIN` or root:
```bash
# Option 1: Run with sudo
sudo bluetruth serve

# Option 2: Grant capability (one-time)
sudo setcap cap_net_admin=eip /usr/bin/bluetruth
bluetruth serve
```

**macOS:** Native Bluetooth access, no special permissions needed.

### API connection refused

Check that the collector is running:
```bash
curl http://localhost:8484/api/health
# Should return { "status": "ok" }
```

If not running, start it:
```bash
bluetruth serve --port 8484
```

## Performance

| Metric | Value |
|---|---|
| Device discovery time | <5s (first scan) |
| Event ingestion | 1000+ events/sec |
| Query latency | <50ms |
| Database size | ~1MB per 10k events |
| Memory usage | ~50MB base + ~10MB per 100 devices |

## Security

BlueTruth runs with minimal privileges on macOS (native API) and requires `CAP_NET_ADMIN` on Linux. All Bluetooth traffic is passively observed — no device state is modified without explicit command.

For production deployments:
- Run in isolated container with network segmentation
- Enable logging and rotate logs daily
- Monitor database growth
- Use API authentication if exposed externally

## Testing

Run the test suite:
```bash
pytest tests/test_bluetruth.py -v
pytest tests/test_bluetruth_scenarios.py -v  # BeigeBox integration tests
```

## License

MIT — See [LICENSE](https://github.com/RALaBarge/beigebox/blob/main/LICENSE.md)

## Support

- **Issues:** https://github.com/RALaBarge/beigebox/issues
- **BeigeBox Tap:** https://github.com/RALaBarge/homebrew-beigebox
