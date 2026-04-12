# OAuth2 SSO + API Key Auth Setup for BeigeBox

## Overview

BeigeBox now supports **OAuth2 SSO** (GitHub, Google) for web UI login + **API key generation** for programmatic access. Single-tenant deployments (each customer on their own VM) can now gate the web UI behind OAuth while still supporting API-based client access.

---

## Configuration

### Step 1: Enable OAuth in `config.yaml`

```yaml
auth:
  web_ui:
    mode: "oauth"  # Enable OAuth gating
    providers:
      - name: github
        client_id: "YOUR_GITHUB_CLIENT_ID"
        # client_secret via BB_GITHUB_CLIENT_SECRET env var or: agentauth add github
        # allowed_orgs: ["your-org"]  # optional: restrict to org members

      # Alternative/additional provider:
      # - name: google
      #   client_id: "YOUR_GOOGLE_CLIENT_ID"
      #   # client_secret via BB_GOOGLE_CLIENT_SECRET env var or: agentauth add google
      #   allowed_emails: ["user@example.com"]  # optional: whitelist emails
```

### Step 2: Register OAuth App

#### GitHub
1. Go to https://github.com/settings/developers
2. Click "New OAuth App"
3. Fill in:
   - **Application name:** "BeigeBox"
   - **Homepage URL:** `https://your-domain.com`
   - **Authorization callback URL:** `https://your-domain.com/auth/github/callback`
4. Copy **Client ID** → `client_id` in config
5. Generate **Client Secret** → store as `BB_GITHUB_CLIENT_SECRET` env var

#### Google (Alternative)
1. Go to https://console.cloud.google.com
2. Create new project → "BeigeBox"
3. APIs & Services → Create OAuth 2.0 credentials
4. Application type: Web application
5. Authorized redirect URIs: `https://your-domain.com/auth/google/callback`
6. Copy **Client ID** → `client_id` in config
7. Copy **Client Secret** → store as `BB_GOOGLE_CLIENT_SECRET` env var

### Step 3: Set Environment Variables (Docker / Production)

```bash
export BB_GITHUB_CLIENT_SECRET="ghp_..."
export BB_SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"

# Or use agentauth (for local dev):
agentauth add github      # stores in OS keychain
agentauth add bb-session  # stores session signing key
```

---

## Login Flow

### For Web UI (Browser)
1. User visits `https://your-domain.com`
2. Redirected to `/auth/github/login` (if OAuth enabled)
3. User clicks "Log in with GitHub"
4. GitHub OAuth flow → user authorized
5. BeigeBox receives code, exchanges for user info
6. User row created/updated in SQLite
7. Session cookie set (`bb_session`)
8. Redirected to `/ui` (web dashboard)

### For Programmatic Access (API Client)

**Option A: Generate API Key from Web UI**
1. User logs in via OAuth (above)
2. Visits settings → "API Keys"
3. Clicks "Create API Key"
4. Receives one-time key display (e.g., `bb_sk_...`)
5. Stores key in client app

**Option B: Use Static API Key (Backwards Compatible)**
```yaml
auth:
  api_key: "my-static-key-here"  # Legacy mode, still works
```

---

## API Endpoints

### OAuth Flow Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/auth/{provider}/login` | GET | Redirect to provider (GitHub/Google) |
| `/auth/{provider}/callback` | GET | OAuth callback (automatic) |
| `/auth/logout` | GET | Clear session, redirect to `/` |
| `/auth/me` | GET | Get current user info or `{authenticated: false}` |

### API Key Management Endpoints

| Endpoint | Method | Purpose | Auth Required |
|----------|--------|---------|----------------|
| `/api/v1/auth/keys` | POST | Create new API key | Session cookie |
| `/api/v1/auth/keys` | GET | List user's API keys (no plaintext) | Session cookie |
| `/api/v1/auth/keys/{key_id}` | DELETE | Revoke an API key | Session cookie |

### Using API Keys

For any OpenAI-compatible API call, provide the key via:

```bash
# Option 1: Bearer token
curl -H "Authorization: Bearer bb_sk_abc123..." http://localhost:8000/v1/chat/completions

# Option 2: api-key header (OpenAI style)
curl -H "api-key: bb_sk_abc123..." http://localhost:8000/v1/chat/completions

# Option 3: Query parameter (fallback)
curl http://localhost:8000/v1/chat/completions?api_key=bb_sk_abc123...
```

---

## Database Schema

### `users` table
```sql
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,     -- 'github', 'google'
    sub TEXT NOT NULL,          -- provider's user ID
    email TEXT NOT NULL,
    name TEXT NOT NULL,
    picture TEXT,
    created_at TEXT,
    last_seen TEXT,
    UNIQUE(provider, sub)
);
```

### `api_keys` table
```sql
CREATE TABLE api_keys (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,      -- FK to users.id
    key_hash TEXT NOT NULL UNIQUE,  -- SHA256 of actual key
    name TEXT,                   -- user-given name
    created_at TEXT,
    last_used TEXT,
    expires_at TEXT,
    active INTEGER DEFAULT 1,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
```

**Security note:** Actual API keys are never stored — only SHA256 hashes. The plaintext key is only shown once at creation time.

---

## Security Features

1. **PKCE flow** — OAuth code exchange is protected (code_challenge + code_verifier)
2. **CSRF protection** — State token validated in callback
3. **Session signing** — Cookies signed with `BB_SESSION_SECRET`, verified on each request
4. **Key hashing** — API keys hashed with SHA256 before storage
5. **Rate limiting** — Optional per-key rate limits in config (static keys only; dynamic keys: unlimited by default)
6. **Expiration** — API keys can have optional `expires_at` timestamp

---

## Example Deployment (Docker)

```dockerfile
FROM beigebox/beigebox:latest

ENV BB_GITHUB_CLIENT_SECRET="ghp_..."
ENV BB_SESSION_SECRET="..."

COPY config.yaml /beigebox/config.yaml
```

```bash
docker run -e BB_GITHUB_CLIENT_SECRET=... -e BB_SESSION_SECRET=... beigebox
```

---

## Testing Locally

```bash
# 1. Set dummy OAuth credentials
export BB_GITHUB_CLIENT_SECRET="test-secret"
export BB_SESSION_SECRET="test-session-key-32-bytes-long-"

# 2. Copy config.example.yaml and uncomment GitHub provider
cp config.example.yaml config.yaml
# Edit config.yaml: set auth.web_ui.mode = "oauth", auth.web_ui.providers[0].client_id = "test-id"

# 3. Start server
uvicorn beigebox.main:app --reload

# 4. Try OAuth flow
# Visit http://localhost:8000/auth/github/login (redirects to GitHub)
# Or test session endpoint: curl http://localhost:8000/auth/me

# 5. Generate API key (requires session)
curl -H "Cookie: bb_session=..." -X POST http://localhost:8000/api/v1/auth/keys
```

---

## Files Modified

1. **`beigebox/web_auth.py`** — Added `GitHubProvider` (Google was already there)
2. **`beigebox/storage/sqlite_store.py`** — Added `api_keys` table + key management methods
3. **`beigebox/main.py`** — Added API key endpoints + dynamic key verification in auth middleware
4. **`config.example.yaml`** — Documented OAuth/API key config options

---

## Next Steps

### Before Tuesday Launch

1. **Register OAuth apps** (GitHub + Google)
2. **Set `config.yaml`** with client IDs
3. **Set environment variables** (client secrets, session key)
4. **Test OAuth flow** locally
5. **Deploy to VM** with config

### Post-Launch

- Monitor API key creation in dashboard
- Collect feedback on UX (key generation, rotation, revocation)
- Add key rotation policy (30-day expiry)
- Add scope-based API key permissions (read-only, write, admin)

---

## Backwards Compatibility

✅ **Static API keys still work.** Existing clients using `auth.api_key` in config or `api-key` headers continue to work without OAuth.

✅ **Can mix modes.** OAuth for web UI + static keys for service accounts.

✅ **Gradual migration.** Deploy OAuth alongside existing key auth, migrate users over time.

---

## Troubleshooting

### "OAuth not configured"
- Check `auth.web_ui.mode: "oauth"` is set (not "none")
- Verify at least one provider is in `auth.web_ui.providers`

### "Invalid OAuth state"
- Session cookie was lost (browser privacy mode, cross-site cookie issue)
- Check `secure=True` in cookie settings if using HTTPS
- Try incognito window

### "Unknown provider: github"
- Client secret not found (BB_GITHUB_CLIENT_SECRET env var not set)
- Check logs: `Auth: GitHub provider missing client_id or client_secret — skipping`

### "API key hash not found"
- Key was revoked (revoked keys have `active=0`)
- Key expired (check `expires_at` timestamp)
- Try creating a new key

---

## Questions?

See `beigebox/web_auth.py` for provider protocol (can extend to Okta, Keycloak, etc.)
See `beigebox/auth.py` for API key validation logic.
