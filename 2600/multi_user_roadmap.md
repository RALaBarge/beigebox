# Multi-User OAuth + Isolation Roadmap

**Date:** 2026-03-28
**Status:** Feature branch `feature/web-auth` — OAuth shim complete, isolation work planned
**Scope:** Enable shared BeigeBox instances with user isolation

---

## Current State (v1.0)

### What's Implemented
- **OAuth2 provider protocol** — pluggable, ships Google (PKCE S256, email_verified check)
- **Session management** — itsdangerous signed cookies, 7-day expiry
- **Users table** — SQLite persistence (id, provider, sub, email, name, picture, created_at, last_seen)
- **Web UI gate** — /ui, /web/* paths require valid session cookie when mode=oauth
- **API unchanged** — Bearer token auth independent, unaffected by OAuth
- **Security** — constant-time state comparison, secure/httponly/samesite cookies

### What's Missing
- **Request isolation** — user_id not threaded through proxy; conversations lack user_id tags
- **Conversation privacy** — no filtering; users see all conversations
- **Message privacy** — same; users can read any message
- **Per-user rate limits** — rate limiting doesn't account for session users
- **Operator/harness tagging** — runs not associated with users
- **Server-side session revocation** — logout only deletes cookie; token valid until max_age
- **Admin interface** — no way to manage users, revoke sessions, see audit trail
- **Vector store isolation** — semantic cache/search not filtered by user

---

## MVP: Must-Have (High Impact, 1-2 hours)

### 1. Conversation Filtering by User (15 min)
**Endpoint:** `GET /api/v1/conversations`

Current behavior: returns all conversations, unfiltered.

```python
# beigebox/main.py, conversation list endpoint
def get_recent_conversations(limit: int = 20):
    user = getattr(request.state, 'web_user', None)
    if user:
        # Filter by user_id
        return sqlite_store.get_recent_conversations(limit=limit, user_id=user['user_id'])
    else:
        # API client (Bearer token) → all conversations
        return sqlite_store.get_recent_conversations(limit=limit)
```

**SQLite change:**
```python
def get_recent_conversations(self, limit: int = 20, user_id: str | None = None) -> list[dict]:
    where = " WHERE user_id = ? " if user_id else ""
    rows = conn.execute(
        f"SELECT c.id, c.created_at, ... FROM conversations c {where} ... LIMIT ?",
        (user_id, limit) if user_id else (limit,),
    ).fetchall()
```

### 2. Thread user_id Through Proxy (30 min)
**Goal:** Tag every conversation with its creator's user_id.

**In proxy.py, chat_completions():**
```python
web_user = getattr(request.state, 'web_user', None)
user_id = web_user['user_id'] if web_user else None

# When creating conversation:
sqlite_store.ensure_conversation(
    conversation_id=conv_id,
    created_at=now_iso,
    user_id=user_id  # NEW
)
```

**In proxy.py, store_message():**
- No change needed; messages inherit user_id via conversation FK

### 3. User Role + Admin Page (45 min)
**Add to users table:**
```sql
ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN disabled INTEGER DEFAULT 0;
```

**Admin endpoints:**
```python
@app.get("/api/v1/admin/users")
async def admin_list_users(request: Request):
    user = getattr(request.state, 'web_user', None)
    if not user or not sqlite_store.get_user(user['user_id']).get('is_admin'):
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    users = sqlite_store.list_all_users()
    return JSONResponse(users)

@app.post("/api/v1/admin/users/{user_id}/disable")
async def admin_disable_user(user_id: str, request: Request):
    # check is_admin, set disabled=1, invalidate all sessions for that user
    ...
```

**CLI admin tool:**
```bash
beigebox admin make-admin <email>    # promote user to admin
beigebox admin disable <email>       # disable user login
beigebox admin list-users            # list all users
```

---

## Should-Have (Medium Impact, 1-2 hours)

### 4. Server-Side Session Revocation
**Problem:** Logout only deletes the cookie. Token is valid in DB until expiry.
**Solution:** Store valid session tokens (hashed) in DB, check on each request.

**New table:**
```sql
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT DEFAULT NULL,
    ip_address TEXT,
    user_agent TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
```

**On login (oauth_callback):**
```python
import hashlib
session_token = st.web_auth.sign_session(...)
token_hash = hashlib.sha256(session_token.encode()).hexdigest()
st.sqlite_store.create_session(
    user_id=user_id,
    token_hash=token_hash,
    expires_at=now + 7 days,
    ip_address=request.client.host,
    user_agent=request.headers.get('user-agent'),
)
```

**On each request (WebAuthMiddleware):**
```python
token = request.cookies.get(COOKIE_SESSION, "")
user = st.web_auth.verify_session(token) if token else None
if user and st.sqlite_store:
    # Check token is not revoked
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    session = st.sqlite_store.get_session_by_hash(token_hash)
    if not session or session['revoked_at']:
        user = None  # Force re-login
```

**On logout:**
```python
token = request.cookies.get(COOKIE_SESSION, "")
token_hash = hashlib.sha256(token.encode()).hexdigest()
st.sqlite_store.revoke_session_by_hash(token_hash)
```

**Admin revoke all user's sessions:**
```python
st.sqlite_store.revoke_all_user_sessions(user_id)
```

### 5. Operator/Harness Tagging
**Add columns:**
```sql
ALTER TABLE operator_runs ADD COLUMN user_id TEXT DEFAULT NULL;
ALTER TABLE harness_runs ADD COLUMN user_id TEXT DEFAULT NULL;
```

**In operator/harness endpoints:**
```python
user_id = getattr(request.state, 'web_user', {}).get('user_id')
operator_run = st.sqlite_store.store_operator_run(..., user_id=user_id)
```

**Filtering on list:**
```python
runs = st.sqlite_store.get_operator_runs(user_id=user_id if web_user else None)
```

### 6. Per-User Rate Limiting
**Extend MultiKeyAuthRegistry to track session users:**
```python
# In ApiKeyMiddleware or new WebRateLimitMiddleware
auth_key = getattr(request.state, 'auth_key', None)
web_user = getattr(request.state, 'web_user', None)

# Rate limit key combines auth source + identity
if auth_key:
    rate_key = f"api_key:{auth_key.name}"
elif web_user:
    rate_key = f"session:{web_user['user_id']}"
else:
    rate_key = f"anon:{request.client.host}"

if not rate_limiter.check(rate_key, limit_rpm):
    return 429
```

---

## Nice-to-Have (Low Priority)

### 7. Audit Logging
Track all sensitive actions: login, logout, conversation create, operator run, admin actions.

```sql
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    action TEXT,
    resource_type TEXT,
    resource_id TEXT,
    ip_address TEXT,
    ts TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    metadata TEXT
);
```

### 8. Vector Store Isolation
Depends on use case: are documents shared or per-user?

**Shared:** No change (current state).
**Per-user:** Tag embeddings with user_id, filter searches.
```python
def search_documents(query, user_id=None):
    results = vector_store.search(query, limit=10)
    if user_id:
        results = [r for r in results if r.get('user_id') == user_id]
    return results
```

---

## Implementation Order

**Phase 1 (Critical):** Must-have features (1-2 hours)
1. Conversation filtering + thread user_id through proxy
2. User role + admin page
3. Test end-to-end

**Phase 2 (Recommended):** Should-have (1-2 hours)
4. Server-side session revocation
5. Operator/harness tagging
6. Per-user rate limiting

**Phase 3 (Polish):** Nice-to-have (ongoing)
7. Audit logging
8. Vector store isolation (if applicable)

---

## Security Checklist

- [x] CSRF protection (state parameter, constant-time comparison)
- [x] Email verification (email_verified from OAuth provider)
- [x] PKCE S256 (authorization code protection)
- [x] Secure cookies (httponly, samesite, secure on HTTPS)
- [x] Session signing (itsdangerous, max_age)
- [ ] Server-side session validation (Phase 2)
- [ ] Admin authentication (need to verify is_admin on admin endpoints)
- [ ] Audit logging (Phase 3)
- [ ] Rate limiting per user (Phase 2)

---

## Config Example

```yaml
auth:
  web_ui:
    mode: oauth
    providers:
      - name: google
        client_id: "YOUR_CLIENT_ID.apps.googleusercontent.com"
        allowed_emails: []   # [] = any Google account; ["@company.com"] = domain allowlist
```

---

## Future: Adding More Providers

The `OAuthProvider` protocol makes this trivial:

```python
# beigebox/web_auth.py
class GitHubProvider:
    name = "github"
    _AUTH_URL = "https://github.com/login/oauth/authorize"
    _TOKEN_URL = "https://github.com/login/oauth/access_token"
    _USERINFO_URL = "https://api.github.com/user"

    def get_authorization_url(self, redirect_uri, state):
        code_verifier = secrets.token_urlsafe(64)
        # ... PKCE S256, GitHub scopes: user:email
        return auth_url, code_verifier

    async def exchange_code(self, code, redirect_uri, code_verifier=""):
        # ... exchange code for token, fetch user info
        return OAuthUserInfo(...)
```

Config:
```yaml
providers:
  - name: google
    client_id: "..."
  - name: github
    client_id: "..."
```

---

## Notes

- **No breaking changes** — API key auth is unaffected; mode=none keeps OAuth fully disabled
- **Backwards compatible** — existing conversations/messages have user_id=NULL; filtering treats as "all users"
- **Minimal DB schema** — only 2 new columns (user_id on conversations, is_admin/disabled on users)
- **Session secret management** — agentauth or BB_SESSION_SECRET env var, never in config
