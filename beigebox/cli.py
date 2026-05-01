#!/usr/bin/env python3
"""
BeigeBox CLI — Tap the line. Control the carrier.

Named after the beige box — the phone phreaker device used to tap
a phone line and intercept both sides of a conversation.

Every command has a phreaker name and a standard alias:

    PHREAKER        STANDARD        WHAT IT DOES
    --------        --------        ----------------------------------
    dial            start, serve    Start the BeigeBox proxy server
    setup           install, pull   Pull required models into Ollama
    tap             log, tail       Live wiretap — watch the wire
    ring            status, ping    Ping a running instance
    sweep           search          Semantic search over conversations
    dump            export          Export conversations to JSON
    flash           info, config    Show stats and config at a glance
    tone            banner          Print the BeigeBox banner
"""

import argparse
import sys

from beigebox import __version__

BANNER = r"""
    ╔══════════════════════════════════════════════════╗
    ║                                                  ║
    ║   ██████  ███████ ██  ██████  ███████            ║
    ║   ██   ██ ██      ██ ██       ██                 ║
    ║   ██████  █████   ██ ██   ███ █████              ║
    ║   ██   ██ ██      ██ ██    ██ ██                 ║
    ║   ██████  ███████ ██  ██████  ███████            ║
    ║                                                  ║
    ║   ██████   ██████  ██   ██                       ║
    ║   ██   ██ ██    ██  ██ ██                        ║
    ║   ██████  ██    ██   ███                         ║
    ║   ██   ██ ██    ██  ██ ██                        ║
    ║   ██████   ██████  ██   ██                       ║
    ║                                                  ║
    ║   Tap the line. Control the carrier.   v""" + __version__ + r"""  ║
    ║                                                  ║
    ╚══════════════════════════════════════════════════╝
"""


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_dial(args):
    """Start the BeigeBox proxy server."""
    import uvicorn
    from beigebox.config import get_config

    cfg = get_config()
    host = args.host or cfg["server"]["host"]
    port = args.port or cfg["server"]["port"]

    print(BANNER)
    print(f"  Dialing up on {host}:{port}")
    # Get first backend URL from backends list
    backends = cfg.get('backends', [])
    backend_url = backends[0].get('url', 'N/A') if backends else 'N/A'
    default_model = cfg.get('models', {}).get('default', 'N/A')
    print(f"  Backend: {backend_url}")
    print(f"  Model: {default_model}")
    print()

    uvicorn.run(
        "beigebox.main:app",
        host=host,
        port=port,
        reload=args.reload,
        log_level="info",
    )


def cmd_tap(args):
    """Live wiretap — watch conversations on the wire."""
    from beigebox.wiretap import live_tap
    live_tap(
        log_path=args.log,
        follow=not args.no_follow,
        last_n=args.last,
        role_filter=args.role,
        raw=args.raw,
    )

def cmd_setup(args):
    """Pull required models into Ollama."""
    import httpx
    from beigebox.config import get_config

    cfg = get_config()
    ollama_url = cfg["backend"]["url"].rstrip("/")

    # Models to pull
    required = [cfg["embedding"]["model"]]

    d_cfg = cfg.get("decision_llm", {})
    if d_cfg.get("enabled") and d_cfg.get("model"):
        required.append(d_cfg["model"])

    if args.model:
        required.extend(args.model)

    print(BANNER)
    print(f"  Ollama: {ollama_url}")
    print(f"  Models to pull: {', '.join(required)}")
    print()

    # Check Ollama is reachable
    try:
        resp = httpx.get(f"{ollama_url}/api/tags", timeout=5)
        resp.raise_for_status()
        existing = [m["name"] for m in resp.json().get("models", [])]
    except Exception as e:
        print(f"  ✗  Cannot reach Ollama at {ollama_url}: {e}")
        print(f"     Make sure Ollama is running: ollama serve")
        return

    for model in required:
        # Split on ":" to strip tag specifics (e.g. ":latest", ":7b") so
        # "qwen3:4b" still matches "qwen3:4b-instruct" in the installed list.
        model_base = model.split(":")[0]
        already = any(model_base in m for m in existing)

        if already:
            print(f"  ✓  {model} — already available")
            continue

        print(f"  ↓  Pulling {model}...")
        try:
            with httpx.stream(
                "POST",
                f"{ollama_url}/api/pull",
                json={"name": model},
                # timeout=None is intentional — large models (7B+) can take
                # many minutes to download. Any finite timeout would break
                # pulls on slow connections without warning.
                timeout=None,
            ) as resp:
                for line in resp.iter_lines():
                    if line:
                        import json
                        try:
                            data = json.loads(line)
                            status = data.get("status", "")
                            if "pulling" in status:
                                total = data.get("total", 0)
                                completed = data.get("completed", 0)
                                if total > 0:
                                    pct = completed / total * 100
                                    # \r overwrites the same line; filled/empty block
                                    # chars give a 20-segment progress bar.
                                    bar = "█" * int(pct // 5) + "░" * (20 - int(pct // 5))
                                    print(f"\r     {bar} {pct:.0f}%  ", end="", flush=True)
                            elif status:
                                print(f"\r     {status}              ", end="", flush=True)
                        except json.JSONDecodeError:
                            pass
            print(f"\r  ✓  {model} — pulled                    ")
        except Exception as e:
            print(f"\r  ✗  {model} — failed: {e}               ")

    print()
    print("  Done. Run 'beigebox dial' to start the proxy.")


def cmd_ring(args):
    """Ping a running BeigeBox instance."""
    import httpx

    url = args.url or "http://localhost:1337"
    try:
        resp = httpx.get(f"{url}/beigebox/health", timeout=5)
        if resp.status_code == 200:
            print(f"  ☎  Ring ring... {url} is UP")

            stats = httpx.get(f"{url}/beigebox/stats", timeout=5).json()
            sq = stats.get("sqlite", {})
            vq = stats.get("vector", {})
            tools = stats.get("tools", [])
            hooks = stats.get("hooks", [])
            dlm = stats.get("decision_llm", {})
            tokens = sq.get("tokens", {})

            print(f"  📼 Conversations: {sq.get('conversations', 0)}")
            print(f"  💬 Messages: {sq.get('messages', 0)} (user: {sq.get('user_messages', 0)}, assistant: {sq.get('assistant_messages', 0)})")
            if tokens.get("total", 0) > 0:
                print(f"  📊 Tokens: ~{tokens['total']:,} total")
            print(f"  🧲 Embeddings: {vq.get('total_embeddings', 0)}")
            print(f"  🔧 Tools: {', '.join(tools) if tools else 'none'}")
            print(f"  🪝 Hooks: {', '.join(hooks) if hooks else 'none'}")
            print(f"  🧠 Decision LLM: {'enabled (' + dlm.get('model', '?') + ')' if dlm.get('enabled') else 'disabled'}")
        else:
            print(f"  ✗  No answer — got HTTP {resp.status_code}")
    except httpx.ConnectError:
        print(f"  ✗  Dead line — nothing at {url}")
    except Exception as e:
        print(f"  ✗  Error: {e}")


def cmd_sweep(args):
    """Semantic search over stored conversations."""
    from beigebox.config import get_config, get_storage_paths
    from beigebox.storage.vector_store import VectorStore
    from beigebox.storage.backends import make_backend, build_backend_kwargs

    cfg = get_config()
    _, vector_store_path = get_storage_paths(cfg)
    _ec = cfg["embedding"]
    _btype, _bkw = build_backend_kwargs(cfg, vector_store_path)
    store = VectorStore(
        embedding_model=_ec["model"],
        embedding_url=_ec.get("backend_url") or cfg["backend"]["url"],
        backend=make_backend(_btype, **_bkw),
    )

    query = " ".join(args.query)
    print(f"  🔍 Sweeping for: '{query}'")
    print("  " + "─" * 56)

    results = store.search(query, n_results=args.results, role_filter=args.role)

    if not results:
        print("  No signal found.")
        return

    for i, hit in enumerate(results, 1):
        meta = hit["metadata"]
        # ChromaDB returns cosine distance [0, 2]; convert to similarity [0, 1].
        score = 1 - hit["distance"]
        content = hit["content"]
        if len(content) > 200:
            content = content[:200] + "..."

        role = meta.get("role", "?")
        role_color = "\033[96m" if role == "user" else "\033[93m"
        reset = "\033[0m"

        print(f"\n  [{i}] {role_color}{role.upper()}{reset} | score: {score:.3f} | model: {meta.get('model', '?')}")
        print(f"      conv: {meta.get('conversation_id', '?')[:16]}...")
        print(f"      {content}")


def cmd_dump(args):
    """Export conversations to JSON."""
    import json
    from beigebox.config import get_config
    from beigebox.storage.sqlite_store import SQLiteStore

    cfg = get_config()
    from beigebox.config import get_storage_paths
    sqlite_path, _ = get_storage_paths(cfg)
    store = SQLiteStore(sqlite_path)
    stats = store.get_stats()

    print(f"  📼 Database: {sqlite_path}")
    print(f"  💬 Conversations: {stats['conversations']} | Messages: {stats['messages']}")

    data = store.export_all_json()
    indent = 2 if args.pretty else None

    with open(args.output, "w") as f:
        json.dump(data, f, indent=indent, ensure_ascii=False)

    print(f"  📦 Dumped {len(data)} conversations to {args.output}")


def cmd_flash(args):
    """Show stats at a glance."""
    from beigebox.config import get_config, get_storage_paths
    from beigebox.storage.sqlite_store import SQLiteStore

    cfg = get_config()
    sqlite_path, vector_path = get_storage_paths(cfg)

    print(BANNER)
    print("  Configuration")
    print(f"  ├─ Backend:   {cfg['backend']['url']}")
    print(f"  ├─ Model:     {cfg['backend']['default_model']}")
    print(f"  ├─ Embedder:  {cfg['embedding']['model']}")
    print(f"  ├─ SQLite:    {sqlite_path}")
    print(f"  ├─ ChromaDB:  {vector_path}")
    print(f"  └─ Logging:   {cfg['storage'].get('log_conversations', True)}")

    try:
        store = SQLiteStore(sqlite_path)
        stats = store.get_stats()
        tokens = stats.get("tokens", {})
        models = stats.get("models", {})

        print()
        print("  Storage")
        print(f"  ├─ Conversations: {stats['conversations']}")
        print(f"  ├─ Messages:      {stats['messages']}")
        print(f"  ├─ User msgs:     {stats['user_messages']}")
        print(f"  └─ Asst msgs:     {stats['assistant_messages']}")

        if tokens.get("total", 0) > 0:
            print()
            print("  Tokens (estimated)")
            print(f"  ├─ Total:     {tokens['total']:,}")
            print(f"  ├─ User:      {tokens['user']:,}")
            print(f"  └─ Assistant: {tokens['assistant']:,}")

        if models:
            print()
            print("  Models")
            model_items = list(models.items())
            for i, (model, info) in enumerate(model_items):
                prefix = "└─" if i == len(model_items) - 1 else "├─"
                print(f"  {prefix} {model}: {info['messages']} msgs, ~{info['tokens']:,} tokens")

    except Exception:
        print("\n  Storage: (no database yet — run 'beigebox dial' first)")

    tools_cfg = cfg.get("tools", {})
    enabled = []
    if tools_cfg.get("web_search", {}).get("enabled"):
        enabled.append(f"web_search ({tools_cfg['web_search'].get('provider', 'duckduckgo')})")
    if tools_cfg.get("web_scraper", {}).get("enabled"):
        enabled.append("web_scraper")
    if tools_cfg.get("google_search", {}).get("enabled"):
        enabled.append("google_search")

    print()
    print("  Tools")
    print(f"  └─ {', '.join(enabled) if enabled else 'none enabled'}")

    # Decision LLM
    d_cfg = cfg.get("decision_llm", {})
    print()
    print("  Decision LLM")
    if d_cfg.get("enabled"):
        print(f"  ├─ Model:   {d_cfg.get('model', '?')}")
        print(f"  ├─ Timeout: {d_cfg.get('timeout', 5)}s")
        routes = d_cfg.get("routes", {})
        print(f"  └─ Routes:  {', '.join(routes.keys()) if routes else 'default only'}")
    else:
        print("  └─ disabled")


    # Cost summary — reads directly from SQLite via CostTracker.
    # getattr guards against commands that share cmd_flash but don't add --days.
    # Falls back silently if the DB doesn't exist or cost_tracking is disabled.
    days = getattr(args, "days", 30)
    try:
        from beigebox.costs import CostTracker
        cost_store = SQLiteStore(sqlite_path)
        tracker = CostTracker(cost_store)
        costs = tracker.get_stats(days=days)
        total = costs.get("total", 0)
        by_model = costs.get("by_model", {})
        print()
        print(f"  API Costs (last {costs['days_queried']} days)")
        if total > 0 or by_model:
            print(f"  ├─ Total:     ${total:.4f}")
            print(f"  ├─ Daily avg: ${costs['average_daily']:.4f}")
            model_items = list(by_model.items())
            for i, (model, info) in enumerate(model_items):
                prefix = "└─" if i == len(model_items) - 1 else "├─"
                print(f"  {prefix} {model}: ${info['cost']:.4f}  ({info['messages']} msgs)")
            by_day = costs.get("by_day", {})
            if by_day:
                recent = list(by_day.items())[:5]
                print()
                print("  Recent daily spend")
                for i, (day, cost) in enumerate(recent):
                    prefix = "└─" if i == len(recent) - 1 else "├─"
                    print(f"  {prefix} {day}: ${cost:.4f}")
        else:
            print("  └─ No API costs recorded (local models are $0.00)")
    except Exception:
        pass  # No DB yet, or cost_tracking not enabled in config

    # Model performance — latency percentiles per model (v0.8+)
    try:
        perf_store = SQLiteStore(sqlite_path)
        perf = perf_store.get_model_performance(days=days)
        by_model_perf = perf.get("by_model", {})
        if by_model_perf:
            print()
            print(f"  Model Performance (last {perf['days_queried']} days)")
            # Header
            print(f"  {'Model':<28} {'Reqs':>5}  {'Avg':>7}  {'p50':>7}  {'p95':>7}  {'$/msg':>8}")
            print("  " + "─" * 68)
            items = sorted(by_model_perf.items(), key=lambda x: x[1]["requests"], reverse=True)
            for model, s in items:
                avg_cost = (s["total_cost_usd"] / s["requests"]) if s["requests"] else 0
                name = model[:26] + ".." if len(model) > 28 else model
                # Colour-code p95: green <1s, yellow <3s, red ≥3s
                p95 = s["p95_latency_ms"]
                # Colour-code p95: red ≥3s (unacceptably slow), yellow 1-3s
                # (borderline), green <1s (healthy for local inference).
                if p95 >= 3000:
                    p95_col = "\033[91m"
                elif p95 >= 1000:
                    p95_col = "\033[93m"
                else:
                    p95_col = "\033[92m"
                reset = "\033[0m"
                p95_str = f"{p95_col}{p95:>5.0f}ms{reset}"
                print(
                    f"  {name:<28} {s['requests']:>5}  "
                    f"{s['avg_latency_ms']:>5.0f}ms  {s['p50_latency_ms']:>5.0f}ms  "
                    f"{p95_str}  ${avg_cost:>7.5f}"
                )
    except Exception:
        pass  # No DB yet or no latency data


def cmd_quarantine_list(args):
    """List quarantined embeddings."""
    from beigebox.config import get_config, get_storage_paths
    from beigebox.storage.sqlite_store import SQLiteStore

    cfg = get_config()
    sqlite_path, _ = get_storage_paths(cfg)
    store = SQLiteStore(sqlite_path)

    filters = args.filter or "all"
    limit = args.limit or 100

    records = store.search_quarantined(filters=filters, limit=limit)

    if not records:
        print(f"  No quarantined embeddings ({filters}).")
        return

    print(f"\n  Quarantined Embeddings ({filters})\n")
    print(f"  {'ID':>5} {'Timestamp':<25} {'Document':<20} {'Confidence':>10} {'Reason':<35}")
    print("  " + "─" * 110)

    for rec in records:
        doc_id = rec["document_id"][:18] + ".." if len(rec["document_id"]) > 20 else rec["document_id"]
        confidence = rec["confidence"]
        reason = rec["reason"][:33] + ".." if len(rec["reason"]) > 35 else rec["reason"]
        timestamp = rec["timestamp"][:19] if rec["timestamp"] else "?"

        print(
            f"  {rec['id']:>5} {timestamp:<25} {doc_id:<20} {confidence:>10.3f} {reason:<35}"
        )

    print()


def cmd_quarantine_review(args):
    """Show full details for a quarantine record."""
    import json
    from beigebox.config import get_config, get_storage_paths
    from beigebox.storage.sqlite_store import SQLiteStore

    cfg = get_config()
    sqlite_path, _ = get_storage_paths(cfg)
    store = SQLiteStore(sqlite_path)

    record_id = args.id

    with store._connect() as conn:
        row = conn.execute(
            "SELECT * FROM quarantined_embeddings WHERE id = ?",
            (record_id,),
        ).fetchone()

    if not row:
        print(f"  ✗  No quarantine record with ID {record_id}")
        return

    rec = dict(row)
    print(f"\n  Quarantine Record #{rec['id']}\n")
    print(f"  Timestamp:      {rec['timestamp']}")
    print(f"  Document ID:    {rec['document_id']}")
    print(f"  Embedding Hash: {rec['embedding_hash'] or '(not stored)'}")
    print(f"  Confidence:     {rec['confidence']:.4f}")
    print(f"  Method:         {rec['detector_method']}")
    print(f"  Reason:\n    {rec['reason']}")
    print()


def cmd_quarantine_stats(args):
    """Show quarantine statistics."""
    from beigebox.config import get_config, get_storage_paths
    from beigebox.storage.sqlite_store import SQLiteStore

    cfg = get_config()
    sqlite_path, _ = get_storage_paths(cfg)
    store = SQLiteStore(sqlite_path)

    stats = store.get_quarantine_stats()

    print(f"\n  Quarantine Statistics\n")
    print(f"  Total quarantined:     {stats['total']}")
    print(f"  High confidence (>0.8): {stats['high_confidence']}")
    print(f"  Medium (0.5-0.8):       {stats['medium_confidence']}")
    print(f"  Last 24 hours:          {stats['last_24h']}")
    print()

    if stats["total"] > 0:
        print(f"  Confidence Percentiles")
        print(f"  ├─ P50: {stats['confidence_p50']:.4f}")
        print(f"  └─ P95: {stats['confidence_p95']:.4f}")
        print()

        if stats["reasons"]:
            print(f"  Top Reasons")
            for i, (reason, count) in enumerate(sorted(stats["reasons"].items(), key=lambda x: x[1], reverse=True)[:5]):
                prefix = "└─" if i == len(stats["reasons"]) - 1 else "├─"
                reason_short = reason[:50] + ".." if len(reason) > 50 else reason
                print(f"  {prefix} {reason_short}: {count}")
            print()

        if stats["methods"]:
            print(f"  Detection Methods")
            for i, (method, count) in enumerate(sorted(stats["methods"].items(), key=lambda x: x[1], reverse=True)):
                prefix = "└─" if i == len(stats["methods"]) - 1 else "├─"
                print(f"  {prefix} {method}: {count}")
            print()


def cmd_quarantine_purge(args):
    """Purge old quarantine records."""
    from beigebox.config import get_config, get_storage_paths
    from beigebox.storage.sqlite_store import SQLiteStore

    cfg = get_config()
    sqlite_path, _ = get_storage_paths(cfg)
    store = SQLiteStore(sqlite_path)

    days = args.days or 30
    dry_run = args.dry_run or False

    count = store.purge_quarantine(days=days, dry_run=dry_run)

    if dry_run:
        print(f"\n  [DRY RUN] Would delete {count} quarantine records older than {days} days")
    else:
        print(f"\n  ✓ Deleted {count} quarantine records older than {days} days")
    print()


def cmd_tone(args):
    """Print the banner."""
    print(BANNER)


def cmd_index_docs(args):
    """Index markdown/text files into ChromaDB for semantic search."""
    import os
    import hashlib
    from pathlib import Path
    from beigebox.config import get_config, get_storage_paths
    from beigebox.storage.vector_store import VectorStore
    from beigebox.storage.backends import make_backend, build_backend_kwargs
    from beigebox.storage.chunker import chunk_text

    doc_path = Path(args.path).resolve()
    if not doc_path.exists():
        print(f"  ✗  Path not found: {doc_path}")
        return

    cfg = get_config()
    _, vector_store_path = get_storage_paths(cfg)
    _ec = cfg["embedding"]
    _btype, _bkw = build_backend_kwargs(cfg, vector_store_path)

    store = VectorStore(
        embedding_model=_ec["model"],
        embedding_url=_ec.get("backend_url") or cfg["backend"]["url"],
        backend=make_backend(_btype, **_bkw),
    )

    print(BANNER)
    print(f"  Indexing documents from: {doc_path}")
    print()

    total_files = 0
    total_chunks = 0

    # If indexing 2600/, also include 2600/2599/ (archived docs)
    index_paths = [doc_path]
    if doc_path.name == "2600" and (doc_path / "2599").exists():
        index_paths.append(doc_path / "2599")
        print("  (also including archived docs in 2600/2599/)")
        print()

    # Walk directories for .md and .txt files
    for search_path in index_paths:
        for root, _, files in os.walk(search_path):
            for filename in sorted(files):
                if not filename.endswith((".md", ".txt")):
                    continue

                filepath = Path(root) / filename
                rel_path = filepath.relative_to(doc_path)

                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        text = f.read()

                    # Compute SHA-256 of file content for deduplication
                    blob_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

                    # Chunk the text
                    chunks = chunk_text(text, source_file=str(rel_path))

                    if not chunks:
                        continue

                    # Store each chunk
                    for chunk in chunks:
                        store.store_document_chunk(
                            source_file=str(rel_path),
                            chunk_index=chunk["chunk_index"],
                            char_offset=chunk["char_offset"],
                            blob_hash=blob_hash,
                            text=chunk["text"],
                        )

                    total_files += 1
                    total_chunks += len(chunks)
                    print(f"  ✓  {rel_path} ({len(chunks)} chunks)")

                except Exception as e:
                    print(f"  ✗  {rel_path} — {e}")

    print()
    print(f"  Indexed {total_files} files, {total_chunks} chunks")
    print(f"  ChromaDB: {vector_store_path}")
    print()
    print("  Operator can now search these docs via 'document_search' tool.")


def cmd_discover_models(args):
    """Discover and display model resource specs from a running BeigeBox instance."""
    import httpx

    url = args.url or "http://localhost:1337"
    try:
        backends = httpx.get(f"{url}/api/v1/backends", timeout=5).json()
        metrics = httpx.get(f"{url}/api/v1/system-metrics", timeout=5).json()
        specs = httpx.get(f"{url}/api/v1/model-specs", timeout=5).json()
    except Exception as e:
        print(f"  ✗  Cannot reach BeigeBox at {url}: {e}")
        return

    print(BANNER)
    print("  Model Resource Discovery")
    print("  " + "─" * 60)

    # Currently loaded models from Ollama /api/ps (via hw_stats)
    loaded = []
    if backends.get("enabled"):
        for b in backends.get("backends", []):
            for hw in (b.get("hw_stats") or []):
                loaded.append({**hw, "backend": b.get("name", "?")})

    if loaded:
        print()
        print("  Currently loaded models:")
        print(f"  {'Model':<35} {'Backend':<12} {'VRAM MB':>8} {'Layers':>10} {'Ctx':>8}")
        print("  " + "─" * 75)
        for m in loaded:
            layers = (
                f"{m['gpu_layers']}/{m['total_layers']}"
                if m.get("total_layers")
                else str(m.get("gpu_layers", 0))
            )
            print(
                f"  {m['model']:<35} {m['backend']:<12} {m.get('vram_used_mb', 0):>8} {layers:>10} {m.get('context_window', 0):>8}"
            )

    # GPU info
    gpus = metrics.get("gpus", [])
    if gpus:
        print()
        print("  GPU(s):")
        for g in gpus:
            mem_used = g.get("memory_used_mb", 0)
            mem_total = g.get("memory_total_mb", 0)
            headroom = mem_total - mem_used if mem_total else 0
            print(
                f"  [{g['id']}] {g['name']} — {mem_used}/{mem_total} MB used — {headroom} MB free"
            )

    # All discovered specs from SQLite
    all_specs = specs.get("specs", [])
    if all_specs:
        print()
        print("  All discovered model specs (SQLite):")
        print(
            f"  {'Model':<35} {'Backend':<12} {'VRAM MB':>8} {'Method':<14} {'Last seen'}"
        )
        print("  " + "─" * 85)
        for s in all_specs:
            last = s.get("last_seen_loaded", "")[:19] if s.get("last_seen_loaded") else "—"
            vram = str(s.get("vram_mb", "—")) if s.get("vram_mb") else "—"
            print(
                f"  {s['model_name']:<35} {s['backend']:<12} {vram:>8} {s.get('discovery_method','?'):<14} {last}"
            )
    else:
        print()
        print("  No model specs discovered yet. Run a model to populate.")

    print()


def cmd_models(args):
    """Pull the OpenRouter model catalog from a running BeigeBox instance.

    Hits /api/v1/openrouter/models (already sanitized — id/name/context_length/pricing)
    and renders a sortable, filterable table. JSON escape hatch via --json.
    """
    import httpx

    url = (args.url or "http://localhost:1337").rstrip("/")
    try:
        resp = httpx.get(f"{url}/api/v1/openrouter/models", timeout=20)
        resp.raise_for_status()
        models = resp.json().get("data", [])
    except Exception as e:
        print(f"  ✗  Cannot reach BeigeBox at {url}: {e}", file=sys.stderr)
        sys.exit(2)

    if not models:
        print("  No OpenRouter models — backend not configured or API key missing.",
              file=sys.stderr)
        sys.exit(1)

    # Filter
    if args.search:
        needle = args.search.lower()
        models = [
            m for m in models
            if needle in m.get("id", "").lower() or needle in m.get("name", "").lower()
        ]

    # Sort
    def _price(m, key):
        try:
            return float((m.get("pricing") or {}).get(key) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    sort_key = (args.sort or "id").lower()
    sorters = {
        "id":      lambda m: m.get("id", ""),
        "name":    lambda m: m.get("name", "") or "",
        "context": lambda m: -int(m.get("context_length") or 0),
        "prompt":  lambda m: _price(m, "prompt"),
        "comp":    lambda m: _price(m, "completion"),
    }
    if sort_key not in sorters:
        print(f"  ✗  unknown --sort value: {sort_key}. "
              f"Choose from: {', '.join(sorters)}", file=sys.stderr)
        sys.exit(2)
    models.sort(key=sorters[sort_key])

    if args.limit:
        models = models[: args.limit]

    if args.json:
        import json
        print(json.dumps(models, indent=2))
        return

    # Table
    print(f"  OpenRouter catalog — {len(models)} model(s)")
    if args.search:
        print(f"  Filter: '{args.search}'")
    print("  " + "─" * 96)
    print(f"  {'ID':<48} {'Ctx':>9} {'Prompt $/1K':>13} {'Comp $/1K':>13}")
    print("  " + "─" * 96)
    for m in models:
        mid = m.get("id", "?")
        ctx = m.get("context_length") or 0
        pricing = m.get("pricing") or {}
        # OpenRouter prices are per-token; show per-1K-token for human eyes.
        try:
            p_prompt = float(pricing.get("prompt") or 0) * 1000.0
            p_comp = float(pricing.get("completion") or 0) * 1000.0
        except (TypeError, ValueError):
            p_prompt = p_comp = 0.0
        print(
            f"  {mid:<48} "
            f"{ctx:>9,} "
            f"{('$%.5f' % p_prompt):>13} "
            f"{('$%.5f' % p_comp):>13}"
        )
    print()


def cmd_rankings(args):
    """Show Artificial Analysis top-15 agentic + coding rankings via the BB cache.

    Hits /api/v1/artificial-analysis/rankings (1-hour-cached scrape). JSON
    escape hatch via --json.
    """
    import httpx

    url = (args.url or "http://localhost:1337").rstrip("/")
    try:
        resp = httpx.get(f"{url}/api/v1/artificial-analysis/rankings", timeout=25)
        resp.raise_for_status()
        data = resp.json() or {}
    except Exception as e:
        print(f"  ✗  Cannot reach BeigeBox at {url}: {e}", file=sys.stderr)
        sys.exit(2)

    agentic = data.get("agentic", []) or []
    coding = data.get("coding", []) or []

    if args.json:
        import json
        print(json.dumps(data, indent=2))
        return

    def _print_board(title, entries, limit):
        if not entries:
            print(f"  {title}: (no data)")
            return
        entries = entries[:limit]
        print(f"  {title}")
        print("  " + "─" * 70)
        print(f"  {'#':>3}  {'Model':<48} {'Score':>10}")
        print("  " + "─" * 70)
        for i, m in enumerate(entries, 1):
            name = m.get("model") or m.get("name") or m.get("id") or "?"
            score_raw = m.get("score") if "score" in m else m.get("intelligence_index")
            try:
                score = f"{float(score_raw):.2f}"
            except (TypeError, ValueError):
                score = "—"
            print(f"  {i:>3}  {name[:48]:<48} {score:>10}")
        print()

    limit = args.top or 15
    track = (args.track or "both").lower()
    print()
    if track in ("agentic", "both"):
        _print_board("Agentic ranking (Artificial Analysis)", agentic, limit)
    if track in ("coding", "both"):
        _print_board("Coding ranking (Artificial Analysis)", coding, limit)


def cmd_deployment_plan(args):
    """Print a capacity summary: VRAM headroom and potential OOM risks."""
    import httpx

    url = args.url or "http://localhost:1337"
    try:
        backends = httpx.get(f"{url}/api/v1/backends", timeout=5).json()
        metrics = httpx.get(f"{url}/api/v1/system-metrics", timeout=5).json()
        specs = httpx.get(f"{url}/api/v1/model-specs", timeout=5).json()
    except Exception as e:
        print(f"  ✗  Cannot reach BeigeBox at {url}: {e}")
        return

    print(BANNER)
    print("  Deployment Capacity Plan")
    print("  " + "─" * 60)

    gpus = metrics.get("gpus", [])
    all_specs = specs.get("specs", [])

    # Build per-GPU VRAM budget
    if gpus:
        print()
        print("  GPU VRAM:")
        for g in gpus:
            used = g.get("memory_used_mb", 0)
            total = g.get("memory_total_mb", 0)
            free = total - used
            bar_pct = used / total if total else 0
            bar_filled = int(bar_pct * 20)
            bar = "█" * bar_filled + "░" * (20 - bar_filled)
            warn = " ⚠ HIGH" if bar_pct > 0.85 else (" ~ warn" if bar_pct > 0.70 else "")
            print(f"  GPU {g['id']} {g['name']}")
            print(
                f"    [{bar}] {used}/{total} MB ({bar_pct*100:.0f}% used){warn}"
            )
            print(f"    Free: {free} MB")

    # Per-model footprint from discovered specs
    if all_specs:
        print()
        print("  Known model VRAM footprints:")
        for s in all_specs:
            vram = s.get("vram_mb")
            vstr = f"{vram} MB" if vram else "unknown"
            # Flag if known vram > free VRAM on any GPU
            risk = ""
            if vram and gpus:
                for g in gpus:
                    free = g.get("memory_total_mb", 0) - g.get("memory_used_mb", 0)
                    if vram > free:
                        risk = f"  ⚠ OOM risk on GPU {g['id']} ({free} MB free)"
            print(f"  {s['model_name']:<35} {vstr:>10}{risk}")

    # System RAM
    print()
    ram_pct = metrics.get("ram_percent")
    ram_used = metrics.get("ram_used_mb")
    ram_total = metrics.get("ram_total_mb")
    if ram_pct is not None:
        ram_warn = (
            " ⚠ HIGH" if ram_pct > 85 else (" ~ warn" if ram_pct > 70 else "")
        )
        print(f"  System RAM: {ram_used}/{ram_total} MB ({ram_pct:.0f}% used){ram_warn}")

    print()


def cmd_bench(args):
    """Run a direct-to-Ollama speed benchmark (bypasses proxy)."""
    import asyncio
    import json

    models = args.model or []
    if not models:
        print("  ✗  Provide at least one --model/-m <name>")
        return

    from beigebox.config import get_config
    from beigebox.bench import BenchmarkRunner, DEFAULT_PROMPT, DEFAULT_NUM_PREDICT, DEFAULT_NUM_RUNS

    cfg = get_config()
    ollama_url = (args.ollama_url or cfg.get("backend", {}).get("url", "http://localhost:11434")).rstrip("/")
    num_predict = args.num_predict or DEFAULT_NUM_PREDICT
    num_runs = args.num_runs or DEFAULT_NUM_RUNS
    prompt = args.prompt or DEFAULT_PROMPT

    print(f"\n  BeigeBox Bench — direct Ollama speed test")
    print(f"  Ollama:      {ollama_url}")
    print(f"  Models:      {', '.join(models)}")
    print(f"  Runs:        {num_runs} (+ 1 warmup each)")
    print(f"  num_predict: {num_predict} tokens")
    print()

    async def _run():
        runner = BenchmarkRunner(ollama_url=ollama_url)
        async for event in runner.run_stream(
            models=models,
            prompt=prompt,
            num_predict=num_predict,
            num_runs=num_runs,
        ):
            etype = event.get("event", "")
            if etype == "warmup":
                status = event.get("status", "")
                model = event.get("model", "")
                if status == "starting":
                    print(f"  ⟳  {model}  warming up…", flush=True)
                elif status == "done":
                    print(f"  ✓  {model}  warm  (load {event.get('load_ms', '?'):.0f} ms)", flush=True)
                elif status == "error":
                    print(f"  ✗  {model}  warmup error: {event.get('error', '')}", flush=True)
            elif etype == "run":
                r = event.get("result", {})
                ok = r.get("ok", False)
                tok_s = r.get("tok_s", 0)
                ttft = r.get("ttft_ms", 0)
                sym = "✓" if ok else "✗"
                run_n = event.get("run", "?")
                total = event.get("total", "?")
                print(f"    {sym} run {run_n}/{total}  {tok_s:>7.1f} tok/s   ttft {ttft:.0f} ms", flush=True)
            elif etype == "model_done":
                s = event.get("summary", {})
                print(f"\n  {s['model']}")
                print(f"    avg  {s['avg_tokens_per_sec']:>7.1f} tok/s")
                print(f"    med  {s['median_tokens_per_sec']:>7.1f} tok/s")
                print(f"    ttft {s['avg_ttft_ms']:>7.1f} ms")
                print()
            elif etype == "done":
                results = event.get("results", [])
                print(f"  {'Model':<40} {'avg tok/s':>10}  {'med tok/s':>10}  {'TTFT ms':>8}  {'OK/Total':>8}")
                print("  " + "─" * 85)
                for r in results:
                    ok_str = f"{r['runs_ok']}/{r['runs_total']}"
                    print(f"  {r['model']:<40} {r['avg_tokens_per_sec']:>10.1f}  {r['median_tokens_per_sec']:>10.1f}  {r['avg_ttft_ms']:>8.1f}  {ok_str:>8}")
                print()
                if args.output:
                    with open(args.output, "w") as f:
                        json.dump(results, f, indent=2)
                    print(f"  Results saved to: {args.output}")
            elif etype == "error":
                print(f"  ✗  {event.get('message', 'unknown error')}")

    asyncio.run(_run())


def cmd_experiment(args):
    """Run a context optimization discovery experiment."""
    import asyncio
    import json
    import httpx

    url = getattr(args, "url", None) or "http://localhost:1337"
    opportunity_id = args.opportunity
    weight_profile = getattr(args, "weight_profile", "general") or "general"
    output_file = getattr(args, "output", None)

    # List mode
    if getattr(args, "list", False):
        try:
            resp = httpx.get(f"{url}/api/v1/discovery/opportunities", timeout=10)
            opps = resp.json().get("opportunities", [])
            print(f"\n  {'ID':<35} {'Variants':<10} {'Expected Impact'}")
            print("  " + "─" * 75)
            for o in opps:
                print(f"  {o['opportunity_id']:<35} {len(o['variants']):<10} {o['expected_impact']}")
            print()
        except Exception as e:
            print(f"  ✗  Cannot reach BeigeBox at {url}: {e}")
        return

    if not opportunity_id:
        print("  ✗  Provide --opportunity <id>  or  --list to see available experiments.")
        return

    print(f"\n  Running discovery experiment: {opportunity_id}")
    print(f"  Weight profile: {weight_profile}")
    print(f"  BeigeBox: {url}\n")

    try:
        resp = httpx.post(
            f"{url}/api/v1/discovery/run",
            json={"opportunity_id": opportunity_id, "weight_profile": weight_profile},
            timeout=600,  # experiments can take a while
        )
        result = resp.json()
    except Exception as e:
        print(f"  ✗  Experiment failed: {e}")
        return

    if "error" in result:
        print(f"  ✗  {result['error']}")
        return

    run_id = result.get("run_id", "?")
    champion = result.get("champion") or {}
    pareto = result.get("pareto_front", [])
    scorecards = result.get("scorecards", [])
    stats = result.get("statistics", {})
    summary = result.get("summary", {})

    print(f"  Run ID:     {run_id}")
    print(f"  Champion:   {champion.get('name', 'none')}  (score={champion.get('weighted', 0):.3f})")
    print(f"  Pareto:     {len(pareto)} variant(s) on frontier")
    print(f"  Oracle:     {summary.get('oracle_pass_rate', '?'):.0%} pass rate")
    print()

    print(f"  {'Variant':<35} {'Score':>7}  {'Accuracy':>9}  {'Latency':>10}  Verdict")
    print("  " + "─" * 85)
    for sc in scorecards:
        vname = sc["variant"]
        score = sc.get("overall", 0)
        acc = sc.get("scores", {}).get("accuracy", 0)
        lat = sc.get("mean_latency_ms", 0)
        stat = stats.get(vname, {})
        verdict = stat.get("verdict", "baseline")
        print(f"  {vname:<35} {score:>7.3f}  {acc:>9.1f}  {lat:>9.0f}ms  {verdict}")

    print()
    if output_file:
        with open(output_file, "w") as f:
            json.dump(result, f, indent=2)
        print(f"  Results saved to: {output_file}")


# ---------------------------------------------------------------------------
# Parser with aliases
# ---------------------------------------------------------------------------

def _add_command(subparsers, names, help_text, func, setup_fn=None):
    """Register a command under multiple names (phreaker + standard).

    names[0] is the canonical name shown in help; names[1:] are hidden aliases
    so familiar commands like "start" and "pull" work without user training.
    """
    p = subparsers.add_parser(names[0], help=help_text, aliases=names[1:])
    p.set_defaults(func=func)
    if setup_fn:
        setup_fn(p)
    return p


def main():
    parser = argparse.ArgumentParser(
        prog="beigebox",
        description="BeigeBox — Tap the line. Control the carrier.",
        epilog=(
            "Each command has a phreaker name and standard aliases.\n"
            "Example: 'beigebox dial' and 'beigebox start' do the same thing.\n"
            "Run 'beigebox <command> --help' for command-specific options."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", "-V", action="version",
        version=f"beigebox {__version__}",
    )

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # dial / start / serve / up
    def setup_dial(p):
        p.add_argument("--host", default=None, help="Override listen host")
        p.add_argument("--port", "-p", type=int, default=None, help="Override listen port")
        p.add_argument("--reload", action="store_true", help="Auto-reload on code changes (dev)")

    _add_command(sub, ["dial", "start", "serve", "up"],
                 "Start the BeigeBox proxy server", cmd_dial, setup_dial)

    # setup / install / pull
    def setup_setup(p):
        p.add_argument("--model", "-m", action="append", default=None,
                        help="Additional model to pull (can specify multiple times)")

    _add_command(sub, ["setup", "install", "pull"],
                 "Pull required models into Ollama", cmd_setup, setup_setup)

    # tap / log / tail / watch
    def setup_tap(p):
        p.add_argument("--log", default=None, help="Path to wire.jsonl (default: from config)")
        p.add_argument("--last", "-n", type=int, default=20, help="Show last N entries before following")
        p.add_argument("--role", "-r", default=None,
                       help="Filter by role (user, assistant, system, tool, decision, "
                            "cache, router, request, backend, classifier, harness, error, etc.)")
        p.add_argument("--no-follow", action="store_true", help="Don't follow, just show last entries")
        p.add_argument("--raw", action="store_true", help="Raw JSONL output, no formatting")

    _add_command(sub, ["tap", "log", "tail", "watch"],
                 "Live wiretap — watch conversations on the wire", cmd_tap, setup_tap)

    # ring / status / ping / health
    def setup_ring(p):
        p.add_argument("--url", "-u", default=None, help="BeigeBox URL (default: http://localhost:1337)")

    _add_command(sub, ["ring", "status", "ping", "health"],
                 "Ping a running BeigeBox instance", cmd_ring, setup_ring)

    # sweep / search / find / query
    def setup_sweep(p):
        p.add_argument("query", nargs="+", help="Search query")
        p.add_argument("--results", "-n", type=int, default=5, help="Number of results")
        p.add_argument("--role", "-r", default=None, help="Filter by role")

    _add_command(sub, ["sweep", "search", "find", "query"],
                 "Semantic search over conversations", cmd_sweep, setup_sweep)

    # dump / export
    def setup_dump(p):
        p.add_argument("--output", "-o", default="conversations_export.json", help="Output file")
        p.add_argument("--pretty", action="store_true", help="Pretty-print JSON")

    _add_command(sub, ["dump", "export"],
                 "Export conversations to JSON", cmd_dump, setup_dump)

    # flash / info / config / stats
    def setup_flash(p):
        p.add_argument("--days", "-d", type=int, default=30,
                        help="Days of cost history to show (default: 30)")
    _add_command(sub, ["flash", "info", "config", "stats"],
                 "Show stats and config at a glance", cmd_flash, setup_flash)

    # tone / banner
    _add_command(sub, ["tone", "banner"],
                 "Print the BeigeBox banner", cmd_tone)

    # index-docs / index
    def setup_index_docs(p):
        p.add_argument("path", help="Directory path containing .md/.txt files to index")

    _add_command(sub, ["index-docs", "index"],
                 "Index markdown/text docs into ChromaDB for semantic search", cmd_index_docs, setup_index_docs)

    # `beigebox operator` subcommand removed in v3 — Operator class deleted.

    def setup_discover_models(p):
        p.add_argument("--url", "-u", default=None,
                       help="BeigeBox URL (default: http://localhost:1337)")

    _add_command(sub, ["discover-models", "discover"],
                 "Discover and display model resource specs", cmd_discover_models, setup_discover_models)

    def setup_models(p):
        p.add_argument("--url", "-u", default=None,
                       help="BeigeBox URL (default: http://localhost:1337)")
        p.add_argument("--search", "-s", default=None,
                       help="Substring filter on id and name (case-insensitive)")
        p.add_argument("--sort", default="id",
                       help="Sort by: id | name | context | prompt | comp (default: id)")
        p.add_argument("--limit", "-n", type=int, default=None,
                       help="Show only the first N rows after sort/filter")
        p.add_argument("--json", action="store_true",
                       help="Output raw JSON instead of a table")

    _add_command(sub, ["models", "mdl", "ls-models"],
                 "Pull the OpenRouter catalog (id/context/pricing)", cmd_models, setup_models)

    def setup_rankings(p):
        p.add_argument("--url", "-u", default=None,
                       help="BeigeBox URL (default: http://localhost:1337)")
        p.add_argument("--track", default="both",
                       help="Which board: agentic | coding | both (default: both)")
        p.add_argument("--top", "-n", type=int, default=None,
                       help="Show only the first N entries (default: 15)")
        p.add_argument("--json", action="store_true",
                       help="Output raw JSON instead of a table")

    _add_command(sub, ["rankings", "rank", "top"],
                 "Top agentic + coding model rankings (Artificial Analysis)",
                 cmd_rankings, setup_rankings)

    def setup_deployment_plan(p):
        p.add_argument("--url", "-u", default=None,
                       help="BeigeBox URL (default: http://localhost:1337)")

    _add_command(sub, ["deployment-plan", "capacity"],
                 "Print VRAM/RAM capacity plan and OOM risk flags", cmd_deployment_plan, setup_deployment_plan)

    # bench / benchmark / speedtest
    def setup_bench(p):
        p.add_argument("--model", "-m", action="append", default=None,
                       help="Model to benchmark (can specify multiple times)")
        p.add_argument("--ollama-url", "-u", default=None,
                       help="Ollama URL (default: from config, e.g. http://host.docker.internal:11434)")
        p.add_argument("--num-runs", "-n", type=int, default=None,
                       help=f"Measured runs per model (default: 5)")
        p.add_argument("--num-predict", type=int, default=None,
                       help=f"Tokens to generate per run (default: 120)")
        p.add_argument("--prompt", default=None,
                       help="Custom prompt (default: built-in ML explanation prompt)")
        p.add_argument("--output", "-o", default=None,
                       help="Write JSON results to this file")
    _add_command(sub, ["bench", "benchmark", "speedtest"],
                 "Direct-to-Ollama speed benchmark (bypasses proxy)", cmd_bench, setup_bench)

    # experiment / exp
    def setup_experiment(p):
        p.add_argument("--opportunity", "-o", default=None,
                       help="Opportunity ID to run (e.g. position_sensitivity)")
        p.add_argument("--list", "-l", action="store_true",
                       help="List all available opportunity IDs")
        p.add_argument("--weight-profile", "-w", default="general",
                       choices=["general", "code", "reasoning", "safety"],
                       help="Scoring weight profile (default: general)")
        p.add_argument("--url", "-u", default=None,
                       help="BeigeBox URL (default: http://localhost:1337)")
        p.add_argument("--output", default=None,
                       help="Write full JSON results to this file")
    _add_command(sub, ["experiment", "exp"],
                 "Run a context optimization discovery experiment", cmd_experiment, setup_experiment)

    # quarantine / security / guard
    def setup_quarantine_list(p):
        p.add_argument("--filter", "-f", choices=["recent", "suspicious", "all"], default="all",
                       help="Filter by time (recent=24h) or confidence (suspicious>0.8)")
        p.add_argument("--limit", "-n", type=int, default=100,
                       help="Max results to show")

    _add_command(sub, ["quarantine-list", "qlist"],
                 "List quarantined embeddings", cmd_quarantine_list, setup_quarantine_list)

    def setup_quarantine_review(p):
        p.add_argument("id", type=int, help="Quarantine record ID to review")

    _add_command(sub, ["quarantine-review", "qreview"],
                 "Show details for a quarantine record", cmd_quarantine_review, setup_quarantine_review)

    _add_command(sub, ["quarantine-stats", "qstats"],
                 "Show quarantine statistics", cmd_quarantine_stats)

    def setup_quarantine_purge(p):
        p.add_argument("--days", "-d", type=int, default=30,
                       help="Delete records older than N days")
        p.add_argument("--dry-run", action="store_true",
                       help="Preview what would be deleted without deleting")

    _add_command(sub, ["quarantine-purge", "qpurge"],
                 "Purge old quarantine records", cmd_quarantine_purge, setup_quarantine_purge)

    args = parser.parse_args()
    if not args.command:
        cmd_tone(args)
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
