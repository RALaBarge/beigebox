"""``/v1/models`` aggregation + advertise-mode rewrite.

Lifted out of ``Proxy`` during the G-series refactor. The orchestrator
calls ``list_models`` which fetches local Ollama models, merges in
router-backend models (e.g. pinned OpenRouter models, vLLM, …), and
optionally rewrites the response to advertise BeigeBox.
"""
from __future__ import annotations

import fnmatch
import logging

import httpx

from beigebox.config import get_runtime_config

logger = logging.getLogger(__name__)


async def list_models(cfg: dict, backend_url: str, backend_router) -> dict:
    """Forward ``/v1/models`` request to backend(s), rewriting names if configured.

    Always fetches from the direct Ollama backend URL first so local
    models are visible regardless of whether multi-backend routing is
    enabled. Router backends (e.g. pinned OpenRouter models) are merged
    on top.
    """
    # ``seen`` deduplicates by model id so a model available on both Ollama
    # and a router backend (e.g. an OR alias for a local model) only
    # appears once. Ollama wins because it's fetched first.
    seen: set[str] = set()
    all_models: list[dict] = []

    # Always include local Ollama models
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{backend_url}/v1/models")
            resp.raise_for_status()

            # Check if local model filtering is enabled (with runtime override support)
            rt_cfg = get_runtime_config()
            local_cfg = cfg.get("local_models", {})
            # Runtime config takes precedence
            filter_enabled = rt_cfg.get("local_models_filter_enabled", local_cfg.get("filter_enabled", False))
            allowed_models = rt_cfg.get("local_models_allowed_models", local_cfg.get("allowed_models", []))

            for m in resp.json().get("data", []):
                mid = m.get("id") or m.get("name") or ""
                if not mid or mid in seen:
                    continue

                # Apply local model filter if enabled
                if filter_enabled and allowed_models:
                    if not any(fnmatch.fnmatch(mid, pattern) for pattern in allowed_models):
                        continue

                seen.add(mid)
                all_models.append(m)
    except Exception:
        pass

    # Merge in router backends (pinned OR models, vLLM, etc.)
    if backend_router:
        router_data = await backend_router.list_all_models()
        for m in router_data.get("data", []):
            mid = m.get("id") or m.get("name") or ""
            if mid and mid not in seen:
                seen.add(mid)
                all_models.append(m)

    data = {"object": "list", "data": all_models}
    return transform_model_names(data, cfg)


def transform_model_names(data: dict, cfg: dict) -> dict:
    """Rewrite model names in the response based on config.

    Supports two modes:
      1. ``advertise``: prepend ``"beigebox:"`` to all model names
      2. ``hidden``: don't advertise BeigeBox's presence (default)
    """
    advert_cfg = cfg.get("model_advertising", {})
    mode = advert_cfg.get("mode", "hidden")  # "advertise" or "hidden"
    prefix = advert_cfg.get("prefix", "beigebox:")

    if mode == "hidden":
        # Transparent mode — pass model list through unchanged.
        # Frontends will see the backend's real model names.
        return data

    # Mode: advertise — prepend prefix to all models
    if mode == "advertise" and "data" in data:
        try:
            for model in data.get("data", []):
                if "name" in model:
                    model["name"] = f"{prefix}{model['name']}"
                if "model" in model:
                    model["model"] = f"{prefix}{model['model']}"
        except (TypeError, KeyError):
            # If structure doesn't match, return unchanged
            logger.warning("Could not rewrite model names — unexpected response structure")

    return data
