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

__version__ = "0.9.0"

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
    print(f"  Backend: {cfg['backend']['url']}")
    print(f"  Model: {cfg['backend']['default_model']}")
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

def cmd_jack(args):
    """Launch the BeigeBox TUI console."""
    from beigebox.tui.app import BeigeBoxApp
    app = BeigeBoxApp()
    app.run()

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
        # Check if any existing model matches (ignoring tag specifics)
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

    url = args.url or "http://localhost:8000"
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
    from beigebox.config import get_config
    from beigebox.storage.vector_store import VectorStore

    cfg = get_config()
    store = VectorStore(
        chroma_path=cfg["storage"]["chroma_path"],
        embedding_model=cfg["embedding"]["model"],
        embedding_url=cfg["embedding"]["backend_url"],
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
    store = SQLiteStore(cfg["storage"]["sqlite_path"])
    stats = store.get_stats()

    print(f"  📼 Database: {cfg['storage']['sqlite_path']}")
    print(f"  💬 Conversations: {stats['conversations']} | Messages: {stats['messages']}")

    data = store.export_all_json()
    indent = 2 if args.pretty else None

    with open(args.output, "w") as f:
        json.dump(data, f, indent=indent, ensure_ascii=False)

    print(f"  📦 Dumped {len(data)} conversations to {args.output}")


def cmd_flash(args):
    """Show stats at a glance."""
    from beigebox.config import get_config
    from beigebox.storage.sqlite_store import SQLiteStore

    cfg = get_config()

    print(BANNER)
    print("  Configuration")
    print(f"  ├─ Backend:   {cfg['backend']['url']}")
    print(f"  ├─ Model:     {cfg['backend']['default_model']}")
    print(f"  ├─ Embedder:  {cfg['embedding']['model']}")
    print(f"  ├─ SQLite:    {cfg['storage']['sqlite_path']}")
    print(f"  ├─ ChromaDB:  {cfg['storage']['chroma_path']}")
    print(f"  └─ Logging:   {cfg['storage'].get('log_conversations', True)}")

    try:
        store = SQLiteStore(cfg["storage"]["sqlite_path"])
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
    # Falls back silently if DB doesn't exist or cost_tracking is disabled.
    days = getattr(args, "days", 30)
    try:
        from beigebox.costs import CostTracker
        cost_store = SQLiteStore(cfg["storage"]["sqlite_path"])
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
        perf_store = SQLiteStore(cfg["storage"]["sqlite_path"])
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


def cmd_operator(args):
    """
    Launch the BeigeBox Operator agent — interactive REPL or single query.
    """
    from beigebox.config import get_config
    from beigebox.storage.vector_store import VectorStore
    from beigebox.agents.operator import Operator
    print(BANNER)
    print("  Operator online. Type 'exit' or Ctrl-C to disconnect.\\n")
    cfg = get_config()
    # Stand up the vector store for semantic search
    try:
        vector_store = VectorStore(
            chroma_path=cfg["storage"]["chroma_path"],
            embedding_model=cfg["embedding"]["model"],
            embedding_url=cfg["embedding"]["backend_url"],
        )
    except Exception as e:
        print(f"  ⚠ Vector store unavailable: {e}")
        vector_store = None
    try:
        op = Operator(vector_store=vector_store)
    except Exception as e:
        print(f"  ✗ Failed to initialize Operator: {e}")
        print("    Make sure Ollama is running and a model is configured.")
        return
    # Single-shot mode
    if args.query:
        question = " ".join(args.query)
        print(f"  ▶ {question}\\n")
        answer = op.run(question)
        print(f"\\n  ◀ {answer}\\n")
        return
    # REPL mode
    print("  Tools available:")
    for tool in op.tools:
        print(f"    ⚡ {tool.name}")
    print()
    try:
        while True:
            try:
                question = input("  op> ").strip()
            except EOFError:
                break
            if not question:
                continue
            if question.lower() in ("exit", "quit", "q", "disconnect"):
                print("  [line disconnected]")
                break
            print()
            answer = op.run(question)
            print(f"\\n  ◀ {answer}\\n")
    except KeyboardInterrupt:
        print("\\n  [line disconnected]")


def cmd_tone(args):
    """Print the banner."""
    print(BANNER)


def cmd_build_centroids(args):
    """Build embedding classifier centroids from seed prompts."""
    from beigebox.agents.embedding_classifier import EmbeddingClassifier

    print(BANNER)
    print("  Building embedding classifier centroids...")
    print()

    classifier = EmbeddingClassifier()
    success = classifier.build_centroids()

    if success:
        print("  ✓  Centroids built successfully")
        print("     The embedding classifier is now ready for fast routing.")
    else:
        print("  ✗  Failed to build centroids")
        print("     Make sure Ollama is running with the embedding model loaded.")


# ---------------------------------------------------------------------------
# Parser with aliases
# ---------------------------------------------------------------------------

def _add_command(subparsers, names, help_text, func, setup_fn=None):
    """Register a command under multiple names (phreaker + standard)."""
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
        p.add_argument("--role", "-r", choices=["user", "assistant"], default=None, help="Filter by role")
        p.add_argument("--no-follow", action="store_true", help="Don't follow, just show last entries")
        p.add_argument("--raw", action="store_true", help="Raw JSONL output, no formatting")

    _add_command(sub, ["tap", "log", "tail", "watch"],
                 "Live wiretap — watch conversations on the wire", cmd_tap, setup_tap)

    # ring / status / ping / health
    def setup_ring(p):
        p.add_argument("--url", "-u", default=None, help="BeigeBox URL (default: http://localhost:8000)")

    _add_command(sub, ["ring", "status", "ping", "health"],
                 "Ping a running BeigeBox instance", cmd_ring, setup_ring)

    # sweep / search / find / query
    def setup_sweep(p):
        p.add_argument("query", nargs="+", help="Search query")
        p.add_argument("--results", "-n", type=int, default=5, help="Number of results")
        p.add_argument("--role", "-r", choices=["user", "assistant"], default=None, help="Filter by role")

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

    # build-centroids
    _add_command(sub, ["build-centroids", "centroids"],
                 "Build embedding classifier centroids from seed prompts", cmd_build_centroids)

    # jack / console / tui
    _add_command(sub, ["jack", "console", "tui"], "Launch the interactive TUI console", cmd_jack)

    # operator / op
    def setup_operator(p):
        p.add_argument("query", nargs="*", help="Question to ask (omit for interactive REPL)")
    _add_command(sub, ["operator", "op"],
                 "Launch the Operator agent (web, data, shell)", cmd_operator, setup_operator)
    
    args = parser.parse_args()
    if not args.command:
        cmd_tone(args)
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
