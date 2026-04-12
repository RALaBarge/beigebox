# Security Policy

## Reporting a Vulnerability

**Do not** open a public issue for security vulnerabilities. Instead, email your report to security@ralabarge.dev with:

- **Title**: Brief description of the vulnerability
- **Affected version(s)**: Which BeigeBox versions are affected
- **Description**: Technical details and proof-of-concept (if possible)
- **Impact**: What an attacker could do if this vulnerability is exploited
- **Proposed fix** (optional): Any ideas on how to fix it

We will acknowledge receipt within 48 hours and provide an estimated timeline for a fix.

## Security Response Timeline

- **Acknowledgment**: Within 48 hours of report
- **Investigation & patch development**: 5-14 days depending on severity
- **Release**: We will issue a security release as soon as the fix is ready
- **Disclosure**: We will disclose the vulnerability details 30 days after the public fix is released

## Severity Levels

| Level | Description | Response Time |
|-------|-------------|----------------|
| **Critical** | Code execution, credential theft, bypass of routing/auth | 24-48 hours |
| **High** | Authentication bypass, prompt injection, information disclosure | 3-5 days |
| **Medium** | Rate limiting bypass, cache poisoning, DoS | 5-14 days |
| **Low** | Minor issues, cosmetic problems | Next release |

## Security Best Practices

### For BeigeBox Users

1. **Keep BeigeBox updated**: Security patches are released regularly. Run `git pull && pip install -e .` to get the latest version.

2. **Protect API keys**: Store in environment variables or secure secret management:
   ```bash
   # Good: Environment variable
   export BEIGEBOX_API_KEY=$(aws secretsmanager get-secret-value --secret-id beigebox-key --query SecretString --output text)
   
   # Bad: Hardcoded in config
   api_key: "sk-..."
   ```

3. **Use authentication**: Enable API key validation:
   ```yaml
   # config.yaml
   auth:
     enabled: true
     require_api_key: true
   ```

4. **Enable TLS for production**: Never expose BeigeBox on public internet without HTTPS:
   ```bash
   # In docker-compose.yml or via uvicorn
   uvicorn beigebox.main:app --ssl-certfile cert.pem --ssl-keyfile key.pem
   ```

5. **Restrict backend access**: Only connect to trusted backend providers:
   ```yaml
   backends:
     - name: ollama
       url: http://localhost:11434  # localhost only for local development
       allowed_models: ["qwen*"]
   ```

6. **Monitor logs**: Review Tap events for suspicious activity:
   ```bash
   beigebox tap | grep -i "error\|warning\|auth"
   ```

7. **Rotate secrets regularly**: Update API keys and database passwords:
   ```bash
   # Rotate API key
   python -c "from beigebox.auth import generate_api_key; print(generate_api_key())"
   ```

8. **Enable content filtering**: Use guardrails for untrusted input:
   ```yaml
   guardrails:
     enabled: true
     filter_harmful_content: true
   ```

### For Contributors

1. All commits should be signed (`git commit -S`)
2. All pull requests must pass security checks:
   - `pip-audit` for dependency vulnerabilities
   - `bandit` for code-level issues
   - `semgrep` for pattern-based security flaws
3. Changes to routing logic need careful review
4. New backends should validate all inputs and outputs
5. Authentication/authorization changes need security review
6. Proxy request handling must prevent request smuggling
7. Cache keys should never include sensitive data

## Known Limitations

### Routing & Backend Selection

- **Trust model**: BeigeBox assumes all configured backends are trustworthy. Don't add untrusted backends.
- **Model switching**: If backend A is compromised, use Z-commands to force a different backend. But assume data was already exfiltrated.
- **Latency-aware routing**: Timing information is used for routing decisions. Adversaries with network access can observe timing and optimize attacks.

### Caching

- **Cache poisoning**: If backend A returns poisoned data, it's cached and served to other users. Disable caching for untrusted backends:
  ```yaml
  backends:
    - name: untrusted
      cache_enabled: false
  ```
- **Session cache**: Session cache keys must be namespaced per user. Don't mix data across users.

### API Proxy

- **Request smuggling**: Ensure all HTTP parsing is strict. Report any request smuggling vulnerabilities.
- **Header injection**: Proxy headers carefully to prevent injection. Don't pass user headers to backend without validation.
- **Streaming**: Streaming responses are not re-validated. Assume streamed content is from the correct source.

### Multi-User Deployments

- **Isolation**: BeigeBox does not enforce user isolation. Deploy per-user instances or use a reverse proxy with authentication.
- **Shared database**: SQLite is not suitable for multi-user deployments. Use PostgreSQL in production.
- **Rate limiting**: Rate limits are per-IP, not per-user. In multi-user setups, use an API gateway.

## Deployment Security Checklist

- [ ] Use HTTPS/TLS in production
- [ ] Enable API key authentication
- [ ] Store secrets in a KMS (AWS Secrets Manager, HashiCorp Vault, etc.)
- [ ] Use a separate database per deployment (not shared SQLite)
- [ ] Enable logging and audit trails
- [ ] Monitor and alert on errors
- [ ] Keep dependencies updated (`pip-audit --fix`)
- [ ] Restrict network access (firewall, VPC, network policy)
- [ ] Use a reverse proxy for additional security (rate limiting, authentication, etc.)
- [ ] Regularly rotate secrets and API keys
- [ ] Test security configuration in staging before production

## Backend Security

When configuring backends:

```yaml
backends:
  - name: ollama
    url: http://localhost:11434              # Local only
    allowed_models: ["qwen*"]                # Whitelist models
    require_auth: true                       # If backend supports it
    timeout: 30                              # Prevent hanging requests
    
  - name: openrouter
    url: https://openrouter.io/api/v1
    auth: "$OPENROUTER_API_KEY"              # Environment variable
    allowed_models: ["gpt*", "claude*"]
    rate_limit: 100                          # Requests per minute
```

## Dependency Scanning

We use `pip-audit` to continuously check for vulnerable dependencies. Run it yourself:

```bash
pip-audit --desc
```

Update vulnerable dependencies immediately:

```bash
pip install --upgrade beigebox
```

## Security Releases

Security releases are issued as `X.Y.Z` (no `-alpha` or `-beta` tags) and announced on:

- GitHub Releases page
- Project README (pinned notice)
- Email to watchers (if subscribed)

## Responsible Disclosure

If you discover a security vulnerability in BeigeBox:

1. **Email security@ralabarge.dev** with:
   - Description of the issue
   - Steps to reproduce
   - Proof-of-concept (if applicable)
   - Potential impact

2. **Do not**:
   - Post details on public channels (GitHub, Reddit, HN, etc.)
   - Create public pull requests with the fix
   - Share details with other parties

3. **We will**:
   - Acknowledge receipt within 48 hours
   - Work on a fix in a private branch
   - Release a patched version
   - Credit you in the release notes (if desired)

## Contact

For security inquiries: security@ralabarge.dev

Please allow 48 hours for initial response.
