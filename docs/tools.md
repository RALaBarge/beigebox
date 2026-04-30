# Tools & Integrations

BeigeBox supports multiple tool systems for extending functionality.

## Chrome DevTools Protocol (CDP)

Real browser automation via Chromium:

```bash
docker compose --profile cdp up -d
```

Available to operator agent:
- `navigate(url)` — Go to URL
- `screenshot()` — Capture page
- `click(selector)` — Click element
- `type(selector, text)` — Type text
- `scroll(dy)` — Scroll page
- `eval(code)` — Execute JavaScript
- `list_tabs()` — Show open tabs

Example:

```python
result = operator.run(
    task="Go to google.com, search for 'AI news', and summarize the results",
    context={}
)
```

Enable in `config.yaml`:

```yaml
tools:
  cdp:
    enabled: true
    url: http://chrome:9222
```

## Plugins

Drop a `.py` in `plugins/` with a `*Tool` class:

```python
# plugins/my_calc.py
class CalcTool:
    """Simple calculator."""

    def run(self, expr: str) -> str:
        return str(eval(expr))
```

Auto-registers at startup. No config needed.

Usage from any MCP client:
```
{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"my_calc","arguments":{"input":"2 ** 8"}}}
```

Or — since this is a `*Tool` class with a string-in / string-out `run` method —
the calling client's agent loop can pick it directly off the `tools/list` surface
served at `POST /mcp`.

## MCP Server

BeigeBox exposes an MCP server for resource/tool discovery:

```bash
# In Claude or other MCP client:
mcp server start \
  --server-url http://localhost:1337/mcp \
  --name beigebox
```

Exposes:
- All configured models
- Available tools (plugins, CDP, document search)
- System metrics

See `beigebox/mcp_server.py` for implementation.

## Document Search (RAG)

Index and search documents:

```bash
# Index documents in a directory
beigebox build-centroids --doc-path /path/to/docs

# Search
curl "http://localhost:1337/api/v1/document-search?q=prompt+engineering&limit=5"
```

Uses ChromaDB embeddings. Enable in config:

```yaml
feature_flags:
  rag_enabled: true
```

## Hooks (Event-driven code)

Custom scripts triggered on events. Drop shell scripts in `hooks/`:

```bash
#!/bin/bash
# hooks/on_request_complete.sh
echo "Request completed in $ELAPSED_MS ms"
```

Available events:
- `on_request_start`
- `on_request_complete`
- `on_tool_start`
- `on_tool_complete`
- `on_error`

## WASM Modules

Post-processing transforms. Drop a compiled `.wasm` in `wasm_modules/`:

```bash
# Example: strips markdown wrapper from responses
wasm_modules/output_normalizer/
```

Enable in config:

```yaml
wasm:
  enabled: true
  module: output_normalizer
```

---

See [Architecture](architecture.md#plugins--extensibility) for details.
