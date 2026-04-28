"""Fan a list of items out to N parallel model calls, optionally reduce.

Designed for tasks where one model call would exhaust a reasoning model's
token budget — e.g. asking trinity-large-thinking to review 13 files in one
prompt. Splitting into 13 calls (one file each) keeps each call's reasoning
budget bounded, and the optional reduce phase merges the per-item responses.

The skill talks OpenAI-compat to whatever ``base_url`` you give it. Defaults
to the local BeigeBox proxy so the call shows up in the wire log alongside
everything else.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

import httpx


DEFAULT_BASE_URL = "http://localhost:1337/v1"
DEFAULT_API_KEY = "none"  # BeigeBox proxy accepts any token; upstream auth handled there.
DEFAULT_TIMEOUT = 1200.0  # 20 min per call — reasoning models need it.


_PLACEHOLDER_RE = re.compile(r"\{(index|item|item\.[A-Za-z_][A-Za-z0-9_-]*)\}")


def _render(template: str, item: Any, index: int) -> str:
    """Substitute ``{item}``, ``{item.field}``, and ``{index}`` into ``template``.

    Single-pass regex substitution: each ``{...}`` match is resolved exactly
    once, so a placeholder that happens to land inside a substituted value
    is not re-substituted. (An earlier multi-pass implementation could expand
    ``{item.other_field}`` literally embedded in a dict value, silently
    corrupting templates.)

    Unknown placeholders are left as literal ``{key}`` rather than raising, so
    a typo doesn't kill the run. For dict items, ``{item}`` itself serializes
    via JSON; ``{item.field}`` resolves to ``item["field"]`` (string passes
    through, non-strings JSON-encode).
    """
    is_dict = isinstance(item, dict)

    def _resolve(match: "re.Match[str]") -> str:
        key = match.group(1)
        if key == "index":
            return str(index)
        if key == "item":
            return json.dumps(item, ensure_ascii=False) if is_dict else str(item)
        if key.startswith("item."):
            field = key[len("item."):]
            if is_dict and field in item:
                v = item[field]
                return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        # Unknown — leave the literal placeholder in place.
        return match.group(0)

    return _PLACEHOLDER_RE.sub(_resolve, template)


def _render_reduce(template: str, joined_responses: str, count: int) -> str:
    return template.replace("{responses}", joined_responses).replace("{count}", str(count))


async def _one_call(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    api_key: str,
    model: str,
    rendered_prompt: str,
    system: str | None,
    temperature: float,
    max_tokens: int | None,
) -> dict[str, Any]:
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": rendered_prompt})

    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        body["max_tokens"] = max_tokens

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    started = time.monotonic()
    resp = await client.post(f"{base_url.rstrip('/')}/chat/completions", json=body, headers=headers)
    elapsed = time.monotonic() - started
    resp.raise_for_status()
    data = resp.json()

    choice = (data.get("choices") or [{}])[0]
    content = (choice.get("message") or {}).get("content") or ""
    finish_reason = choice.get("finish_reason")
    return {
        "content": content,
        "finish_reason": finish_reason,
        "tokens": data.get("usage") or {},
        "duration_seconds": round(elapsed, 2),
        "model": data.get("model", model),
    }


async def fan_out(
    items: list[Any],
    prompt_template: str,
    *,
    model: str,
    concurrency: int = 4,
    base_url: str = DEFAULT_BASE_URL,
    api_key: str = DEFAULT_API_KEY,
    system: str | None = None,
    temperature: float = 0.2,
    max_tokens: int | None = None,
    reduce_prompt: str | None = None,
    reduce_model: str | None = None,
    reduce_system: str | None = None,
    reduce_max_tokens: int | None = None,
    reduce_on_partial: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Fan ``items`` out to parallel model calls and optionally reduce.

    Per-item errors are captured in the result rather than raised, so one
    bad item does not abort the run. The reduce step fires when ``reduce_prompt``
    is set; by default it requires every item to have succeeded. Pass
    ``reduce_on_partial=True`` to let reduce run on whatever responses came
    back.

    Returns:
        {
          "responses": [{item, content, finish_reason, tokens, duration_seconds, error}, ...],
          "reduce": {content, finish_reason, tokens, duration_seconds} | None,
          "stats": {items, succeeded, failed, total_prompt_tokens,
                    total_completion_tokens, total_duration_seconds},
        }
    """
    if concurrency < 1:
        raise ValueError("concurrency must be >= 1")
    if not items:
        return {
            "responses": [],
            "reduce": None,
            "stats": {
                "items": 0,
                "succeeded": 0,
                "failed": 0,
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "total_duration_seconds": 0.0,
            },
        }

    sem = asyncio.Semaphore(concurrency)
    started = time.monotonic()

    async with httpx.AsyncClient(timeout=timeout) as client:

        async def _bounded(index: int, item: Any) -> dict[str, Any]:
            rendered = _render(prompt_template, item, index)
            async with sem:
                try:
                    res = await _one_call(
                        client,
                        base_url=base_url,
                        api_key=api_key,
                        model=model,
                        rendered_prompt=rendered,
                        system=system,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                    return {"item": item, "error": None, **res}
                except Exception as exc:
                    return {
                        "item": item,
                        "error": f"{type(exc).__name__}: {exc}",
                        "content": "",
                        "finish_reason": None,
                        "tokens": {},
                        "duration_seconds": 0.0,
                        "model": model,
                    }

        responses = await asyncio.gather(*(_bounded(i, item) for i, item in enumerate(items)))

        succeeded = [r for r in responses if r["error"] is None]
        failed = [r for r in responses if r["error"] is not None]

        reduce_result: dict[str, Any] | None = None
        if reduce_prompt and (succeeded and (reduce_on_partial or not failed)):
            # Number sequentially over succeeded responses only — labelling them by
            # the original `responses` index would skip numbers when items failed
            # ("Response 2" with no Response 1 is misleading to the merger model).
            joined = "\n\n---\n\n".join(
                f"### Response {n + 1}\n{r['content']}"
                for n, r in enumerate(succeeded)
            )
            reduce_rendered = _render_reduce(reduce_prompt, joined, len(succeeded))
            reduce_model_id = reduce_model or model
            try:
                reduce_result = await _one_call(
                    client,
                    base_url=base_url,
                    api_key=api_key,
                    model=reduce_model_id,
                    rendered_prompt=reduce_rendered,
                    system=reduce_system,
                    temperature=temperature,
                    max_tokens=reduce_max_tokens,
                )
                reduce_result["error"] = None
            except Exception as exc:
                # Mirror the success-path schema (incl. "model") so downstream
                # callers can read reduce_result["model"] without a KeyError.
                reduce_result = {
                    "error": f"{type(exc).__name__}: {exc}",
                    "content": "",
                    "finish_reason": None,
                    "tokens": {},
                    "duration_seconds": 0.0,
                    "model": reduce_model_id,
                }

    total_prompt = sum((r["tokens"].get("prompt_tokens") or 0) for r in responses)
    total_completion = sum((r["tokens"].get("completion_tokens") or 0) for r in responses)
    if reduce_result and not reduce_result.get("error"):
        total_prompt += reduce_result["tokens"].get("prompt_tokens", 0) or 0
        total_completion += reduce_result["tokens"].get("completion_tokens", 0) or 0

    stats = {
        "items": len(items),
        "succeeded": len(succeeded),
        "failed": len(failed),
        "total_prompt_tokens": total_prompt,
        "total_completion_tokens": total_completion,
        "total_duration_seconds": round(time.monotonic() - started, 2),
    }
    return {"responses": responses, "reduce": reduce_result, "stats": stats}
