# BeigeBox Plugins

Drop any `.py` file here and it's automatically registered as a tool at startup.
No code changes required anywhere else.

## Plugin contract

A plugin file needs exactly one class ending in `Tool` with a `.run(self, input: str) -> str` method.

```python
# plugins/my_tool.py
PLUGIN_NAME = "my_tool"   # registry key (optional — defaults to snake_case class name)

class MyTool:
    def __init__(self):
        pass

    def run(self, query: str) -> str:
        return "Hello from my plugin"
```

## Enabling plugins

In `config.yaml`:

```yaml
tools:
  plugins:
    enabled: true          # master switch
    my_tool:
      enabled: true        # per-plugin flag (absent = enabled by default)
```

## Bundled examples

| File | Tool name | What it does |
|------|-----------|--------------|
| `dice.py` | `dice` | XdY dice rolls, drop-lowest, coin flip |
| `units.py` | `units` | Length, weight, data size, temperature conversion |
| `wiretap_summary.py` | `wiretap_summary` | Summarises recent proxy traffic from wire.jsonl |

## Notes

- Plugin names cannot shadow built-in tools (`web_search`, `calculator`, etc.)
- Files starting with `_` are ignored
- Constructor `__init__` runs at startup — keep it fast; defer heavy work to `run()`
- Plugins have full access to `beigebox.config.get_config()` for settings
