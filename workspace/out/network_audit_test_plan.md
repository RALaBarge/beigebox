# NetworkAuditTool — Agent Test Plan

## Overview

The `network_audit` tool enables BeigeBox Operator agents to perform local network discovery, port scanning, service fingerprinting, and CVE lookup without requiring root privileges or external binaries.

---

## Tool Registration

Config entry in `config.yaml`:
```yaml
tools:
  enabled: true
  network_audit:
    enabled: true
    timeout: 1.0
    concurrency: 200
    max_hosts: 256
```

---

## Agent Command Reference

All commands use the format: `{"tool": "network_audit", "input": "<command> [args]"}`

### 1. Check Status (always safe, no scanning)

```json
{"tool": "network_audit", "input": "get_status"}
```

Returns: privilege level, local interfaces, detected subnet, vuln DB version.

---

### 2. Discover and Scan the Local Network

```json
{"tool": "network_audit", "input": "scan_network"}
```

Auto-detects local subnet and scans top-1000 ports on every discovered host.

With explicit subnet:
```json
{"tool": "network_audit", "input": "scan_network subnet=192.168.1.0/24 ports=top-1000"}
```

Fast scan (common ports only):
```json
{"tool": "network_audit", "input": "scan_network ports=common timeout=0.5"}
```

---

### 3. Scan a Single Device

```json
{"tool": "network_audit", "input": "scan_device ip=192.168.1.1"}
```

With custom port range:
```json
{"tool": "network_audit", "input": "scan_device ip=192.168.1.50 ports=22,80,443,8080"}
```

---

### 4. Fingerprint a Specific Service

```json
{"tool": "network_audit", "input": "fingerprint_service ip=192.168.1.1 port=80"}
```

Returns: banner, service name, version, HTTP title, TLS cert info, CVEs, findings.

---

### 5. Check CVEs for a Known Service

```json
{"tool": "network_audit", "input": "check_vulnerabilities service=OpenSSH version=7.4"}
```

Returns: CVE list with severity, CVSS score, and remediation advice.

---

## Example Agent Workflows

### Workflow A: "Audit my network and tell me what's exposed"

```
Step 1: get_status
  → Confirm local interfaces detected, note subnet (e.g. 192.168.1.0/24)

Step 2: scan_network subnet=192.168.1.0/24 ports=top-1000
  → Returns all live hosts, open ports, services, CVEs, risk levels

Step 3: check_vulnerabilities service=<service> version=<version>  [for each flagged service]
  → Get detailed CVE + remediation for high-severity items

Step 4: Summarise findings in plain English
  → "Found 8 devices. Router (192.168.1.1) has 2 CRITICAL issues: default credentials
     and outdated firmware. Pi (192.168.1.50) runs OpenSSH 7.4 with known CVEs."
```

### Workflow B: "What ports is my NAS exposing?"

```
Step 1: scan_device ip=192.168.1.100
  → Lists open ports with service names

Step 2: fingerprint_service ip=192.168.1.100 port=5000
  → Gets banner, admin panel detection, TLS status

Step 3: Agent summarises findings
```

### Workflow C: "Is my router running vulnerable firmware?"

```
Step 1: scan_device ip=192.168.1.1 ports=80,443,8080,8443
  → Finds open HTTP/HTTPS ports

Step 2: fingerprint_service ip=192.168.1.1 port=80
  → Returns Server header (e.g. lighttpd/1.4.45), HTTP title, admin panel detection

Step 3: check_vulnerabilities service=lighttpd version=1.4.45
  → Returns CVE-2022-41556 (HIGH) if version matches

Step 4: Agent flags PLAIN_HTTP_ADMIN if admin panel accessible over HTTP
```

---

## Expected Output Schema

### scan_network / scan_device

```json
{
  "scan_meta": {
    "version": "1.0.0",
    "timestamp": "2026-04-12T10:00:00Z",
    "duration_seconds": 12.5,
    "local_ip": "192.168.1.10",
    "gateway": "192.168.1.1",
    "subnet": "192.168.1.0/24",
    "hosts_discovered": 4,
    "ports_scanned": "top-1000",
    "privilege_level": "non-root",
    "scan_method": "tcp_connect"
  },
  "hosts": [
    {
      "ip": "192.168.1.1",
      "mac": "AA:BB:CC:DD:EE:FF",
      "oui_vendor": "TP-Link",
      "hostnames": ["router.local"],
      "open_ports": [
        {
          "port": 80,
          "protocol": "tcp",
          "service": "lighttpd",
          "version": "1.4.45",
          "banner": "lighttpd/1.4.45",
          "http_title": "TP-Link Router",
          "http_server": "lighttpd/1.4.45",
          "tls": null,
          "cves": [
            {
              "id": "CVE-2022-41556",
              "severity": "HIGH",
              "cvss": 7.5,
              "description": "Resource leak via HTTP/1.1 request smuggling",
              "confidence": "CONFIRMED"
            }
          ],
          "findings": [
            {
              "id": "PLAIN_HTTP_ADMIN",
              "severity": "HIGH",
              "summary": "Admin interface accessible over unencrypted HTTP",
              "detail": "Port 80 exposes an admin panel without TLS."
            }
          ]
        }
      ],
      "findings": [],
      "risk_level": "HIGH"
    }
  ],
  "summary": {
    "critical": 0,
    "high": 2,
    "medium": 1,
    "low": 0,
    "info": 0,
    "total_devices": 4,
    "exposed_count": 1,
    "critical_vulns": 0,
    "top_findings": [
      "Admin interface accessible over unencrypted HTTP on 192.168.1.1 — HIGH",
      "lighttpd 1.4.45 CVE-2022-41556 on 192.168.1.1 — HIGH"
    ]
  }
}
```

---

## Privilege Levels

| Capability | Without Root | With Root |
|------------|-------------|-----------|
| ARP cache parse | Yes (/proc/net/arp) | Yes |
| TCP connect scan | Yes | Yes |
| ICMP ping sweep | Yes (via system ping) | Yes |
| SYN scan | No (post-MVP) | Post-MVP |
| UDP scan | No (post-MVP) | Post-MVP |

Privilege level is reported in `scan_meta.privilege_level` and `get_status` output.

---

## Security Considerations

- The tool scans networks you own or are authorized to scan.
- TCP connect scan leaves connection logs on target hosts.
- Banner grabbing sends minimal probes (GET /, SSH hello, FTP prompt).
- No credentials are tested against discovered services (that is Phase 4 — Router assessment, post-MVP).
- Scan results may contain sensitive service version info — treat output as sensitive.

---

## Limitations (MVP)

- No SYN scan (requires root + raw socket — post-MVP)
- No UDP scan (SNMP, DNS, mDNS — post-MVP)
- No router credential testing (post-MVP, Phase 4)
- No behavioral anomaly detection (post-MVP, Phase 3)
- No diff mode (post-MVP)
- CVE database is embedded and static; run `check_vulnerabilities` against known services for up-to-date results
