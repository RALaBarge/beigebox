"""
Council agent — "council then commander" pattern.

Phase 1: Operator analyzes the query and proposes a council of 2–4 specialist
         models (name, model, task). Returns JSON the caller can let the user edit.

Phase 2: Specialists are dispatched in parallel. Results are yielded as each
         completes via an async generator. The operator then synthesizes all
         outputs into a final answer.

Usage:
    # Phase 1
    council = await propose(query, backend_url, operator_model)
    # → [{"name": "Code Analyst", "model": "qwen2.5-coder:14b", "task": "..."}, ...]

    # Phase 2
    async for event in execute(query, council, backend_url, operator_model):
        # event: {"type": "member_done", "name": ..., "result": ...}
        #        {"type": "synthesis",   "result": ...}
        #        {"type": "error",       "message": ...}
        yield event
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

import httpx

logger = logging.getLogger(__name__)

_PROPOSAL_SYSTEM = """\
You are a strategic coordinator. A user has a query that benefits from multiple
specialist perspectives. Propose a council of 2–4 specialist AI models, each
assigned a distinct angle to analyse.

Available models:
{models}

Respond with ONLY a JSON array — no markdown, no explanation:
[
  {{"name": "short role name", "model": "model_id", "task": "specific aspect to analyse (1-2 sentences)"}},
  ...
]

Rules:
- Use ONLY model IDs from the available list above.
- Each specialist covers a clearly distinct angle.
- Tasks must be concrete and specific to the user's query.
- 2 specialists minimum, 4 maximum.
"""

_SYNTHESIS_SYSTEM = """\
You are a senior analyst. A council of specialists has analysed a user's query
from different angles. Synthesise their findings into a single, coherent,
actionable response.

Integrate the best ideas, note any disagreements, and give a clear answer.
Be concise and direct.
"""

_MEMBER_SYSTEM = """\
You are a specialist analyst. Your role: {role}
Your task: {task}

Analyse the user's query from this specific angle only. Be concise and direct.
"""

_CHAT_TIMEOUT = 180.0


# ── helpers ──────────────────────────────────────────────────────────────────

def _strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _is_thinker(model: str) -> bool:
    return any(t in model.lower() for t in ("qwen3", "r1", "deepseek-r"))


async def _chat(
    backend_url: str,
    model: str,
    messages: list[dict],
    timeout: float = _CHAT_TIMEOUT,
) -> str:
    opts: dict = {"num_ctx": 8192}
    if _is_thinker(model):
        opts["think"] = False

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{backend_url.rstrip('/')}/v1/chat/completions",
            json={
                "model":       model,
                "messages":    messages,
                "stream":      False,
                "temperature": 0.2,
                "options":     opts,
            },
        )
        resp.raise_for_status()
        return _strip_think(resp.json()["choices"][0]["message"]["content"])


async def _fetch_models(backend_url: str) -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{backend_url.rstrip('/')}/v1/models")
            resp.raise_for_status()
            data = resp.json()
            return [m["id"] for m in data.get("data", [])]
    except Exception as e:
        logger.warning("council: could not fetch model list: %s", e)
        return []


def _extract_json_array(text: str) -> list | None:
    text = re.sub(r"```(?:json)?", "", text).strip().strip("`").strip()
    # Try whole thing
    try:
        v = json.loads(text)
        if isinstance(v, list):
            return v
    except json.JSONDecodeError:
        pass
    # Find first [...] block
    depth, start = 0, None
    for i, ch in enumerate(text):
        if ch == "[":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    v = json.loads(text[start:i + 1])
                    if isinstance(v, list):
                        return v
                except json.JSONDecodeError:
                    start = None
    return None


# ── public API ────────────────────────────────────────────────────────────────

async def propose(
    query: str,
    backend_url: str,
    operator_model: str,
    allowed_models: list[str] | None = None,
) -> list[dict]:
    """
    Ask the operator to propose a council for the given query.
    Returns a list of {name, model, task} dicts.

    allowed_models: if provided, restrict the operator to only these model IDs.
    """
    if allowed_models:
        models = allowed_models
    else:
        models = await _fetch_models(backend_url)
    models_block = "\n".join(f"  - {m}" for m in models) if models else "  (unavailable)"

    system = _PROPOSAL_SYSTEM.format(models=models_block)
    messages = [
        {"role": "system", "content": system},
        {"role": "user",   "content": query},
    ]

    raw = await _chat(backend_url, operator_model, messages, timeout=60.0)
    logger.debug("council proposal raw: %s", raw[:500])

    council = _extract_json_array(raw)
    if not council:
        logger.warning("council: could not parse proposal JSON, returning fallback")
        # Fallback: two generic members using whatever model is available
        fallback_model = models[0] if models else operator_model
        return [
            {"name": "Analyst A", "model": fallback_model, "task": f"Analyse: {query}"},
            {"name": "Analyst B", "model": operator_model, "task": f"Provide an alternative perspective on: {query}"},
        ]

    # Validate and sanitise
    valid = []
    for m in council:
        if not isinstance(m, dict):
            continue
        valid.append({
            "name":  str(m.get("name", "Specialist"))[:60],
            "model": str(m.get("model", operator_model)),
            "task":  str(m.get("task",  query))[:300],
        })

    return valid or [{"name": "Analyst", "model": operator_model, "task": query}]


async def execute(
    query: str,
    council: list[dict],
    backend_url: str,
    operator_model: str,
):
    """
    Async generator. Dispatches council members in parallel, yields events:
      {"type": "member_start",  "name": str, "model": str}
      {"type": "member_done",   "name": str, "model": str, "result": str}
      {"type": "member_error",  "name": str, "error": str}
      {"type": "synthesizing"}
      {"type": "synthesis",     "result": str}
      {"type": "error",         "message": str}
    """
    if not council:
        yield {"type": "error", "message": "Empty council"}
        return

    # -- Dispatch all members in parallel, yield as they complete --
    results: list[dict] = []
    queue: asyncio.Queue = asyncio.Queue()

    async def run_member(member: dict):
        name  = member["name"]
        model = member["model"]
        task  = member["task"]
        await queue.put({"type": "member_start", "name": name, "model": model})
        try:
            messages = [
                {"role": "system", "content": _MEMBER_SYSTEM.format(role=name, task=task)},
                {"role": "user",   "content": query},
            ]
            result = await _chat(backend_url, model, messages)
            await queue.put({"type": "member_done", "name": name, "model": model, "result": result})
        except Exception as e:
            logger.error("council member %s failed: %s", name, e)
            await queue.put({"type": "member_error", "name": name, "error": str(e)})

    tasks = [asyncio.create_task(run_member(m)) for m in council]

    done_count = 0
    total = len(tasks)
    while done_count < total:
        event = await queue.get()
        if event["type"] in ("member_done", "member_error"):
            done_count += 1
            if event["type"] == "member_done":
                results.append(event)
        yield event

    # -- Synthesis --
    yield {"type": "synthesizing"}

    briefing_parts = [f"User query: {query}\n"]
    for r in results:
        briefing_parts.append(f"--- {r['name']} ({r['model']}) ---\n{r['result']}")

    briefing = "\n\n".join(briefing_parts)
    try:
        synthesis = await _chat(
            backend_url,
            operator_model,
            [
                {"role": "system", "content": _SYNTHESIS_SYSTEM},
                {"role": "user",   "content": briefing},
            ],
        )
        yield {"type": "synthesis", "result": synthesis}
    except Exception as e:
        logger.error("council synthesis failed: %s", e)
        yield {"type": "error", "message": f"Synthesis failed: {e}"}
