# BeigeBox Pen/Sec MCP

A separate MCP endpoint (`POST /pen-mcp`) exposing 53 wrapped offensive-security
tools spanning network scanning, web vuln, subdomain enum, URL/parameter
discovery, SSL/TLS audit, SNMP/NetBIOS/LDAP, SMB/AD lateral, Kerberos
(impacket + kerbrute), credential testing, OSINT, and binary forensics.
Uses the same JSON-RPC McpServer implementation as `/mcp` but with its
own registry so security tooling stays out of the default tool surface.

Inspired by [HexStrike AI](https://github.com/0x4m4/hexstrike-ai) (MIT). We
re-implement the *nix wrappers cleanly using argv-list `subprocess.run`
(no shell string concatenation, no f-string injection) and gracefully handle
missing binaries so the server stays usable when only some tools are
installed.

## Enable

In `config.yaml`:

```yaml
security_mcp:
  enabled: true
```

Restart BeigeBox. The startup log will report
`Pen/Sec MCP server: enabled (POST /pen-mcp) — N wrappers loaded`.

## Register with Claude Code (or any MCP client)

In `.mcp.json`:

```json
{
  "mcpServers": {
    "beigebox-pensec": {
      "url": "http://localhost:1337/pen-mcp",
      "headers": { "Authorization": "Bearer YOUR_BEIGEBOX_KEY" }
    }
  }
}
```

The key needs `/pen-mcp` in its `allowed_endpoints`.

## Install the tool binaries

```bash
# Most are in Kali / Debian / Ubuntu repos:
sudo apt-get install -y \
  nmap masscan rustscan amass subfinder fierce dnsenum \
  gobuster feroxbuster dirsearch ffuf nikto sqlmap wpscan dalfox \
  wafw00f hydra john hashcat \
  enum4linux enum4linux-ng smbmap netexec \
  binwalk exiftool checksec \
  arjun paramspider seclists \
  testssl.sh sslscan ssh-audit \
  snmp onesixtyone nbtscan ldap-utils \
  impacket-scripts kerbrute \
  whatweb exploitdb theharvester cewl metasploit-framework
# ProjectDiscovery + go-tools (not all in apt):
go install github.com/projectdiscovery/httpx/cmd/httpx@latest
go install github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest
go install github.com/projectdiscovery/katana/cmd/katana@latest
go install github.com/projectdiscovery/naabu/v2/cmd/naabu@latest
go install github.com/projectdiscovery/dnsx/cmd/dnsx@latest
go install github.com/lc/gau/v2/cmd/gau@latest
go install github.com/tomnomnom/waybackurls@latest
go install github.com/hakluke/hakrawler@latest
nuclei -ut   # update templates
```

`netexec` (formerly `crackmapexec`) is also installable via `pipx install netexec`.

Wrappers gracefully report `binary 'X' not found on PATH` if a tool isn't
installed — the rest keep working.

## Tool list (53 wrappers)

### Network discovery / port scanning
| Tool | Binary | Notes |
|---|---|---|
| `nmap_scan` | `nmap` | Port + service + script profiles |
| `nmap_advanced_scan` | `nmap` | NSE-script-driven (vuln, exploit, …) |
| `masscan_scan` | `masscan` | High-rate sweep (needs root / CAP_NET_RAW) |
| `rustscan_scan` | `rustscan` | Fast scanner that pipes into nmap |
| `fierce_scan` | `fierce` | DNS recon / zone walk |
| `dnsenum_scan` | `dnsenum` | DNS brute / zone transfer |

### Subdomain / asset discovery
| Tool | Binary | Notes |
|---|---|---|
| `amass_scan` | `amass` | Passive + optional active subdomain enum |
| `subfinder_scan` | `subfinder` | Fast passive enum (ProjectDiscovery) |
| `httpx_probe` | `httpx` (PD) | HTTP probe + tech detect |
| `wafw00f_scan` | `wafw00f` | WAF fingerprinting |

### Web vuln / fuzz / crawl
| Tool | Binary | Notes |
|---|---|---|
| `nuclei_scan` | `nuclei` | Template-based vuln scanner |
| `katana_crawl` | `katana` | Headless JS-aware crawler |
| `ffuf_scan` | `ffuf` | Fuzzer — URL needs FUZZ marker |
| `gobuster_scan` | `gobuster` | Dir / DNS / vhost brute |
| `feroxbuster_scan` | `feroxbuster` | Recursive content discovery |
| `dirsearch_scan` | `dirsearch` | Python dir brute-forcer |
| `nikto_scan` | `nikto` | Classic web server scanner |
| `sqlmap_scan` | `sqlmap` | SQLi detection / exploitation |
| `dalfox_xss_scan` | `dalfox` | XSS scanner |
| `wpscan_scan` | `wpscan` | WordPress security scan (API token optional) |

### URL / parameter discovery (passive intel)
| Tool | Binary | Notes |
|---|---|---|
| `gau_discovery` | `gau` | URLs from Wayback / CommonCrawl / OTX / URLScan |
| `waybackurls_discovery` | `waybackurls` | Wayback URL dump |
| `arjun_parameter_discovery` | `arjun` | Hidden HTTP parameter discovery |
| `paramspider_mining` | `paramspider` | Mine parameters from archive URLs |
| `hakrawler_crawl` | `hakrawler` | Fast Go web crawler |

### SMB / AD / lateral
| Tool | Binary | Notes |
|---|---|---|
| `enum4linux_scan` | `enum4linux` | Legacy SMB / RPC enumeration |
| `enum4linux_ng_scan` | `enum4linux-ng` | Modern Python rewrite, JSON output |
| `smbmap_scan` | `smbmap` | SMB share + permission mapping |
| `netexec_scan` | `netexec`/`nxc` | crackmapexec successor — requires `authorization: true` |

### Credentials / cracking — **all require `authorization: true`**
| Tool | Binary | Notes |
|---|---|---|
| `hydra_attack` | `hydra` | Online password brute-force |
| `john_crack` | `john` | Offline hash cracking |
| `hashcat_crack` | `hashcat` | GPU-accelerated cracking (RTX 4070 here) |

### Binary / forensics
| Tool | Binary | Notes |
|---|---|---|
| `binwalk_analyze` | `binwalk` | Firmware / blob signature scan + extract |
| `exiftool_extract` | `exiftool` | Metadata extraction (any file) |
| `checksec_analyze` | `checksec` | ELF protections (NX, PIE, RELRO, canary) |

### SSL / TLS / SSH config audit
| Tool | Binary | Notes |
|---|---|---|
| `testssl_scan` | `testssl.sh` | Comprehensive TLS / cipher / cert audit |
| `sslscan_scan` | `sslscan` | Quick TLS cipher dump |
| `ssh_audit_scan` | `ssh-audit` | SSH cipher/kex/MAC + CVE check |

### SNMP / NetBIOS / LDAP enumeration
| Tool | Binary | Notes |
|---|---|---|
| `snmpwalk_scan` | `snmpwalk` | Walk SNMP tree (v1/v2c/v3) |
| `onesixtyone_scan` | `onesixtyone` | Fast SNMP community-string scanner |
| `nbtscan_scan` | `nbtscan` | NetBIOS name service scan |
| `ldapsearch_scan` | `ldapsearch` | LDAP query (anonymous or bound) |

### Active Directory / Kerberos — most require `authorization: true`
| Tool | Binary | Notes |
|---|---|---|
| `impacket_secretsdump` | `impacket-secretsdump` | SAM / LSA / DCSync dump |
| `impacket_getuserspns` | `impacket-GetUserSPNs` | Kerberoasting (TGS request) |
| `impacket_getnpusers` | `impacket-GetNPUsers` | AS-REP roasting (no preauth) |
| `kerbrute_userenum` | `kerbrute` | Kerberos username enum |

### OSINT / exploit lookup / payload generation
| Tool | Binary | Notes |
|---|---|---|
| `whatweb_scan` | `whatweb` | Web technology fingerprinting |
| `searchsploit_lookup` | `searchsploit` | Local Exploit-DB keyword/CVE search |
| `theharvester_scan` | `theHarvester` | OSINT email / subdomain / employee gathering |
| `cewl_wordlist_gen` | `cewl` | Spider a site to generate a custom wordlist |
| `msfvenom_generate` | `msfvenom` | One-shot Metasploit payload gen — requires `authorization: true` |

### ProjectDiscovery extras
| Tool | Binary | Notes |
|---|---|---|
| `naabu_scan` | `naabu` | Fast SYN port scanner |
| `dnsx_resolve` | `dnsx` | Bulk DNS resolver / probe |

## Invocation

Each tool takes a JSON object as `input`. Examples:

```json
// tools/call name=nmap_scan
{ "input": "{\"target\": \"scanme.nmap.org\", \"profile\": \"service\", \"ports\": \"1-1000\"}" }

// tools/call name=nuclei_scan
{ "input": "{\"target\": \"https://example.com\", \"severity\": \"critical,high\"}" }

// tools/call name=ffuf_scan
{ "input": "{\"url\": \"https://example.com/FUZZ\", \"wordlist\": \"/usr/share/wordlists/dirb/common.txt\"}" }
```

Result is JSON: `{ok, binary, argv, returncode, stdout, stderr, duration_s}`
plus per-tool extras (e.g. `findings`, `subdomains`, `results`) when output
parsing succeeds.

## Adding more wrappers

1. Create a new `SecurityTool` subclass in `tools/` (one file per category).
2. Use `safe_target()` / `safe_arg()` from `_base.py` for all user input.
3. Call `run_argv([...], timeout=...)` from `_run.py` — argv list, never shell.
4. Add the class to `ALL_TOOL_FACTORIES` in `tools/__init__.py`.

## Security notes

- Argv-list subprocess only (no shell). Free-form params are validated against
  shell-metacharacter blocklists in `_base.py:safe_target/safe_arg`.
- Per-tool timeouts (default 600s, overridable per call).
- `masscan` and `nmap -sS` need root or `CAP_NET_RAW` — run BeigeBox in a
  privileged container or grant per-binary caps.
- API-key-bearing tools (`wpscan`, optionally `nuclei`/`subfinder`) take the
  key as an input field; do not store keys in this module.
- **Destructive wrappers** (`hydra_attack`, `john_crack`, `hashcat_crack`,
  `netexec_scan`) require an explicit `"authorization": true` field in the
  input. The wrapper refuses to run otherwise. This is a deliberate friction
  layer to make sure the operator is consciously authorizing the activity
  rather than the LLM inferring intent from a vague prompt.
- This endpoint is **off by default**. Only enable on hosts authorized to
  run offensive tooling.
