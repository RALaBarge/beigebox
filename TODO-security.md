# Security Hardening TODO

## uvicorn — ANSI escape injection in access logs
All versions of uvicorn are vulnerable to ANSI escape sequence injection in the
request access logger. A malicious request path can embed terminal escape codes
that execute when logs are viewed in a terminal emulator.

**Fix:** Use a structured JSON logger (e.g. `python-json-logger`) for production,
or strip ANSI escapes in the log formatter. Run behind a reverse proxy
(nginx/caddy) that sanitizes request lines before they reach uvicorn.

## lxml — consider switching BS4 parser to html.parser
lxml has a history of XXE and XSS CVEs due to its C extension wrapping
libxml2/libxslt. BeigeBox uses it in `beigebox/tools/web_scraper.py` to parse
untrusted HTML fetched from the internet.

The BS4 `html.parser` backend (Python stdlib) has no C attack surface and no
XXE risk. It is slower but sufficient for scraping use cases. Trade-off:
- `lxml`: faster, handles malformed HTML better
- `html.parser`: smaller attack surface, no native deps, no XXE class of bugs

**Fix:** In `web_scraper.py`, change `BeautifulSoup(raw_html, "lxml")` to
`BeautifulSoup(raw_html, "html.parser")` and remove lxml from dependencies,
or keep lxml but pin to latest and monitor advisories.

## Secrets management — evaluate replacing agentauth
API keys are currently passed via `.env` files, with `agentauth` providing OS
keychain credential management. For multi-user or production deployments,
evaluate whether a dedicated secrets manager (HashiCorp Vault, Docker secrets,
SOPS, or cloud KMS) would be more appropriate. If so, agentauth can be retired
as a dependency.

**Evaluate:** Is agentauth sufficient for the single-operator use case, or does
the stack benefit from a standard secrets backend? Decision deferred until
multi-user requirements are clearer.
