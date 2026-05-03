"""Model preload — fire-and-forget tasks that pin Ollama models in VRAM.

Two coroutines are exposed:

  - ``_preload_model(url, model, label, ...)`` — POST /api/generate with
    ``keep_alive: -1``. Generic ``ollama`` chat-model warmup.
  - ``_preload_embedding_model(cfg)`` — POST /api/embed (embedding-only
    models reject /api/generate with 400).

``schedule_preloads(cfg)`` collects every distinct special-purpose model
(routing/judge, operator/agentic, summary) and schedules them as
fire-and-forget background tasks, staggered 15 s apart so they don't
race for VRAM bandwidth. The function returns the list of scheduled
tasks for tests / introspection — production never awaits them.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from beigebox.config import get_primary_backend_url

logger = logging.getLogger(__name__)


async def _preload_model(
    url: str,
    model: str,
    label: str,
    retries: int = 5,
    base_delay: float = 5.0,
) -> None:
    """
    Pin a model in Ollama's memory at startup.
    Retries with exponential backoff — Ollama may still be loading the
    model from disk when beigebox first starts, so one attempt is never
    enough.
    """
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{url}/api/generate",
                    json={"model": model, "prompt": "", "keep_alive": -1},
                )
                resp.raise_for_status()
            logger.info("%s model '%s' preloaded and pinned", label, model)
            return
        except Exception as e:
            delay = base_delay * (2 ** attempt)
            if attempt < retries - 1:
                logger.warning(
                    "%s preload attempt %d/%d failed (%s) — retrying in %.0fs",
                    label, attempt + 1, retries, e, delay,
                )
                await asyncio.sleep(delay)
            else:
                logger.warning(
                    "%s preload failed after %d attempts: %s",
                    label, retries, e,
                )


async def _preload_embedding_model(cfg: dict) -> None:
    """Pin the embedding model in Ollama's memory at startup."""
    embed_cfg = cfg.get("embedding", {})
    model = embed_cfg.get("model", "")
    url = embed_cfg.get("backend_url") or get_primary_backend_url(cfg)
    if not model:
        return
    # Embedding-only models (e.g. nomic-embed-text) reject /api/generate
    # with 400. Use /api/embed to warm them up instead.
    for attempt in range(5):
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{url}/api/embed",
                    json={"model": model, "input": "warmup", "keep_alive": -1},
                )
                resp.raise_for_status()
            logger.info("Embedding model '%s' preloaded and pinned", model)
            return
        except Exception as e:
            delay = 5.0 * (2 ** attempt)
            if attempt < 4:
                logger.warning(
                    "Embedding preload attempt %d/5 failed (%s) — retrying in %.0fs",
                    attempt + 1, e, delay,
                )
                await asyncio.sleep(delay)
            else:
                logger.warning(
                    "Embedding preload failed after 5 attempts: %s", e
                )


def schedule_preloads(cfg: dict) -> list[asyncio.Task]:
    """
    Collect every distinct special-purpose model (routing/judge,
    operator/agentic, summary) and schedule fire-and-forget warmup tasks.

    Returns the list of tasks — caller does NOT need to await them.
    Server starts accepting requests immediately while models warm up.
    """
    tasks: list[asyncio.Task] = [
        asyncio.create_task(_preload_embedding_model(cfg))
    ]

    # Collect all distinct special-purpose models (judge, operator, summary)
    # and pin them in Ollama at startup so cold-start latency never hits a
    # live request. Stagger by 15s each to avoid VRAM bandwidth contention.
    backend_url = get_primary_backend_url(cfg)
    models_cfg = cfg.get("models", {})
    profiles = models_cfg.get("profiles", {})
    default_model = models_cfg.get("default", "")
    special_models: list[tuple[str, str]] = []  # (model, label)
    seen: set[str] = set()
    for label, key in [
        ("routing/judge", "routing"),
        ("operator/agentic", "agentic"),
        ("summary", "summary"),
    ]:
        m = profiles.get(key) or default_model
        if m and m not in seen:
            seen.add(m)
            special_models.append((m, label))

    async def _staggered_preloads() -> None:
        # Give Ollama 15s head-start before the first special model, then
        # stagger each additional distinct model by 15s so they don't race.
        await asyncio.sleep(15)
        for idx, (model, label) in enumerate(special_models):
            if idx > 0:
                await asyncio.sleep(15)
            await _preload_model(backend_url, model, label)

    tasks.append(asyncio.create_task(_staggered_preloads()))
    return tasks


__all__ = [
    "_preload_model",
    "_preload_embedding_model",
    "schedule_preloads",
]
