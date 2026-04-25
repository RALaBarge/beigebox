# BeigeBox Pen/Sec MCP

A separate MCP endpoint (`POST /pen-mcp`) exposing wrapped offensive-security
tools — nmap, nuclei, sqlmap, ffuf, gobuster, amass, subfinder, httpx,
nikto, wpscan, masscan, wafw00f, dnsenum to start. Uses the same JSON-RPC
McpServer implementation as `/mcp` but with its own registry so security
tooling stays out of the default tool surface.

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
sudo apt-get install -y \
  nmap masscan amass subfinder dnsenum \
  gobuster feroxbuster ffuf nikto sqlmap wpscan \
  wafw00f hydra john hashcat
# ProjectDiscovery Go tools (not all in apt):
go install github.com/projectdiscovery/httpx/cmd/httpx@latest
go install github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest
nuclei -ut   # update templates
```

Wrappers gracefully report `binary 'X' not found on PATH` if a tool isn't
installed — the rest keep working.

## Tool list (v1)

| Tool                | Binary       | Category            |
|---------------------|--------------|---------------------|
| `nmap_scan`         | `nmap`       | Port / service scan |
| `masscan_scan`      | `masscan`    | High-rate port sweep (needs root) |
| `dnsenum_scan`      | `dnsenum`    | DNS enumeration     |
| `amass_scan`        | `amass`      | Subdomain enum (passive/active) |
| `subfinder_scan`    | `subfinder`  | Fast passive subdomain enum |
| `httpx_probe`       | `httpx` (PD) | HTTP probing + tech detect |
| `wafw00f_scan`      | `wafw00f`    | WAF fingerprinting  |
| `nuclei_scan`       | `nuclei`     | Template-based vuln scanner |
| `ffuf_scan`         | `ffuf`       | Web fuzzer (needs FUZZ marker) |
| `gobuster_scan`     | `gobuster`   | Dir / DNS / vhost brute |
| `nikto_scan`        | `nikto`      | Classic web server scanner |
| `sqlmap_scan`       | `sqlmap`     | SQLi detection / exploit |
| `wpscan_scan`       | `wpscan`     | WordPress security scan |

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
- This endpoint is **off by default**. Only enable on hosts authorized to
  run offensive tooling.
