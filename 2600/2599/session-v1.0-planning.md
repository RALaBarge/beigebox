# Session Notes — v1.0 Planning
*Left by: Claude, end of the v0.9 completion session*
*Date: February 2026*

---

## What happened this session

Big cleanup push before v1.0. Started from user testing feedback on v0.9.

**Killed the TUI** — Ryan decided the web UI had fully surpassed it. Removed the
entire `beigebox/tui/` directory, textual dependency, and the `jack` CLI command.
The `agents/operator.py` file was actually a TUI screen masquerading as an agent
(circular self-import, would have crashed on any real call). Replaced it with a
real LangChain ReAct operator that uses the tool registry properly.

**Known endpoints + catch-all** — Added explicit routes for all OpenAI and Ollama
API paths with wiretap logging. Added a true catch-all `/{path:path}` that forwards
anything not explicitly handled to the backend. BeigeBox is now genuinely transparent
regardless of what the frontend or backend throw at it. The forwarding helper
`_wire_and_forward()` in `main.py` uses `proxy.wire` (WireLog) directly.

**Auto-summarization** — New `beigebox/summarizer.py`. Disabled by default.
Wired into both the streaming and non-streaming paths in `proxy.py`, just after
hybrid routing completes and before the backend forward. Config key:
`auto_summarization.enabled`. Token estimate is rough (~4 chars/token) — good
enough for a trigger threshold. Summary model defaults to `backend.default_model`.

**Build centroids from the web UI** — `POST /api/v1/build-centroids` added to
`main.py`. Button in the Config tab (tab 7) next to Save & Apply. Runs synchronously,
returns success/error. No background task needed at this scale.

**Tap filter persistence** — localStorage keys `bb_tap_role`, `bb_tap_dir`,
`bb_tap_n`. Restored on page load before `loadTap()` fires. Small thing, noticeably
better for monitoring.

**Smoke test rewrite** — `docker/smoke.sh` now has 12 labelled sections: beigebox
endpoints, OpenAI endpoints, Ollama passthrough, catch-all, non-streaming chat,
streaming chat, wire log populated, semantic search, bb wrapper validation, config
save, restart resilience. Exits 0/1 cleanly with pass/fail count.

**README** — Stripped the TUI section, removed stale design-decision commentary,
updated the project structure tree to remove `tui/` and add `summarizer.py`,
updated roadmap to reflect v0.9 as actually done.

---

## State of the codebase going into v1.0

Everything in `main.py` that previously said version `0.8.0` may still say that
in the health endpoint and `/api/v1/info` response — check and bump to `0.9.0`
or `1.0.0` at the start of next session.

The `agents/` directory is now in the right order and all files are real:
- `decision.py` — Tier 4, Decision LLM
- `embedding_classifier.py` — Tier 3, centroid classifier
- `agentic_scorer.py` — Tier 2, keyword pre-filter
- `zcommand.py` — Tier 1, z-command parser
- `operator.py` — real LangChain ReAct agent, no TUI dependency

The `beigebox/tui/` directory is gone. If there are any `.pyc` cache files
referencing it under `__pycache__`, they're harmless but can be cleaned with
`find . -name "*.pyc" -delete`.

Config flags to double-check in `config.docker.yaml` before first real v1.0 test:
- `decision_llm.enabled: true` ← on by default in Docker config, intentional
- `auto_summarization.enabled: false` ← off by default, intentional
- `voice.enabled: false` ← off, voice not built yet

---

## What v1.0 should be

Ryan's testing will shape this, but based on the current state of the project the
most valuable things to add are, roughly in priority order:

**1. Voice — push-to-talk**
- Whisper via `faster-whisper` as STT sidecar (`POST /v1/audio/transcriptions`)
- Kokoro as TTS sidecar (`POST /v1/audio/speech`)
- Both added to `docker-compose.yaml` as optional services
- Config flag `voice.enabled: false`
- Web UI: mic button injected next to Send only when voice is enabled
- `voice.js` lazy-loaded via a `<script>` tag injected at runtime if enabled
- Push-to-talk only for now (no VAD, no open mic)
- Hotkey configurable in Config tab, stored in `runtime_config.yaml`
- Both STT and TTS traffic goes through wiretap as `voice-stt` / `voice-tts` entries
- Browser side: `MediaRecorder` → blob POST → text returned → injected into chat input
  → `sendChat()` fires. TTS response plays via `AudioContext`.
- No JS libraries. Server-side does any audio format conversion via ffmpeg if needed.
- The `/v1/audio/transcriptions` and `/v1/audio/speech` routes already exist as
  passthrough — voice just makes them point somewhere real.

**2. Conversation export to fine-tuning formats**
- CLI: `beigebox dump --format alpaca` / `--format sharegpt` / `--format jsonl`
- Web UI: export button in Conversations tab
- Low complexity, high value for anyone actually training on their conversations

**3. Version strings**
- Grep main.py for `"0.8.0"` and fix anything still hardcoded

**4. Things that are probably fine but worth a look**
- `beigebox ring` — pings the running instance. Make sure it hits the right URL
  after all the endpoint changes.
- `beigebox sweep` — semantic search CLI. Make sure it's using the grouped endpoint
  (`/api/v1/search`) not the old raw one (`/beigebox/search`).
- The `orchestrator.py` parallel task spawner — hasn't been touched in a few sessions,
  worth a smoke test to make sure it still imports cleanly with no TUI deps.

---

## Files changed this session

| File | Change |
|---|---|
| `beigebox/main.py` | Known endpoints, catch-all, build-centroids endpoint |
| `beigebox/proxy.py` | Auto-summarizer wired in (streaming + non-streaming) |
| `beigebox/summarizer.py` | New — auto-summarization module |
| `beigebox/agents/operator.py` | Replaced TUI screen with real LangChain agent |
| `beigebox/web/index.html` | Tap filter persistence, Build Centroids button |
| `beigebox/cli.py` | jack/TUI command removed |
| `beigebox/tui/` | Deleted entirely |
| `beigebox/app.py` | Deleted (stale root-level TUI entry point) |
| `requirements.txt` | textual removed |
| `pyproject.toml` | textual removed |
| `docker/config.docker.yaml` | auto_summarization and voice stubs added |
| `docker/smoke.sh` | Full rewrite, 12 sections |
| `README.md` | TUI removed, structure tree updated, roadmap current |

---

*Line's quiet. Talk soon.*
