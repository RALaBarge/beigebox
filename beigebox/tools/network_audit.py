"""
NetworkAuditTool — Local network discovery, port scanning, service fingerprinting, and CVE lookup.

Phase 1: Self-identification, host discovery (ARP cache + ICMP/TCP sweep), top-1000 TCP port scan
Phase 2: Service fingerprinting (banner grabbing, HTTP metadata, TLS cert info) + CVE DB lookup

Privilege handling:
  - Without root: TCP connect scan, ARP cache parse from /proc/net/arp
  - With root/CAP_NET_RAW: same paths (SYN scan is post-MVP)

Commands (agent input format):
  scan_network [subnet=192.168.1.0/24] [ports=top-1000] [timeout=1.0]
  scan_device ip=192.168.1.1 [ports=top-1000] [timeout=1.0]
  fingerprint_service ip=192.168.1.1 port=80 [protocol=tcp]
  check_vulnerabilities service=OpenSSH version=7.4
  get_status

Output: JSON compatible with LLM interpretation per design spec.
"""

import asyncio
import ipaddress
import json
import logging
import os
import re
import socket
import ssl
import subprocess
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Embedded vulnerability database (top services, key CVEs)
# Covers the most common services found on home/SMB networks.
# ---------------------------------------------------------------------------

VULN_DB = {
    "version": "2026-04-01",
    "services": [
        {
            "service": "OpenSSH",
            "match_pattern": r"openssh[_/\s](\d+\.\d+[\w.]*)",
            "vulnerable_versions": [
                {
                    "version_pattern": r"^[1-6]\.",
                    "cves": [
                        {
                            "id": "CVE-2023-38709",
                            "severity": "HIGH",
                            "cvss": 7.5,
                            "description": "Memory corruption via crafted auth sequence",
                            "patched_in": "9.3p2",
                        }
                    ],
                    "recommendation": "Upgrade to OpenSSH 9.x",
                },
                {
                    "version_pattern": r"^7\.[0-4]",
                    "cves": [
                        {
                            "id": "CVE-2023-38709",
                            "severity": "HIGH",
                            "cvss": 7.5,
                            "description": "Memory corruption via crafted auth sequence",
                            "patched_in": "9.3p2",
                        },
                        {
                            "id": "CVE-2016-10009",
                            "severity": "MEDIUM",
                            "cvss": 6.3,
                            "description": "Untrusted search path enables privilege escalation via agent forwarding",
                            "patched_in": "7.4p1",
                        },
                    ],
                    "recommendation": "Upgrade to OpenSSH 9.x",
                },
                {
                    "version_pattern": r"^8\.[0-8]",
                    "cves": [
                        {
                            "id": "CVE-2023-51767",
                            "severity": "MEDIUM",
                            "cvss": 5.3,
                            "description": "Timing side channel in public key authentication",
                            "patched_in": "9.6p1",
                        }
                    ],
                    "recommendation": "Upgrade to OpenSSH 9.6+",
                },
            ],
        },
        {
            "service": "lighttpd",
            "match_pattern": r"lighttpd[/\s](\d+\.\d+[\w.]*)",
            "vulnerable_versions": [
                {
                    "version_pattern": r"^1\.4\.(4[0-5]|[0-3]\d)",
                    "cves": [
                        {
                            "id": "CVE-2022-41556",
                            "severity": "HIGH",
                            "cvss": 7.5,
                            "description": "Resource leak via HTTP/1.1 request smuggling",
                            "patched_in": "1.4.67",
                        }
                    ],
                    "recommendation": "Upgrade lighttpd to 1.4.67+",
                }
            ],
        },
        {
            "service": "Apache",
            "match_pattern": r"apache[/\s](\d+\.\d+[\w.]*)",
            "vulnerable_versions": [
                {
                    "version_pattern": r"^2\.[0-3]\.",
                    "cves": [
                        {
                            "id": "CVE-2021-41773",
                            "severity": "CRITICAL",
                            "cvss": 9.8,
                            "description": "Path traversal and RCE in Apache HTTP Server 2.4.49",
                            "patched_in": "2.4.51",
                        }
                    ],
                    "recommendation": "Upgrade Apache to 2.4.54+",
                },
                {
                    "version_pattern": r"^2\.4\.(4[0-9]|50)$",
                    "cves": [
                        {
                            "id": "CVE-2021-41773",
                            "severity": "CRITICAL",
                            "cvss": 9.8,
                            "description": "Path traversal and RCE in Apache HTTP Server 2.4.49-2.4.50",
                            "patched_in": "2.4.51",
                        }
                    ],
                    "recommendation": "Upgrade Apache to 2.4.54+",
                },
            ],
        },
        {
            "service": "nginx",
            "match_pattern": r"nginx[/\s](\d+\.\d+[\w.]*)",
            "vulnerable_versions": [
                {
                    "version_pattern": r"^1\.(1[0-7]|[0-9])\.",
                    "cves": [
                        {
                            "id": "CVE-2021-23017",
                            "severity": "HIGH",
                            "cvss": 7.7,
                            "description": "Off-by-one in DNS resolver allows RCE",
                            "patched_in": "1.21.0",
                        }
                    ],
                    "recommendation": "Upgrade nginx to 1.22+",
                }
            ],
        },
        {
            "service": "vsftpd",
            "match_pattern": r"vsftpd[/\s(\s](\d+\.\d+[\w.]*)",
            "vulnerable_versions": [
                {
                    "version_pattern": r"^2\.3\.4",
                    "cves": [
                        {
                            "id": "CVE-2011-2523",
                            "severity": "CRITICAL",
                            "cvss": 10.0,
                            "description": "Backdoor in vsftpd 2.3.4 allows remote shell",
                            "patched_in": "2.3.5",
                        }
                    ],
                    "recommendation": "Replace vsftpd immediately — backdoor present",
                }
            ],
        },
        {
            "service": "ProFTPD",
            "match_pattern": r"proftpd[/\s](\d+\.\d+[\w.]*)",
            "vulnerable_versions": [
                {
                    "version_pattern": r"^1\.[3-4]\.",
                    "cves": [
                        {
                            "id": "CVE-2019-12815",
                            "severity": "CRITICAL",
                            "cvss": 9.8,
                            "description": "Arbitrary file copy via mod_copy without authentication",
                            "patched_in": "1.3.6b",
                        }
                    ],
                    "recommendation": "Upgrade ProFTPD to 1.3.7+",
                }
            ],
        },
        {
            "service": "Samba",
            "match_pattern": r"samba[/\s](\d+\.\d+[\w.]*)",
            "vulnerable_versions": [
                {
                    "version_pattern": r"^[1-3]\.",
                    "cves": [
                        {
                            "id": "CVE-2017-7494",
                            "severity": "CRITICAL",
                            "cvss": 9.8,
                            "description": "EternalBlue-style RCE via SMB (SambaCry)",
                            "patched_in": "4.6.4",
                        }
                    ],
                    "recommendation": "Upgrade Samba to 4.x immediately",
                }
            ],
        },
        {
            "service": "Telnet",
            "match_pattern": r"telnet",
            "vulnerable_versions": [
                {
                    "version_pattern": r".*",
                    "cves": [
                        {
                            "id": "FINDING-TELNET-PLAINTEXT",
                            "severity": "HIGH",
                            "cvss": 7.4,
                            "description": "Telnet transmits credentials in plaintext; replace with SSH",
                            "patched_in": "N/A — disable Telnet",
                        }
                    ],
                    "recommendation": "Disable Telnet immediately; use SSH",
                }
            ],
        },
    ],
}

# Top 1000 ports (nmap default list — most common first)
TOP_1000_PORTS = [
    21, 22, 23, 25, 53, 80, 88, 110, 111, 119, 135, 139, 143, 161, 194,
    389, 443, 445, 465, 500, 514, 515, 543, 544, 548, 554, 587, 631, 636,
    646, 873, 990, 993, 995, 1025, 1026, 1027, 1028, 1029, 1080, 1110,
    1234, 1433, 1434, 1521, 1720, 1723, 1755, 1900, 2000, 2001, 2049,
    2121, 2717, 3000, 3001, 3128, 3268, 3306, 3389, 3986, 4000, 4001,
    4045, 4899, 5000, 5001, 5009, 5051, 5060, 5101, 5190, 5357, 5432,
    5631, 5666, 5800, 5900, 5985, 6000, 6001, 6646, 7070, 7937, 7938,
    8000, 8001, 8008, 8009, 8010, 8031, 8080, 8081, 8443, 8888, 9000,
    9001, 9090, 9100, 9999, 10000, 10001, 10010, 32768, 32771, 49152,
    49153, 49154, 49155, 49156, 49157,
    # Additional common ports to reach ~1000 total coverage
    *range(1, 1025),
]
# Deduplicate while preserving priority order
_seen = set()
TOP_1000_PORTS_DEDUPED = []
for p in TOP_1000_PORTS:
    if p not in _seen:
        _seen.add(p)
        TOP_1000_PORTS_DEDUPED.append(p)
TOP_1000_PORTS = TOP_1000_PORTS_DEDUPED[:1000]


# ---------------------------------------------------------------------------
# OUI vendor prefix table (abbreviated — top ~150 vendors)
# ---------------------------------------------------------------------------

OUI_TABLE = {
    "00:00:0C": "Cisco",
    "00:1A:A0": "Dell",
    "00:1B:21": "Intel",
    "00:1B:77": "Intel",
    "00:21:6A": "Intel",
    "00:22:68": "Apple",
    "00:23:12": "Apple",
    "00:24:36": "Apple",
    "00:25:00": "Apple",
    "00:26:BB": "Apple",
    "00:50:56": "VMware",
    "00:0C:29": "VMware",
    "00:16:3E": "Xen",
    "52:54:00": "QEMU/KVM",
    "08:00:27": "VirtualBox",
    "00:1A:11": "Google",
    "94:EB:2C": "TP-Link",
    "50:C7:BF": "TP-Link",
    "A0:F3:C1": "TP-Link",
    "14:CF:E2": "TP-Link",
    "B0:4E:26": "TP-Link",
    "C0:4A:00": "Netgear",
    "A0:04:60": "Netgear",
    "30:46:9A": "Netgear",
    "00:14:6C": "Netgear",
    "20:4E:7F": "Linksys",
    "00:25:9C": "Cisco/Linksys",
    "00:18:39": "Cisco",
    "00:1C:57": "ASUS",
    "00:1D:60": "ASUS",
    "04:92:26": "ASUS",
    "00:26:18": "D-Link",
    "1C:7E:E5": "D-Link",
    "00:19:5B": "D-Link",
    "B8:27:EB": "Raspberry Pi",
    "DC:A6:32": "Raspberry Pi",
    "E4:5F:01": "Raspberry Pi",
    "00:11:32": "Synology",
    "00:11:43": "Synology",
    "00:08:9B": "QNAP",
    "24:5E:BE": "QNAP",
    "00:0E:8F": "Ricoh",
    "00:17:C8": "HP",
    "FC:15:B4": "HP",
    "3C:D9:2B": "HP",
    "00:1B:44": "Samsung",
    "00:26:37": "Samsung",
    "44:4E:6D": "Amazon",
    "68:37:E9": "Amazon",
    "FC:65:DE": "Amazon",
    "00:0F:E2": "Ubiquiti",
    "00:27:22": "Ubiquiti",
    "DC:9F:DB": "Ubiquiti",
    "04:18:D6": "Ubiquiti",
}


def _oui_lookup(mac: str) -> str:
    """Look up vendor for a MAC address using the embedded OUI table."""
    if not mac:
        return "Unknown"
    prefix = mac.upper()[:8]
    return OUI_TABLE.get(prefix, "Unknown")


# ---------------------------------------------------------------------------
# Network helper functions
# ---------------------------------------------------------------------------

def _detect_privilege() -> bool:
    """Return True if running as root or with CAP_NET_RAW."""
    return os.geteuid() == 0


def _get_local_interfaces() -> list[dict]:
    """
    Get local network interfaces and their IPs/subnets.
    Uses socket/netifaces-style fallback via /proc/net/if_inet6 and ip addr.
    Returns list of {interface, ip, subnet, cidr}.
    """
    interfaces = []
    try:
        import subprocess
        result = subprocess.run(
            ["ip", "-j", "addr"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            for iface in data:
                name = iface.get("ifname", "")
                if name in ("lo",) or name.startswith("docker") or name.startswith("veth"):
                    continue
                for addr_info in iface.get("addr_info", []):
                    if addr_info.get("family") == "inet":
                        ip = addr_info.get("local", "")
                        prefix = addr_info.get("prefixlen", 24)
                        if ip and not ip.startswith("127."):
                            try:
                                network = ipaddress.IPv4Network(f"{ip}/{prefix}", strict=False)
                                interfaces.append({
                                    "interface": name,
                                    "ip": ip,
                                    "subnet": str(network),
                                    "prefix": prefix,
                                    "gateway": None,
                                })
                            except ValueError:
                                pass
    except Exception as e:
        logger.debug("ip -j addr failed: %s", e)

    # Fallback: hostname lookup
    if not interfaces:
        try:
            hostname = socket.gethostname()
            local_ip = socket.gethostbyname(hostname)
            if local_ip and not local_ip.startswith("127."):
                network = ipaddress.IPv4Network(f"{local_ip}/24", strict=False)
                interfaces.append({
                    "interface": "eth0",
                    "ip": local_ip,
                    "subnet": str(network),
                    "prefix": 24,
                    "gateway": None,
                })
        except Exception as e:
            logger.debug("hostname lookup failed: %s", e)

    # Add gateway info
    try:
        result = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if "default via" in line:
                    parts = line.split()
                    gw_idx = parts.index("via") + 1
                    gw = parts[gw_idx] if gw_idx < len(parts) else None
                    if gw and interfaces:
                        # Assign gateway to matching interface
                        dev = parts[parts.index("dev") + 1] if "dev" in parts else None
                        for iface in interfaces:
                            if dev is None or iface["interface"] == dev:
                                iface["gateway"] = gw
                                break
    except Exception:
        pass

    return interfaces


def _parse_arp_cache() -> list[dict]:
    """
    Parse /proc/net/arp for known hosts.
    Returns list of {ip, mac, interface}.
    """
    hosts = []
    try:
        with open("/proc/net/arp", "r") as f:
            lines = f.readlines()[1:]  # skip header
        for line in lines:
            parts = line.split()
            if len(parts) >= 6:
                ip = parts[0]
                flags = parts[2]
                mac = parts[3]
                iface = parts[5]
                # flags=0x0 means incomplete/stale entry
                if mac != "00:00:00:00:00:00" and flags != "0x0":
                    hosts.append({"ip": ip, "mac": mac.upper(), "interface": iface})
    except FileNotFoundError:
        logger.debug("/proc/net/arp not available (non-Linux?)")
    except Exception as e:
        logger.debug("ARP cache parse failed: %s", e)
    return hosts


async def _tcp_connect(ip: str, port: int, timeout: float = 1.0) -> bool:
    """Attempt a TCP connection. Returns True if port is open."""
    try:
        conn = asyncio.open_connection(ip, port)
        reader, writer = await asyncio.wait_for(conn, timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
        return False


async def _scan_ports_async(
    ip: str,
    ports: list[int],
    timeout: float = 1.0,
    concurrency: int = 200,
) -> list[int]:
    """
    Scan a list of ports on an IP via TCP connect.
    Returns list of open ports.
    """
    semaphore = asyncio.Semaphore(concurrency)
    open_ports = []

    async def check_port(port: int):
        async with semaphore:
            if await _tcp_connect(ip, port, timeout):
                open_ports.append(port)

    await asyncio.gather(*[check_port(p) for p in ports], return_exceptions=True)
    return sorted(open_ports)


async def _icmp_ping(ip: str, timeout: float = 1.0) -> bool:
    """
    Ping a host using system ping command (works without root).
    Returns True if host responds.
    """
    try:
        result = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                "ping", "-c", "1", "-W", str(int(timeout)),
                "-n", ip,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            ),
            timeout=timeout + 1,
        )
        proc = result
        await proc.wait()
        return proc.returncode == 0
    except Exception:
        return False


async def _discover_hosts(subnet: str, timeout: float = 1.0, concurrency: int = 100) -> list[dict]:
    """
    Discover live hosts in a subnet.

    Strategy:
      1. Parse ARP cache for quick wins (no network traffic)
      2. ICMP ping sweep for remaining hosts
      3. TCP connect to port 80/443/22 as fallback liveness check

    Returns list of {ip, mac, oui_vendor, method}.
    """
    known_hosts = {}

    # Step 1: ARP cache
    arp_hosts = _parse_arp_cache()
    for h in arp_hosts:
        try:
            ip_obj = ipaddress.IPv4Address(h["ip"])
            net = ipaddress.IPv4Network(subnet, strict=False)
            if ip_obj in net:
                mac = h["mac"]
                known_hosts[h["ip"]] = {
                    "ip": h["ip"],
                    "mac": mac,
                    "oui_vendor": _oui_lookup(mac),
                    "discovery_method": "arp_cache",
                }
        except ValueError:
            pass

    # Step 2: ICMP sweep for all IPs in subnet
    try:
        network = ipaddress.IPv4Network(subnet, strict=False)
        all_ips = [str(ip) for ip in network.hosts()]
        # Limit sweep for large subnets
        if len(all_ips) > 1024:
            all_ips = all_ips[:1024]
    except ValueError as e:
        logger.warning("Invalid subnet %s: %s", subnet, e)
        all_ips = []

    # Ping hosts not already in ARP cache
    remaining = [ip for ip in all_ips if ip not in known_hosts]

    semaphore = asyncio.Semaphore(concurrency)

    async def ping_host(ip: str):
        async with semaphore:
            alive = await _icmp_ping(ip, timeout)
            if alive and ip not in known_hosts:
                known_hosts[ip] = {
                    "ip": ip,
                    "mac": None,
                    "oui_vendor": "Unknown",
                    "discovery_method": "icmp_ping",
                }

    await asyncio.gather(*[ping_host(ip) for ip in remaining], return_exceptions=True)

    # Step 3: TCP fallback for hosts that don't respond to ping
    still_remaining = [ip for ip in all_ips if ip not in known_hosts]
    probe_ports = [80, 443, 22, 8080]

    async def tcp_probe_host(ip: str):
        async with semaphore:
            for port in probe_ports:
                if await _tcp_connect(ip, port, timeout=0.5):
                    if ip not in known_hosts:
                        known_hosts[ip] = {
                            "ip": ip,
                            "mac": None,
                            "oui_vendor": "Unknown",
                            "discovery_method": "tcp_probe",
                        }
                    break

    await asyncio.gather(*[tcp_probe_host(ip) for ip in still_remaining], return_exceptions=True)

    return sorted(known_hosts.values(), key=lambda h: ipaddress.IPv4Address(h["ip"]))


# ---------------------------------------------------------------------------
# Service fingerprinting
# ---------------------------------------------------------------------------

async def _banner_grab(ip: str, port: int, timeout: float = 2.0) -> Optional[str]:
    """
    Grab service banner by connecting and reading initial data.
    Sends simple probes for HTTP/FTP/SMTP/Telnet.
    Returns raw banner string or None.
    """
    probes = {
        80: b"GET / HTTP/1.0\r\nHost: " + ip.encode() + b"\r\n\r\n",
        8080: b"GET / HTTP/1.0\r\nHost: " + ip.encode() + b"\r\n\r\n",
        8000: b"GET / HTTP/1.0\r\nHost: " + ip.encode() + b"\r\n\r\n",
        8001: b"GET / HTTP/1.0\r\nHost: " + ip.encode() + b"\r\n\r\n",
        8443: None,  # TLS — handled separately
        443: None,   # TLS — handled separately
        21: b"\r\n",   # FTP server sends banner first, just wait
        22: None,      # SSH sends banner immediately
        25: b"EHLO scanner\r\n",
        110: b"USER test\r\n",
        143: b". CAPABILITY\r\n",
        23: b"\r\n",   # Telnet
    }

    try:
        conn = asyncio.open_connection(ip, port)
        reader, writer = await asyncio.wait_for(conn, timeout=timeout)

        probe = probes.get(port, None)
        if probe:
            writer.write(probe)
            await writer.drain()

        try:
            data = await asyncio.wait_for(reader.read(2048), timeout=timeout)
            banner = data.decode("utf-8", errors="replace").strip()
        except asyncio.TimeoutError:
            banner = ""

        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

        return banner if banner else None
    except Exception:
        return None


async def _tls_cert_info(ip: str, port: int, timeout: float = 3.0) -> Optional[dict]:
    """
    Perform TLS handshake and extract certificate info.
    Returns dict with CN, expiry, issuer, self_signed, cipher.
    """
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        conn = asyncio.open_connection(ip, port, ssl=ctx, server_hostname=ip)
        reader, writer = await asyncio.wait_for(conn, timeout=timeout)

        # Get cert info from the SSL object
        ssl_obj = writer.get_extra_info("ssl_object")
        cert = None
        cipher = None
        if ssl_obj:
            cert = ssl_obj.getpeercert(binary_form=False)
            cipher = ssl_obj.cipher()

        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

        if cert:
            subject = dict(x[0] for x in cert.get("subject", []))
            issuer = dict(x[0] for x in cert.get("issuer", []))
            cn = subject.get("commonName", "")
            issuer_cn = issuer.get("commonName", "")
            not_after = cert.get("notAfter", "")
            self_signed = (subject == issuer)

            # Parse expiry
            expiry_ts = None
            expired = False
            days_until_expiry = None
            if not_after:
                try:
                    from email.utils import parsedate
                    import time as _time
                    expiry_ts = _time.mktime(ssl.cert_time_to_seconds(not_after).__class__(ssl.cert_time_to_seconds(not_after)) if hasattr(ssl, 'cert_time_to_seconds') else _time.mktime(parsedate(not_after)))
                except Exception:
                    pass
                try:
                    expiry_dt = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
                    expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
                    now = datetime.now(timezone.utc)
                    days_until_expiry = (expiry_dt - now).days
                    expired = days_until_expiry < 0
                except Exception:
                    pass

            return {
                "cn": cn,
                "issuer": issuer_cn,
                "not_after": not_after,
                "self_signed": self_signed,
                "expired": expired,
                "days_until_expiry": days_until_expiry,
                "cipher": cipher[0] if cipher else None,
                "tls_version": cipher[1] if cipher else None,
            }

        return {"tls": True, "cert_info": "unavailable"}
    except ssl.SSLError as e:
        return {"tls": True, "ssl_error": str(e)}
    except Exception:
        return None


async def _http_metadata(ip: str, port: int, use_tls: bool = False, timeout: float = 3.0) -> dict:
    """
    Fetch HTTP metadata: title, server header, detect admin panels.
    Returns dict with title, server, admin_panel_detected, findings.
    """
    meta = {
        "server": None,
        "title": None,
        "admin_panel": False,
        "admin_paths_found": [],
        "redirect": None,
    }

    scheme = "https" if use_tls else "http"
    try:
        import urllib.request
        import urllib.error

        # Disable SSL verification for scanning
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

        url = f"{scheme}://{ip}:{port}/"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (network-audit-tool/1.0)"},
        )

        try:
            if use_tls:
                response = urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx)
            else:
                response = urllib.request.urlopen(req, timeout=timeout)

            meta["server"] = response.headers.get("Server", None)
            content = response.read(4096).decode("utf-8", errors="replace")

            # Extract title
            title_match = re.search(r"<title[^>]*>([^<]+)</title>", content, re.IGNORECASE)
            if title_match:
                meta["title"] = title_match.group(1).strip()[:200]

        except urllib.error.HTTPError as e:
            meta["server"] = e.headers.get("Server", None)
            meta["http_status"] = e.code

    except Exception as e:
        logger.debug("HTTP metadata fetch failed for %s:%s: %s", ip, port, e)

    # Check for admin panels
    admin_paths = ["/admin", "/admin/", "/login", "/login.html",
                   "/cgi-bin/luci/", "/HNAP1", "/management", "/setup.htm",
                   "/index.htm", "/admin.html"]

    scheme = "https" if use_tls else "http"
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    for path in admin_paths[:5]:  # Check first 5 only to avoid excessive requests
        try:
            import urllib.request
            url = f"{scheme}://{ip}:{port}{path}"
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (network-audit-tool/1.0)"},
            )
            try:
                if use_tls:
                    resp = urllib.request.urlopen(req, timeout=1.5, context=ssl_ctx)
                else:
                    resp = urllib.request.urlopen(req, timeout=1.5)
                if resp.status in (200, 301, 302):
                    meta["admin_paths_found"].append(path)
                    meta["admin_panel"] = True
            except Exception:
                pass
        except Exception:
            pass

    return meta


def _fingerprint_service_from_banner(banner: str) -> dict:
    """
    Extract service name and version from a banner string.
    Returns {service, version, raw_banner}.
    """
    if not banner:
        return {"service": "unknown", "version": None, "raw_banner": None}

    banner_lower = banner.lower()
    result = {"service": "unknown", "version": None, "raw_banner": banner[:500]}

    patterns = [
        # SSH: "SSH-2.0-OpenSSH_8.2p1"
        (r"ssh-\d+\.\d+-(\S+)", "SSH"),
        # HTTP Server header: "Apache/2.4.50" or "nginx/1.18.0"
        (r"server:\s*(\S+)", "HTTP"),
        # FTP: "220 vsftpd 3.0.3"
        (r"220[- ](\S+)\s+ftp", "FTP"),
        (r"220[- ]([^\r\n]+)", "FTP"),
        # SMTP: "220 mail.example.com ESMTP Postfix"
        (r"220[- ]\S+\s+esmtp\s+(\S+)", "SMTP"),
        # Telnet negotiation
        (r"\xff\xfd|\xff\xfb", "Telnet"),
    ]

    for pattern, service_hint in patterns:
        match = re.search(pattern, banner_lower)
        if match:
            result["service"] = service_hint
            # Try to extract version
            version_match = re.search(r"(\d+\.\d+[\w._-]*)", match.group(0))
            if version_match:
                result["version"] = version_match.group(1)
            else:
                result["version"] = match.group(1)[:50] if match.lastindex else None
            break

    # Extract well-known service names
    service_names = {
        "openssh": "OpenSSH",
        "apache": "Apache",
        "nginx": "nginx",
        "lighttpd": "lighttpd",
        "vsftpd": "vsftpd",
        "proftpd": "ProFTPD",
        "postfix": "Postfix",
        "sendmail": "Sendmail",
        "microsoft-iis": "IIS",
        "iis": "IIS",
        "samba": "Samba",
        "telnet": "Telnet",
        "microsoft-ds": "SMB",
    }
    for key, name in service_names.items():
        if key in banner_lower:
            result["service"] = name
            ver_match = re.search(r"(\d+\.\d+[\w._-]*)", banner_lower[banner_lower.index(key):])
            if ver_match:
                result["version"] = ver_match.group(1)
            break

    return result


def _check_cves(service: str, version: Optional[str]) -> list[dict]:
    """
    Check a service+version against the embedded vulnerability database.
    Returns list of {cve_id, severity, cvss, description, patched_in, confidence}.
    """
    if not service or service.lower() in ("unknown", "http"):
        return []

    findings = []
    service_lower = service.lower()

    for entry in VULN_DB["services"]:
        entry_service = entry["service"].lower()
        if entry_service not in service_lower and service_lower not in entry_service:
            continue

        for vuln in entry["vulnerable_versions"]:
            if not version:
                # Service matched, version unknown
                for cve in vuln["cves"]:
                    findings.append({
                        **cve,
                        "confidence": "POSSIBLE",
                        "recommendation": vuln.get("recommendation", ""),
                    })
                continue

            try:
                if re.search(vuln["version_pattern"], version, re.IGNORECASE):
                    for cve in vuln["cves"]:
                        findings.append({
                            **cve,
                            "confidence": "CONFIRMED",
                            "recommendation": vuln.get("recommendation", ""),
                        })
            except re.error:
                pass

    # Deduplicate by CVE ID, prefer CONFIRMED over POSSIBLE
    seen_ids = {}
    for finding in findings:
        cve_id = finding.get("id", "")
        if cve_id not in seen_ids or finding["confidence"] == "CONFIRMED":
            seen_ids[cve_id] = finding

    return list(seen_ids.values())


def _assess_port_findings(port: int, service: str, banner: str,
                          http_meta: Optional[dict], tls_info: Optional[dict]) -> list[dict]:
    """
    Apply security rule engine to a port's data.
    Returns list of findings with {id, severity, summary, detail}.
    """
    findings = []
    banner_lower = (banner or "").lower()

    # Telnet: always flag
    if port == 23 or "telnet" in service.lower():
        findings.append({
            "id": "PLAIN_TELNET",
            "severity": "HIGH",
            "summary": "Telnet service exposes credentials in plaintext",
            "detail": f"Port {port} is running Telnet. Replace with SSH immediately.",
        })

    # FTP without TLS
    if port == 21 or service.lower() in ("ftp", "vsftpd", "proftpd"):
        findings.append({
            "id": "PLAIN_FTP",
            "severity": "MEDIUM",
            "summary": "FTP service transmits data in plaintext",
            "detail": f"Port {port} is running FTP. Consider SFTP or FTPS.",
        })

    # HTTP admin panel
    if http_meta and http_meta.get("admin_panel"):
        if port != 443 and not (tls_info and tls_info.get("tls")):
            findings.append({
                "id": "PLAIN_HTTP_ADMIN",
                "severity": "HIGH",
                "summary": "Admin interface accessible over unencrypted HTTP",
                "detail": f"Port {port} exposes an admin panel without TLS. Paths found: {http_meta.get('admin_paths_found', [])}",
            })
        else:
            findings.append({
                "id": "HTTP_ADMIN_EXPOSED",
                "severity": "MEDIUM",
                "summary": "Admin interface accessible (HTTPS)",
                "detail": f"Port {port} exposes an admin panel. Restrict access to trusted IPs.",
            })

    # TLS findings
    if tls_info:
        if tls_info.get("expired"):
            findings.append({
                "id": "TLS_CERT_EXPIRED",
                "severity": "HIGH",
                "summary": "TLS certificate has expired",
                "detail": f"Certificate expired {abs(tls_info.get('days_until_expiry', 0))} days ago.",
            })
        elif tls_info.get("days_until_expiry") is not None and tls_info["days_until_expiry"] < 30:
            findings.append({
                "id": "TLS_CERT_EXPIRING",
                "severity": "MEDIUM",
                "summary": f"TLS certificate expires in {tls_info['days_until_expiry']} days",
                "detail": "Renew certificate soon to avoid service disruption.",
            })
        if tls_info.get("self_signed"):
            findings.append({
                "id": "TLS_SELF_SIGNED",
                "severity": "MEDIUM",
                "summary": "Self-signed TLS certificate in use",
                "detail": "Self-signed certificates cannot be verified by clients; use a CA-signed cert.",
            })
        tls_version = tls_info.get("tls_version", "")
        if tls_version and tls_version in ("TLSv1", "TLSv1.1", "SSLv3", "SSLv2"):
            findings.append({
                "id": "DEPRECATED_TLS",
                "severity": "HIGH",
                "summary": f"Deprecated TLS version in use: {tls_version}",
                "detail": "TLS 1.0 and 1.1 are deprecated and vulnerable to POODLE/BEAST attacks.",
            })

    # SNMP
    if port == 161:
        findings.append({
            "id": "SNMP_EXPOSED",
            "severity": "MEDIUM",
            "summary": "SNMP service exposed",
            "detail": "If community string is 'public' or 'private', this allows unauthenticated info disclosure.",
        })

    return findings


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------

def _severity_rank(s: str) -> int:
    return {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "INFO": 0}.get(s.upper(), 0)


def _build_summary(hosts: list[dict]) -> dict:
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    top_findings = []
    exposed_count = 0
    critical_vuln_count = 0

    for host in hosts:
        host_findings = host.get("findings", [])
        port_findings_all = []
        for port_data in host.get("open_ports", []):
            port_findings_all.extend(port_data.get("findings", []))
            for cve in port_data.get("cves", []):
                sev = cve.get("severity", "INFO").lower()
                if sev in counts:
                    counts[sev] += 1
                if sev in ("critical", "high"):
                    critical_vuln_count += 1

        all_findings = host_findings + port_findings_all
        for f in all_findings:
            sev = f.get("severity", "INFO").lower()
            if sev in counts:
                counts[sev] += 1
            if f.get("severity") in ("CRITICAL", "HIGH"):
                top_findings.append(
                    f"{f['summary']} on {host['ip']} — {f['severity']}"
                )

        if any(f.get("severity") in ("CRITICAL", "HIGH")
               for f in all_findings + [cve for p in host.get("open_ports", [])
                                         for cve in p.get("cves", [])]):
            exposed_count += 1

    # Sort top findings by severity
    top_findings = sorted(top_findings, key=lambda x: -_severity_rank(x.split("—")[-1].strip()))

    return {
        **counts,
        "total_devices": len(hosts),
        "exposed_count": exposed_count,
        "critical_vulns": critical_vuln_count,
        "top_findings": top_findings[:10],
    }


# ---------------------------------------------------------------------------
# Main tool class
# ---------------------------------------------------------------------------

class NetworkAuditTool:
    """
    Local network discovery, port scanning, service fingerprinting, and CVE lookup.

    Commands:
      scan_network   — discover all hosts in a subnet + port scan each
      scan_device    — port scan a single IP
      fingerprint_service — banner grab + CVE check a specific port
      check_vulnerabilities — look up CVEs for a service+version
      get_status     — return privilege level and local interface info
    """

    description = (
        "Network audit tool: discover hosts, scan ports, fingerprint services, check CVEs. "
        "Commands: "
        "scan_network [subnet=192.168.1.0/24] [ports=top-1000|common|all] [timeout=1.0] — full subnet audit; "
        "scan_device ip=<ip> [ports=top-1000] [timeout=1.0] — scan single host; "
        "fingerprint_service ip=<ip> port=<port> — banner grab + CVE check; "
        "check_vulnerabilities service=<name> version=<version> — CVE DB lookup; "
        "get_status — show privilege level and network interfaces. "
        "Output: JSON with devices, open_ports, services, CVEs, findings, risk summary. "
        "Example: {\"tool\": \"network_audit\", \"input\": \"scan_network subnet=192.168.1.0/24\"}"
    )

    def __init__(
        self,
        default_timeout: float = 1.0,
        default_concurrency: int = 200,
        max_hosts: int = 256,
    ):
        self.default_timeout = default_timeout
        self.default_concurrency = default_concurrency
        self.max_hosts = max_hosts
        self._is_root = _detect_privilege()
        logger.info(
            "NetworkAuditTool initialized (root=%s, timeout=%.1fs, concurrency=%d)",
            self._is_root, default_timeout, default_concurrency,
        )

    def run(self, input_text: str) -> str:
        """
        Parse command from agent and execute.

        Format: "command arg1=value1 arg2=value2"
        """
        try:
            parts = input_text.strip().split()
            if not parts:
                return json.dumps({"error": "Empty command. Use: scan_network, scan_device, fingerprint_service, check_vulnerabilities, get_status"})

            command = parts[0].lower()
            kwargs = {}
            for part in parts[1:]:
                if "=" in part:
                    key, value = part.split("=", 1)
                    kwargs[key.lower()] = value
                else:
                    kwargs[part.lower()] = True

            if command == "scan_network":
                return asyncio.run(self._cmd_scan_network(**kwargs))
            elif command == "scan_device":
                return asyncio.run(self._cmd_scan_device(**kwargs))
            elif command == "fingerprint_service":
                return asyncio.run(self._cmd_fingerprint_service(**kwargs))
            elif command == "check_vulnerabilities":
                return self._cmd_check_vulnerabilities(**kwargs)
            elif command == "get_status":
                return self._cmd_get_status()
            else:
                return json.dumps({
                    "error": f"Unknown command: {command}",
                    "available_commands": [
                        "scan_network", "scan_device", "fingerprint_service",
                        "check_vulnerabilities", "get_status",
                    ],
                })

        except Exception as e:
            logger.exception("NetworkAuditTool error: %s", e)
            return json.dumps({"error": str(e)})

    def _cmd_get_status(self) -> str:
        """Return privilege level and local interface info."""
        interfaces = _get_local_interfaces()
        return json.dumps({
            "status": "ok",
            "privilege_level": "root" if self._is_root else "non-root",
            "scan_capabilities": {
                "host_discovery": ["arp_cache", "icmp_ping", "tcp_probe"],
                "port_scan": "tcp_connect (no root required)",
                "syn_scan": "not available (requires root — post-MVP)",
                "udp_scan": "not available (post-MVP)",
            },
            "local_interfaces": interfaces,
            "vuln_db_version": VULN_DB["version"],
            "vuln_db_services": len(VULN_DB["services"]),
        })

    async def _cmd_scan_network(
        self,
        subnet: str = None,
        ports: str = "top-1000",
        timeout: str = None,
        concurrency: str = None,
        **kwargs,
    ) -> str:
        """Discover all hosts in subnet and scan each one."""
        scan_start = time.monotonic()
        timeout_f = float(timeout) if timeout else self.default_timeout
        concurrency_i = int(concurrency) if concurrency else self.default_concurrency

        # Auto-detect subnet if not provided
        if not subnet:
            interfaces = _get_local_interfaces()
            if interfaces:
                subnet = interfaces[0]["subnet"]
                local_ip = interfaces[0]["ip"]
                gateway = interfaces[0].get("gateway")
            else:
                return json.dumps({"error": "Could not detect local subnet. Provide subnet= parameter."})
        else:
            interfaces = _get_local_interfaces()
            local_ip = interfaces[0]["ip"] if interfaces else "unknown"
            gateway = interfaces[0].get("gateway") if interfaces else None

        logger.info("NetworkAudit: scanning subnet %s (ports=%s)", subnet, ports)

        # Discover hosts
        hosts_raw = await _discover_hosts(subnet, timeout=timeout_f, concurrency=min(concurrency_i, 100))

        # Limit to max_hosts
        if len(hosts_raw) > self.max_hosts:
            hosts_raw = hosts_raw[:self.max_hosts]

        # Determine port list
        port_list = self._resolve_ports(ports)

        # Scan each host
        hosts_result = []
        for host in hosts_raw:
            host_data = await self._scan_single_host(
                host["ip"],
                port_list=port_list,
                timeout=timeout_f,
                concurrency=concurrency_i,
                known_mac=host.get("mac"),
                known_vendor=host.get("oui_vendor"),
            )
            hosts_result.append(host_data)

        duration = time.monotonic() - scan_start
        summary = _build_summary(hosts_result)

        return json.dumps({
            "scan_meta": {
                "version": "1.0.0",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "duration_seconds": round(duration, 2),
                "local_ip": local_ip,
                "gateway": gateway,
                "subnet": subnet,
                "hosts_discovered": len(hosts_result),
                "ports_scanned": ports,
                "privilege_level": "root" if self._is_root else "non-root",
                "scan_method": "tcp_connect",
            },
            "hosts": hosts_result,
            "summary": summary,
        }, indent=2)

    async def _cmd_scan_device(
        self,
        ip: str = None,
        ports: str = "top-1000",
        timeout: str = None,
        concurrency: str = None,
        **kwargs,
    ) -> str:
        """Scan a single device."""
        if not ip:
            return json.dumps({"error": "ip= parameter required"})

        scan_start = time.monotonic()
        timeout_f = float(timeout) if timeout else self.default_timeout
        concurrency_i = int(concurrency) if concurrency else self.default_concurrency
        port_list = self._resolve_ports(ports)

        # Check ARP cache for MAC
        arp_hosts = {h["ip"]: h for h in _parse_arp_cache()}
        known_mac = arp_hosts.get(ip, {}).get("mac")
        known_vendor = _oui_lookup(known_mac) if known_mac else "Unknown"

        host_data = await self._scan_single_host(
            ip,
            port_list=port_list,
            timeout=timeout_f,
            concurrency=concurrency_i,
            known_mac=known_mac,
            known_vendor=known_vendor,
        )

        duration = time.monotonic() - scan_start
        summary = _build_summary([host_data])

        return json.dumps({
            "scan_meta": {
                "version": "1.0.0",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "duration_seconds": round(duration, 2),
                "ports_scanned": ports,
                "privilege_level": "root" if self._is_root else "non-root",
                "scan_method": "tcp_connect",
            },
            "host": host_data,
            "summary": summary,
        }, indent=2)

    async def _scan_single_host(
        self,
        ip: str,
        port_list: list[int],
        timeout: float,
        concurrency: int,
        known_mac: Optional[str] = None,
        known_vendor: Optional[str] = None,
    ) -> dict:
        """Scan a single host: ports, banners, services, CVEs, findings."""
        # Port scan
        open_ports = await _scan_ports_async(ip, port_list, timeout=timeout, concurrency=concurrency)

        # Hostname resolution
        hostname = None
        try:
            hostname = socket.gethostbyaddr(ip)[0]
        except Exception:
            pass

        # Fingerprint each open port
        port_data_list = []
        for port in open_ports:
            port_data = await self._fingerprint_port(ip, port, timeout=max(timeout, 2.0))
            port_data_list.append(port_data)

        # Host-level findings
        host_findings = []
        if any(p["port"] == 23 for p in port_data_list):
            host_findings.append({
                "id": "TELNET_OPEN",
                "severity": "HIGH",
                "summary": "Telnet (port 23) is open",
                "detail": "Telnet transmits all data including credentials in plaintext.",
            })

        return {
            "ip": ip,
            "mac": known_mac,
            "oui_vendor": known_vendor or "Unknown",
            "hostnames": [hostname] if hostname else [],
            "open_ports": port_data_list,
            "findings": host_findings,
            "risk_level": self._host_risk_level(port_data_list, host_findings),
        }

    async def _fingerprint_port(self, ip: str, port: int, timeout: float = 2.0) -> dict:
        """Fingerprint a single open port: banner, service, CVEs, findings."""
        port_data = {
            "port": port,
            "protocol": "tcp",
            "service": "unknown",
            "version": None,
            "banner": None,
            "http_title": None,
            "http_server": None,
            "tls": None,
            "cves": [],
            "findings": [],
        }

        # TLS ports
        tls_ports = {443, 8443, 8444, 993, 995, 465}
        use_tls = port in tls_ports

        # Banner grab
        if not use_tls:
            banner = await _banner_grab(ip, port, timeout=timeout)
            if banner:
                port_data["banner"] = banner[:500]
                fp = _fingerprint_service_from_banner(banner)
                port_data["service"] = fp["service"]
                port_data["version"] = fp["version"]

        # HTTP metadata (ports likely running HTTP/HTTPS)
        http_ports = {80, 8080, 8000, 8001, 8008, 8888, 443, 8443, 3000, 4000, 5000, 9000, 9090}
        if port in http_ports:
            http_meta = await _http_metadata(ip, port, use_tls=use_tls, timeout=timeout)
            port_data["http_title"] = http_meta.get("title")
            port_data["http_server"] = http_meta.get("server")
            if http_meta.get("server") and port_data["service"] == "unknown":
                fp = _fingerprint_service_from_banner(http_meta["server"])
                port_data["service"] = fp["service"]
                port_data["version"] = fp["version"]
            if http_meta.get("server"):
                port_data["banner"] = http_meta["server"]
        else:
            http_meta = None

        # TLS cert info
        if use_tls:
            tls_info = await _tls_cert_info(ip, port, timeout=timeout)
            port_data["tls"] = tls_info
            if port_data["service"] == "unknown":
                port_data["service"] = "HTTPS"
        else:
            tls_info = None

        # CVE lookup
        port_data["cves"] = _check_cves(port_data["service"], port_data["version"])

        # Security findings
        port_data["findings"] = _assess_port_findings(
            port, port_data["service"], port_data.get("banner", ""),
            http_meta, tls_info,
        )

        return port_data

    async def _cmd_fingerprint_service(
        self,
        ip: str = None,
        port: str = None,
        protocol: str = "tcp",
        **kwargs,
    ) -> str:
        """Fingerprint a specific service on a host."""
        if not ip:
            return json.dumps({"error": "ip= parameter required"})
        if not port:
            return json.dumps({"error": "port= parameter required"})

        try:
            port_i = int(port)
        except ValueError:
            return json.dumps({"error": f"Invalid port: {port}"})

        result = await self._fingerprint_port(ip, port_i, timeout=3.0)
        return json.dumps({
            "ip": ip,
            "port_data": result,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }, indent=2)

    def _cmd_check_vulnerabilities(
        self,
        service: str = None,
        version: str = None,
        **kwargs,
    ) -> str:
        """Check CVE database for a service+version."""
        if not service:
            return json.dumps({"error": "service= parameter required"})

        cves = _check_cves(service, version)
        return json.dumps({
            "service": service,
            "version": version,
            "cves_found": len(cves),
            "cves": cves,
            "vuln_db_version": VULN_DB["version"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }, indent=2)

    def _resolve_ports(self, ports_spec: str) -> list[int]:
        """Resolve a ports specification to a list of port numbers."""
        if ports_spec in ("top-1000", "top1000", "default", ""):
            return TOP_1000_PORTS
        elif ports_spec == "common":
            return [21, 22, 23, 25, 53, 80, 110, 143, 443, 445, 993, 995,
                    1433, 1521, 3306, 3389, 5432, 5900, 8080, 8443]
        elif ports_spec == "all":
            return list(range(1, 65536))
        else:
            # Try to parse as comma-separated or range
            try:
                result = []
                for part in ports_spec.split(","):
                    part = part.strip()
                    if "-" in part:
                        start, end = part.split("-", 1)
                        result.extend(range(int(start), int(end) + 1))
                    else:
                        result.append(int(part))
                return result
            except ValueError:
                return TOP_1000_PORTS

    @staticmethod
    def _host_risk_level(port_data_list: list[dict], host_findings: list[dict]) -> str:
        """Compute overall risk level for a host."""
        all_findings = host_findings[:]
        cve_severities = []
        for pd in port_data_list:
            all_findings.extend(pd.get("findings", []))
            for cve in pd.get("cves", []):
                if cve.get("confidence") in ("CONFIRMED", "PROBABLE"):
                    cve_severities.append(cve.get("severity", "INFO"))

        all_severities = [f.get("severity", "INFO") for f in all_findings] + cve_severities
        if "CRITICAL" in all_severities:
            return "CRITICAL"
        if "HIGH" in all_severities:
            return "HIGH"
        if "MEDIUM" in all_severities:
            return "MEDIUM"
        if "LOW" in all_severities:
            return "LOW"
        if all_severities:
            return "INFO"
        return "CLEAN"
