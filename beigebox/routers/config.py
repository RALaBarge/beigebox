"""Configuration + system context endpoints.

Extracted from beigebox/main.py (B-6). Heavy config-read/write surface
plus system-context document and WASM reload.

Endpoints:
- /api/v1/info — feature flags + version + backend
- /api/v1/config (GET) — full merged config + runtime overrides (~180 LOC)
- /api/v1/config (POST) — save runtime overrides + apply hot changes (~145 LOC)
- /api/v1/wasm/reload — admin-gated; reload WASM modules from disk
- /api/v1/system-context (GET + POST) — system_context.md content
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from beigebox import __version__ as _BB_VERSION
from beigebox.config import (
    get_config,
    get_effective_backends_config,
    get_primary_backend_url,
    get_runtime_config,
    get_storage_paths,
    update_runtime_config,
)
from beigebox.constants import (
    DEFAULT_AGENTIC_MODEL,
    DEFAULT_MODEL,
    DEFAULT_ROUTING_MODEL,
    DEFAULT_SUMMARY_MODEL,
)
from beigebox.routers._shared import _require_admin
from beigebox.state import get_state


logger = logging.getLogger(__name__)


router = APIRouter()


# ── /api/v1/info ─────────────────────────────────────────────────────────

@router.get("/api/v1/info")
async def api_info():
    """System info — what features are available."""
    cfg = get_config()
    _st = get_state()
    return JSONResponse({
        "version": _BB_VERSION,
        "name": "BeigeBox",
        "description": "Transparent Pythonic LLM Proxy",
        "server": {
            "host": cfg["server"].get("host", "0.0.0.0"),
            "port": cfg["server"].get("port", 8000),
        },
        "backend": {
            "url": get_primary_backend_url(cfg),
            "default_model": get_runtime_config().get("default_model") or cfg.get("models", {}).get("default", ""),
        },
        "features": {
            "storage": _st.conversations is not None and _st.vector_store is not None,
            "tools": _st.tool_registry is not None and cfg.get("tools", {}).get("enabled", False),
            "hooks": _st.hook_manager is not None,
            "operator": True,
        },
        "model_advertising": cfg.get("model_advertising", {}).get("mode", "hidden"),
    })


# ── /api/v1/config (GET) ─────────────────────────────────────────────────

@router.get("/api/v1/config")
async def api_config():
    """Full configuration — all config.yaml settings plus runtime overrides.

    Runtime keys (from runtime_config.yaml) take precedence where they exist.
    Safe to expose: no secrets, API keys are redacted.
    """
    cfg = get_config()
    rt = get_runtime_config()

    return JSONResponse({
        "features": {
            "backends":              rt.get("features_backends", cfg.get("features", {}).get("backends", cfg.get("backends_enabled", False))),
            "harness":               rt.get("features_harness", cfg.get("features", {}).get("harness", cfg.get("harness", {}).get("enabled", True))),
            "tools":                 rt.get("features_tools", cfg.get("features", {}).get("tools", cfg.get("tools", {}).get("enabled", False))),
            "cost_tracking":         rt.get("features_cost_tracking", cfg.get("features", {}).get("cost_tracking", cfg.get("cost_tracking", {}).get("enabled", False))),
            "conversation_replay":   rt.get("features_conversation_replay", cfg.get("features", {}).get("conversation_replay", cfg.get("conversation_replay", {}).get("enabled", False))),
            "auto_summarization":    rt.get("features_auto_summarization", cfg.get("features", {}).get("auto_summarization", cfg.get("auto_summarization", {}).get("enabled", False))),
            "aggressive_summarization": rt.get("features_aggressive_summarization", cfg.get("features", {}).get("aggressive_summarization", cfg.get("aggressive_summarization", {}).get("enabled", False))),
            "system_context":        rt.get("features_system_context", cfg.get("features", {}).get("system_context", cfg.get("system_context", {}).get("enabled", False))),
            "wiretap":               rt.get("features_wiretap", cfg.get("features", {}).get("wiretap", cfg.get("wiretap", {}).get("enabled", True))),
            "payload_log":           rt.get("payload_log_enabled", False),
            "wasm":                  rt.get("features_wasm", cfg.get("features", {}).get("wasm", cfg.get("wasm", {}).get("enabled", False))),
            "guardrails":            rt.get("features_guardrails", cfg.get("features", {}).get("guardrails", cfg.get("guardrails", {}).get("enabled", False))),
            "hooks":                 rt.get("features_hooks", cfg.get("features", {}).get("hooks", cfg.get("hooks", {}).get("enabled", False))),
        },
        "backend": {
            "url":           cfg.get("backend", {}).get("url", ""),
            "default_model": rt.get("default_model") or cfg.get("backend", {}).get("default_model", ""),
            "timeout":       cfg.get("backend", {}).get("timeout", 120),
        },
        "models": {
            "default":       rt.get("models_default") or cfg.get("models", {}).get("default", DEFAULT_MODEL),
            "routing":       rt.get("models_routing") or cfg.get("models", {}).get("profiles", {}).get("routing", DEFAULT_ROUTING_MODEL),
            "agentic":       rt.get("models_agentic") or cfg.get("models", {}).get("profiles", {}).get("agentic", DEFAULT_AGENTIC_MODEL),
            "summary":       rt.get("models_summary") or cfg.get("models", {}).get("profiles", {}).get("summary", DEFAULT_SUMMARY_MODEL),
        },
        "server": {
            "host": cfg.get("server", {}).get("host", "0.0.0.0"),
            "port": cfg.get("server", {}).get("port", 8000),
        },
        "embedding": {
            "model":       cfg.get("embedding", {}).get("model", ""),
            "backend_url": cfg.get("embedding", {}).get("backend_url", ""),
        },
        "storage": {
            "path":               get_storage_paths(cfg)[0],
            "vector_store_path":  get_storage_paths(cfg)[1],
            "log_conversations":  rt.get("log_conversations", cfg.get("storage", {}).get("log_conversations", True)),
        },
        "tools": {
            "enabled":      rt.get("tools_enabled", cfg.get("tools", {}).get("enabled", False)),
            "web_search":   cfg.get("tools", {}).get("web_search", {}),
            "web_scraper":  cfg.get("tools", {}).get("web_scraper", {}),
            "calculator":   cfg.get("tools", {}).get("calculator", {}),
            "datetime":     cfg.get("tools", {}).get("datetime", {}),
            "system_info":  cfg.get("tools", {}).get("system_info", {}),
            "memory":       cfg.get("tools", {}).get("memory", {}),
            "browserbox":   cfg.get("tools", {}).get("browserbox", {}),
        },
        "local_models": {
            "filter_enabled": cfg.get("local_models", {}).get("filter_enabled", False),
            "allowed_models": cfg.get("local_models", {}).get("allowed_models", []),
        },
        "model_advertising": cfg.get("model_advertising", {}),
        "backends_enabled": get_effective_backends_config()[0],
        "backends": [
            {k: ("***" if "key" in k.lower() and v else v)
             for k, v in b.items()}
            for b in get_effective_backends_config()[1]
        ],
        "cost_tracking": {
            **cfg.get("cost_tracking", {}),
            "enabled": rt.get("cost_tracking_enabled", cfg.get("cost_tracking", {}).get("enabled", False)),
            "track_openrouter": rt.get("cost_track_openrouter", cfg.get("cost_tracking", {}).get("track_openrouter", True)),
            "track_local": rt.get("cost_track_local", cfg.get("cost_tracking", {}).get("track_local", False)),
        },
        "harness": {
            "enabled":      rt.get("harness_enabled", cfg.get("harness", {}).get("enabled", True)),
            "ralph_enabled": rt.get("ralph_enabled", cfg.get("harness", {}).get("ralph_enabled", False)),
        },
        "conversation_replay": {
            "enabled": rt.get("conversation_replay_enabled", cfg.get("conversation_replay", {}).get("enabled", False)),
        },
        "auto_summarization": {
            **cfg.get("auto_summarization", {}),
            "enabled": rt.get("auto_summarization_enabled", cfg.get("auto_summarization", {}).get("enabled", False)),
            "token_budget": rt.get("auto_token_budget", cfg.get("auto_summarization", {}).get("token_budget", 3000)),
            "summary_model": rt.get("auto_summary_model", cfg.get("auto_summarization", {}).get("summary_model", "")),
            "keep_last": rt.get("auto_keep_last", cfg.get("auto_summarization", {}).get("keep_last", 4)),
        },
        "aggressive_summarization": {
            **cfg.get("aggressive_summarization", {}),
            "enabled":   rt.get("agg_sum_enabled",   cfg.get("aggressive_summarization", {}).get("enabled", False)),
            "keep_last": rt.get("agg_sum_keep_last",  cfg.get("aggressive_summarization", {}).get("keep_last", 2)),
            "model":     rt.get("agg_sum_model",      cfg.get("aggressive_summarization", {}).get("model", "")),
        },
        "routing": {
            "session_cache": {
                "ttl_seconds": rt.get("tier1_ttl", cfg.get("routing", {}).get("session_cache", {}).get("ttl_seconds", 3600)),
            },
            "classifier": {
                "enabled": rt.get("features_classifier", cfg.get("features", {}).get("classifier", cfg.get("classifier", {}).get("enabled", True))),
                "centroid_rebuild_interval": rt.get("classifier_rebuild_interval", cfg.get("routing", {}).get("classifier", {}).get("centroid_rebuild_interval", cfg.get("classifier", {}).get("centroid_rebuild_interval", 3600))),
            },
            "allow_openrouter_for_plain_models": rt.get("allow_openrouter_for_plain_models", cfg.get("routing", {}).get("allow_openrouter_for_plain_models", False)),
        },
        "logging": {
            "level": rt.get("log_level") or cfg.get("logging", {}).get("level", "INFO"),
            "file":  cfg.get("logging", {}).get("file", ""),
        },
        "wiretap": cfg.get("wiretap", {}),
        "payload_log": {
            "enabled": rt.get("payload_log_enabled", False),
            "path":    cfg.get("payload_log", {}).get("path", "./data/payload.jsonl"),
        },
        "hooks": cfg.get("hooks", []),
        "web_ui": {
            "vi_mode":      rt.get("web_ui_vi_mode", False),
            "palette":      rt.get("web_ui_palette", "default"),
        },
        "runtime": {
            "system_prompt_prefix": rt.get("system_prompt_prefix", ""),
            "tools_disabled":       rt.get("tools_disabled", []),
        },
        "system_context": {
            "enabled": rt.get("system_context_enabled", cfg.get("system_context", {}).get("enabled", False)),
            "path":    cfg.get("system_context", {}).get("path", "./system_context.md"),
        },
        "wasm": {
            "enabled":        rt.get("wasm_enabled", get_state().proxy.wasm_runtime.enabled if get_state().proxy else cfg.get("wasm", {}).get("enabled", False)),
            "timeout_ms":     rt.get("wasm_timeout_ms", cfg.get("wasm", {}).get("timeout_ms", 500)),
            "modules":        get_state().proxy.wasm_runtime.list_modules() if get_state().proxy else [],
            "default_module": rt.get("wasm_default_module") or (get_state().proxy.wasm_runtime.default_module if get_state().proxy else cfg.get("wasm", {}).get("default_module", "")),
            "modules_cfg": {
                name: {
                    "description": mcfg.get("description", ""),
                    "enabled":     mcfg.get("enabled", True),
                    "path":        mcfg.get("path", ""),
                }
                for name, mcfg in cfg.get("wasm", {}).get("modules", {}).items()
            },
        },
        "generation": {
            "temperature":    rt.get("gen_temperature"),
            "top_p":          rt.get("gen_top_p"),
            "top_k":          rt.get("gen_top_k"),
            "num_ctx":        rt.get("gen_num_ctx"),
            "repeat_penalty": rt.get("gen_repeat_penalty"),
            "max_tokens":     rt.get("gen_max_tokens"),
            "seed":           rt.get("gen_seed"),
            "stop":           rt.get("gen_stop"),
            "force":          rt.get("gen_force", False),
        },
    })


# ── /api/v1/config (POST) ────────────────────────────────────────────────

@router.post("/api/v1/config")
async def api_config_save(request: Request):
    """Save runtime-adjustable settings to runtime_config.yaml.

    All keys are hot-reloaded — no restart required.

    When adding a new editable field, you must update THREE places:
    1. `allowed` dict below — add "your_key": "your_key"
    2. GET /api/v1/config above — add rt.get("your_key", cfg.get(..., default))
    3. index.html saveConfig() — add to bools/strings/numbers list
    """
    try:
        body = await request.json()
    except Exception as e:
        logger.debug("Invalid JSON body: %s", str(e)[:200])
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    updated = []
    changed = []
    errors = []

    rt_before = get_runtime_config()

    allowed = {
        # Web UI
        "web_ui_vi_mode": "web_ui_vi_mode",
        "web_ui_palette": "web_ui_palette",
        # Features (Phase 1)
        "features_backends": "features_backends",
        "features_harness": "features_harness",
        "features_tools": "features_tools",
        "features_cost_tracking": "features_cost_tracking",
        "features_conversation_replay": "features_conversation_replay",
        "features_auto_summarization": "features_auto_summarization",
        "features_system_context": "features_system_context",
        "features_wiretap": "features_wiretap",
        "features_wasm": "features_wasm",
        "features_guardrails": "features_guardrails",
        "features_hooks": "features_hooks",
        # Models Registry (Phase 2)
        "models_default": "models_default",
        "models_routing": "models_routing",
        "models_agentic": "models_agentic",
        "models_summary": "models_summary",
        "default_model": "default_model",
        "tools_enabled": "tools_enabled",
        "log_conversations": "log_conversations",
        "log_level": "log_level",
        "system_prompt_prefix": "system_prompt_prefix",
        "tools_disabled": "tools_disabled",
        "cost_tracking_enabled": "cost_tracking_enabled",
        "cost_track_openrouter": "cost_track_openrouter",
        "cost_track_local": "cost_track_local",
        "harness_enabled": "harness_enabled",
        "conversation_replay_enabled": "conversation_replay_enabled",
        "auto_summarization_enabled": "auto_summarization_enabled",
        "auto_token_budget": "auto_token_budget",
        "auto_summary_model": "auto_summary_model",
        "auto_keep_last": "auto_keep_last",
        "features_aggressive_summarization": "features_aggressive_summarization",
        "agg_sum_enabled": "agg_sum_enabled",
        "agg_sum_keep_last": "agg_sum_keep_last",
        "agg_sum_model": "agg_sum_model",
        "system_context_enabled": "system_context_enabled",
        "local_models_filter_enabled": "local_models_filter_enabled",
        "local_models_allowed_models": "local_models_allowed_models",
        "gen_temperature": "gen_temperature",
        "gen_top_p": "gen_top_p",
        "gen_top_k": "gen_top_k",
        "gen_num_ctx": "gen_num_ctx",
        "gen_repeat_penalty": "gen_repeat_penalty",
        "gen_max_tokens": "gen_max_tokens",
        "gen_seed": "gen_seed",
        "gen_stop": "gen_stop",
        "gen_force": "gen_force",
        "wasm_default_module": "wasm_default_module",
        "wasm_enabled": "wasm_enabled",
        "wasm_timeout_ms": "wasm_timeout_ms",
        "allow_openrouter_for_plain_models": "allow_openrouter_for_plain_models",
        "backends_enabled": "backends_enabled",
        "browserbox_enabled": "browserbox_enabled",
        "browserbox_ws_url": "browserbox_ws_url",
        "browserbox_timeout": "browserbox_timeout",
        "payload_log_enabled": "payload_log_enabled",
    }

    for key, rt_key in allowed.items():
        if key in body:
            new_val = body[key]
            ok = update_runtime_config(rt_key, new_val)
            if ok:
                updated.append(key)
                if new_val != rt_before.get(rt_key):
                    changed.append(key)
            else:
                errors.append(key)

    rt = get_runtime_config()
    _st = get_state()

    if "default_model" in updated and _st.proxy:
        _st.proxy.default_model = rt.get("default_model", _st.proxy.default_model)

    if "log_conversations" in updated and _st.proxy:
        _st.proxy.log_enabled = rt.get("log_conversations", _st.proxy.log_enabled)

    if "wasm_default_module" in updated and _st.proxy:
        _st.proxy.wasm_runtime.default_module = rt.get("wasm_default_module", "")

    if "wasm_enabled" in updated and _st.proxy:
        new_wasm_enabled = rt.get("wasm_enabled")
        if new_wasm_enabled is True:
            _st.proxy.wasm_runtime.enable(get_config())
        elif new_wasm_enabled is False:
            _st.proxy.wasm_runtime.disable()

    if errors:
        return JSONResponse({"saved": updated, "changed": changed, "errors": errors}, status_code=207)
    return JSONResponse({"saved": updated, "changed": changed, "ok": True})


# ── WASM reload ──────────────────────────────────────────────────────────

@router.post("/api/v1/wasm/reload")
async def api_wasm_reload(request: Request):
    """Reload WASM modules from disk without restarting BeigeBox.

    Admin-only: a malicious caller with file-write access could otherwise
    swap a module file then trigger reload to execute it.
    """
    if (denied := _require_admin(request)) is not None:
        return denied
    _st = get_state()
    if not _st.proxy:
        return JSONResponse({"error": "proxy not initialized"}, status_code=503)
    loaded = _st.proxy.wasm_runtime.reload()
    return JSONResponse({"ok": True, "modules": loaded})


# ── System context document ──────────────────────────────────────────────

@router.get("/api/v1/system-context")
async def api_get_system_context():
    """Return the current contents of system_context.md."""
    cfg = get_config()
    from beigebox.system_context import read_context_file
    content = read_context_file(cfg)
    rt = get_runtime_config()
    sc_cfg = cfg.get("system_context", {})
    enabled = rt.get("system_context_enabled", sc_cfg.get("enabled", False))
    return JSONResponse({
        "content": content,
        "enabled": enabled,
        "path": sc_cfg.get("path", "./system_context.md"),
        "length": len(content),
    })


@router.post("/api/v1/system-context")
async def api_set_system_context(request: Request):
    """Write new contents to system_context.md.

    Hot-reloads immediately — next proxied request picks it up.
    """
    try:
        body = await request.json()
    except Exception as e:
        logger.debug("Invalid JSON body: %s", str(e)[:200])
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    content = body.get("content", "")
    if not isinstance(content, str):
        return JSONResponse({"error": "content must be a string"}, status_code=400)

    cfg = get_config()
    from beigebox.system_context import write_context_file
    ok = write_context_file(cfg, content)
    if ok:
        return JSONResponse({"ok": True, "length": len(content)})
    return JSONResponse({"error": "failed to write system_context.md"}, status_code=500)
