"""Body-mutation pipeline stages applied just before the backend call.

Each function takes the request body plus the static config (and reads
the runtime config itself when it needs hot-reloaded state) and returns
the (potentially) mutated body. They run in fixed order in
``Proxy._run_request_pipeline``:

    inject_generation_params  →  inject_model_options  →  apply_window_config

The split mirrors the priority hierarchy: runtime config → per-model
options → per-pane window config (highest wins).

Lifted out of ``Proxy`` during the G-series refactor — none of the
functions touched ``self`` aside from ``self.cfg`` so they're plain
functions now.
"""
from __future__ import annotations

import logging

from beigebox.config import get_runtime_config

logger = logging.getLogger(__name__)


def inject_generation_params(body: dict, cfg: dict) -> dict:
    """Inject runtime generation parameters into the request body.

    Reads from runtime_config.yaml so changes apply immediately without
    restart. Only injects keys that are explicitly set (non-None).
    Frontend values are NOT overridden — if the frontend already sent
    ``temperature``, we leave it alone unless the runtime config is set
    to force it.

    Supported keys (all optional, all hot-reloaded):
        gen_temperature      float   0.0–2.0
        gen_top_p            float   0.0–1.0
        gen_top_k            int     e.g. 40
        gen_num_ctx          int     context window tokens, e.g. 4096
        gen_repeat_penalty   float   e.g. 1.1
        gen_max_tokens       int     max output tokens
        gen_seed             int     for reproducibility
        gen_stop             list    stop sequences
        gen_force            bool    if true, override even if frontend sent value
    """
    rt = get_runtime_config()

    # Normalize missing/empty model to the configured default so the router
    # always has a non-empty model name to match against backend model lists.
    if not body.get("model"):
        default = (
            rt.get("default_model")
            or cfg.get("backend", {}).get("default_model", "")
        )
        if default:
            body["model"] = default
            logger.debug("model not set by client — defaulting to '%s'", default)

    force = rt.get("gen_force", False)

    param_map = {
        "gen_temperature":    "temperature",
        "gen_top_p":          "top_p",
        "gen_top_k":          "top_k",
        "gen_num_ctx":        "num_ctx",
        "gen_repeat_penalty": "repeat_penalty",
        "gen_max_tokens":     "max_tokens",
        "gen_seed":           "seed",
        "gen_stop":           "stop",
    }

    for rt_key, body_key in param_map.items():
        val = rt.get(rt_key)
        if val is None:
            continue
        # Only inject if not already set by the frontend, unless force=true
        if force or body_key not in body or body[body_key] is None:
            body[body_key] = val

    return body


def inject_model_options(body: dict, cfg: dict) -> dict:
    """Inject per-model Ollama options from config and runtime_config.

    Priority (highest wins):
      1. runtime_config model_options  — set via UI, hot-reloaded
      2. config.yaml models.<name>.options  — static, requires restart

    runtime_config structure (flat num_gpu per model)::

        runtime:
          model_options:
            qwen3:4b: 0      # CPU
            mistral:7b: 99   # all GPU layers
            llama2:70b: 20   # partial offload
    """
    model = body.get("model", "")
    if not model:
        return body

    # Layer 1: static config options
    model_cfg = cfg.get("models", {}).get(model, {})
    options = dict(model_cfg.get("options", {}))

    # Layer 2: runtime model_options (num_gpu override per model)
    rt_model_opts = get_runtime_config().get("model_options", {})
    if model in rt_model_opts:
        num_gpu = rt_model_opts[model]
        if num_gpu is not None:
            options["num_gpu"] = int(num_gpu)

    if not options:
        return body
    # Merge: frontend options first, then our config layers on top
    body_opts = dict(body.get("options") or {})
    body_opts.update(options)
    body["options"] = body_opts
    logger.debug("Model options injected for '%s': %s", model, list(options.keys()))
    return body


def apply_window_config(body: dict) -> tuple[dict, bool]:
    """Apply per-pane window config sent by the frontend as ``_window_config``.

    The frontend embeds a ``_window_config`` dict in the request body for
    any pane that has non-default settings. These override all other
    config layers (global runtime config, per-model options) since they
    represent an explicit per-session user choice. The key is stripped
    before the body is forwarded.

    Supported fields (all optional, null/missing = skip):
        temperature, top_p, top_k, num_ctx, max_tokens,
        repeat_penalty, seed  — top-level body params
        num_gpu               — goes into body["options"]["num_gpu"]
        force_reload          — if true, caller should evict model before forwarding
        system_prompt         — handled by the frontend (not re-injected here)

    Returns ``(body, force_reload)`` — ``force_reload`` signals the caller
    to evict the model from Ollama first so it reloads fresh with the new
    options.
    """
    wc = body.pop("_window_config", None)
    if not wc:
        return body, False

    param_map = {
        "temperature":    "temperature",
        "top_p":          "top_p",
        "top_k":          "top_k",
        "num_ctx":        "num_ctx",
        "max_tokens":     "max_tokens",
        "repeat_penalty": "repeat_penalty",
        "seed":           "seed",
    }
    applied = []
    for wc_key, body_key in param_map.items():
        val = wc.get(wc_key)
        if val is not None:
            body[body_key] = val
            applied.append(wc_key)

    num_gpu = wc.get("num_gpu")
    if num_gpu is not None:
        opts = dict(body.get("options") or {})
        opts["num_gpu"] = int(num_gpu)
        body["options"] = opts
        applied.append("num_gpu")

    force_reload = bool(wc.get("force_reload"))

    if applied:
        logger.debug("Window config applied: %s%s", applied, " (force_reload)" if force_reload else "")
    return body, force_reload
