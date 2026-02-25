# BeigeBox — Changelog

---

### v0.10.1 — Ensemble to Harness
- Ensemble Voting moved from Chat tab modal to Harness tab third mode (Manual / Orchestrated / **Ensemble**)
- Ensemble mode: inline model checklist, judge selector, results grid with winner highlight — no modal
- `🎯 Ensemble` button removed from Chat toolbar
- Dashboard Subsystems section shows ghost/faded example rows while proxy is starting up so new users understand what the panel is for
- `cli.py` version bumped to match `pyproject.toml` (was stale at 0.9.0)
- Config tab deep-link tooltip corrected from "tab (8)" to "tab (7)"
- `todo.md` rewritten: clean sections for Active, Known Issues, Nice To Have, Closed
- README: Docker Quickstart simplified to three lines; port table clarified (1337→8000); Testing section notes to run from repo root; Changelog extracted to `2600/CHANGELOG.md`

---

### v0.10.0 — Observability Consolidation
- **Killed Semantic Map** — removed module, endpoint, tests, config, web UI tab. Conversation Replay covers the use case.
- **Killed Flight Recorder** — merged per-stage timing into Wire Tap. Every request now emits a timing summary entry with `latency_ms` and per-stage `timing` breakdown directly in the wiretap JSONL.
- Wire Tap entries with timing data show expandable breakdown bars (click "▸ timing breakdown")
- Tab count reduced from 8 to 7: Dashboard, Chat, Conversations, Tap, Operator, Harness, Config
- All dead `if recorder:` guards removed from proxy request pipeline (~60 lines)
- `system_context:` and `generation:` sections added to config.yaml and config.docker.yaml
- Placeholder `system_context.md` with usage documentation added to project root
- README updated: new Customization section, API table, file tree, tab numbers
- 214 tests passing, smoke.sh covers system context and generation param endpoints

---

### v0.9.9 — Release Candidate
- Conversation export to JSONL / Alpaca / ShareGPT via `GET /api/v1/export` and web UI button
- TTS auto-play on assistant responses (Web Audio API, configurable voice/model/speed)
- STT and TTS routing to separate configurable service URLs (`stt_url`, `tts_url`)
- Whisper + Kokoro-FastAPI added to `docker-compose.yaml` as optional services
- TTS fires in Operator tab (model path and operator path)
- System context injection (`system_context.md`, hot-reloadable, HTTP read/write API)
- Full generation parameter exposure (`gen_temperature`, `gen_top_p`, etc. with force mode)
- Dedicated Voice / Audio section in Config tab with test button
- Harness `_parse_json` hardened: trailing commas, truncation recovery, embedded object extraction
- Harness `_run_operator` fixed to use `127.0.0.1` (Docker loopback safe)
- `smoke.sh` expanded to cover all features (17 test sections)
- Test suite expanded: `test_system_context.py`, `test_proxy_injection.py`, `test_harness.py`, export tests

---

### v0.9.2
- Multi-model ensemble voting with judge LLM
- Mobile-responsive web UI (tablet, mobile, small phone, landscape)
- Generic OpenAI-compatible backend (llama.cpp, vLLM, TGI, Aphrodite, LocalAI)
- Backend retry with exponential backoff

---

### v0.9.0
- Harness tab: Manual mode (parallel model runner) + Orchestrated mode (goal-directed master)
- Voice push-to-talk: mic button, click/hold/hotkey, STT forwarding
- Operator shell security hardening (allowlist, pattern blocking, audit logging, busybox wrapper)
- Full config exposure in web UI with live apply
- Vi mode, palette themes, conversation forking

---

### v0.8.0
- Conversation replay with full routing context
- Wire tap with persistent filters and live mode
- Prompt injection detection (flag and block modes)
- Multi-pane chat with fan-out and per-pane targets
- Auto-summarization for context window management
