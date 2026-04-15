# BeigeBox Auth & Security Issues

## Current Issues

### 1. ⚠️ Login Page Download Bug (CRITICAL)
**Status:** Active  
**Port:** localhost:1337  
**Symptom:** Login page is being downloaded as a file instead of displayed in browser

**Root Cause:**
- WebAuthMiddleware redirects unauthenticated users to `GET /auth/login` (line 747)
- Only a `POST /auth/login` endpoint exists (line 1294)
- No GET endpoint to serve the login page HTML
- Browser follows 302 redirect to POST-only endpoint, causing download instead of render

**Solution:**
- Add `GET /auth/login` endpoint that serves login HTML form
- Keep `POST /auth/login` for form submission
- Ensure login form matches Beige UI style

**Files to Modify:**
- `beigebox/main.py` — add GET endpoint + login form HTML

---

## Next Steps (After Fix)

### 2. Test Auth Flow
- [ ] Test login page renders at localhost:1337
- [ ] Test password submission
- [ ] Test session cookie set correctly
- [ ] Test logout clears cookie

### 3. Hardening Tasks (From Apr 12 Session)
- [ ] P1-A: RAG Poisoning Scanner (guards ChromaDB)
- [ ] P1-B: MCP parameter validator (tool call injection)
- [ ] P1-C: API anomaly detector (token extraction)
- [ ] P1-D: Agent memory validator

### 4. Release Prep
- [ ] Version bump (v0.2.0-security)
- [ ] Update CHANGELOG
- [ ] PyPI package build & upload

---

## Context

This branch is preparing beigebox for a separate "beigebox-security" repository. Password auth and hardening tools are being added to make the system sellable as a secure LLM middleware.

**Related commits:**
- aab8d8af: gate web UI behind password auth when enabled
- 94ad0abd: implement proper password authentication for SimplePasswordAuth
- 26e835de: add itsdangerous and bcrypt to requirements for password auth
