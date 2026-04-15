# BeigeBox Password Authentication Setup

## Overview

BeigeBox includes built-in password authentication for single-tenant deployments. The system uses:
- **Default user:** `admin`
- **Default password:** `changeme` (customizable via `BB_INITIAL_PASSWORD` env var)
- **Session tokens:** Signed with HMAC (4-hour TTL)
- **Password hashing:** bcrypt with 12 rounds
- **Cookie security:** httponly, samesite=strict, secure (HTTPS only)

## Quick Start

### 1. Enable Password Auth in Config

Edit `config.yaml`:
```yaml
auth:
  mode: "password"
  dynamic_key_rate_limit_rpm: 100

# Optional: set in environment instead
# export BB_INITIAL_PASSWORD="your-secure-password"
```

### 2. Start the Server

```bash
# Development
uvicorn beigebox.main:app --reload

# Production (Docker)
docker compose up -d beigebox
```

### 3. Login

Navigate to `http://localhost:8000/auth/login` (or your configured port).

**Default credentials:**
- Username: `admin` (fixed)
- Password: `changeme` (or your `BB_INITIAL_PASSWORD`)

### 4. Change Password

After first login, you'll be prompted to change the default password.

```bash
# Or use the API
curl -X POST http://localhost:8000/auth/change-password \
  -H "Content-Type: application/json" \
  -d '{
    "old_password": "changeme",
    "new_password": "your-new-secure-password"
  }' \
  -b "bb_session=<your-session-token>"
```

---

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BB_INITIAL_PASSWORD` | `changeme` | Default password for initial login |
| `BB_SESSION_TIMEOUT` | `14400` | Session TTL in seconds (4 hours) |
| `BB_PASSWORD_MIN_LENGTH` | `8` | Minimum password length |

### Config File Options

```yaml
auth:
  mode: "password"              # Enable password auth
  dynamic_key_rate_limit_rpm: 100  # API key rate limit

# Optional: password policy
password_policy:
  min_length: 8
  require_uppercase: true
  require_digits: true
  require_special_chars: false
```

---

## API Reference

### GET /auth/login

Serves the login form HTML page.

**Response:** `text/html` with login form

### POST /auth/login

Submit credentials for authentication.

**Request:**
```json
{
  "username": "admin",
  "password": "changeme"
}
```

**Response (Success):**
```json
{
  "authenticated": true,
  "username": "admin",
  "password_needs_change": false
}
```

Sets cookie: `bb_session` (httponly, samesite=strict)

**Response (Failure):**
```json
{
  "error": "Invalid credentials"
}
```

Status: `401 Unauthorized`

### POST /auth/change-password

Change the password after first login.

**Headers:**
- `Content-Type: application/json`
- Requires valid session cookie

**Request:**
```json
{
  "old_password": "changeme",
  "new_password": "new-secure-password"
}
```

**Response (Success):**
```json
{
  "changed": true
}
```

**Response (Failure):**
```json
{
  "error": "Old password incorrect"
}
```

Status: `401 Unauthorized`

### GET /auth/logout

Clear session and redirect to login page.

**Response:** Redirect to `/` (302)
**Cookie:** Deletes `bb_session`

### GET /auth/me

Check authentication status.

**Response (Authenticated):**
```json
{
  "authenticated": true,
  "username": "admin"
}
```

**Response (Not Authenticated):**
```json
{
  "authenticated": false
}
```

---

## Security Considerations

### Password Storage

- Passwords are hashed with bcrypt (12 rounds, ~100ms computation)
- Hashes stored in SQLite database at `data/beigebox.db`
- Never stored in plaintext, logs, or config files

### Session Management

- Sessions are signed with HMAC (not encrypted)
- Token validation happens server-side
- 4-hour TTL (customizable via env var)
- Browser cookies are httponly (JS cannot access)

### HTTPS Enforcement

For production deployment:
1. Enable HTTPS/TLS (set `request.url.scheme == "https"`)
2. Cookies will automatically use `secure` flag
3. Use environment variable for initial password (not hardcoded)

### Rate Limiting

- API endpoints have rate limiting (100 req/min by default)
- Failed login attempts are not explicitly rate-limited (add external WAF if needed)
- Session timeout prevents token reuse

---

## Troubleshooting

### "Login page is downloaded instead of rendered"

**Cause:** Middleware redirect to GET /auth/login but endpoint doesn't exist  
**Fix:** Ensure `beigebox/main.py` has the GET /auth/login endpoint (line ~1295)

### "Invalid credentials" after setting BB_INITIAL_PASSWORD

**Cause:** Password mismatch between env var and login attempt  
**Fix:** Verify env var is set and matches exactly (case-sensitive)

### Session cookie not being set

**Cause:** Running on HTTP (insecure)  
**Fix:** For development, the secure flag is not enforced. For production, use HTTPS

### "Session expired" after 4 hours

**Cause:** Session TTL (time-to-live) reached  
**Fix:** Re-login or increase `BB_SESSION_TIMEOUT` env var

---

## Multi-Tenant Deployments

Password auth is designed for **single-tenant** SaaS (one admin user).

For multi-tenant or OAuth deployments:
1. Use `auth.mode: "oauth"` instead
2. Configure OAuth provider (GitHub, Google, etc.)
3. See [OAUTH_SETUP.md](./OAUTH_SETUP.md) for details

---

## Testing

```bash
# Test login with default password
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "changeme"}'

# Extract session token
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "changeme"}' \
  -c - | grep bb_session | awk '{print $NF}')

# Verify authentication
curl -b "bb_session=$TOKEN" http://localhost:8000/auth/me
```

---

## See Also

- [SECURITY_POLICY.md](./SECURITY_POLICY.md) — Threat model, detection accuracy
- [DEPLOYMENT_SECURITY_CHECKLIST.md](./DEPLOYMENT_SECURITY_CHECKLIST.md) — Pre-production validation
- [SECURITY_TOOLS_README.md](../beigebox/security/SECURITY_TOOLS_README.md) — Hardening layers (RAG, injection, anomaly)
