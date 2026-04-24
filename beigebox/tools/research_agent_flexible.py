"""
ResearchAgentFlexibleTool — backend-agnostic research agent.

Same iterative research loop as ResearchAgentTool, but accepts a ``backend``
parameter at runtime so the Operator (or user) can choose which inference
provider handles the research.

Backend format: ``"provider:model"``
    - "openrouter:arcee/trinity"
    - "ollama:neural-chat"
    - "openai:gpt-4"
    - "anthropic:claude-opus"

Falls back to BeigeBox's configured default when ``backend`` is omitted or
the specified provider is unreachable.

Input format (JSON string):
    {
        "topic": "RAG poisoning attacks",
        "research_questions": ["What are the main attack vectors?"],
        "max_turns": 10,
        "depth": "medium",
        "backend": "openrouter:arcee/trinity"
    }
"""

from __future__ import annotations

import abc
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from beigebox.config import get_config, get_runtime_config
from beigebox.response_normalizer import normalize_response

logger = logging.getLogger(__name__)

_WORKSPACE_OUT = Path(
    os.environ.get("WORKSPACE_OUT", "/app/workspace/out")
).resolve()

# ── Depth presets ───────────────────────────────────────────────────────────
_DEPTH_PRESETS = {
    "quick":  (0.5, "Be concise. Provide a brief overview with key points only."),
    "medium": (1.0, "Be thorough. Cover main aspects with supporting detail."),
    "deep":   (2.0, "Be exhaustive. Cover every angle, include edge cases, cite specifics."),
}

# ── Shared research system prompt ───────────────────────────────────────────
_RESEARCH_SYSTEM = """\
You are a focused research agent. Your task is to investigate a specific topic and answer \
research questions with structured, evidence-based findings.

TOPIC: {topic}

RESEARCH QUESTIONS:
{questions}

INSTRUCTIONS:
- {depth_instruction}
- Structure your response as a comprehensive research report.
- Include specific facts, data points, and citations where possible.
- Identify areas of uncertainty and flag what you don't know.
- Suggest follow-up questions that would deepen understanding.

Respond with a JSON object:
{{
    "findings": "markdown-formatted research findings",
    "sources": ["list of sources/references cited"],
    "confidence": 0.0-1.0,
    "next_questions": ["unanswered or follow-up questions"]
}}

Output ONLY the JSON object. No prose before or after."""


# ═══════════════════════════════════════════════════════════════════════════
#  Backend adapter layer
# ═══════════════════════════════════════════════════════════════════════════

class BackendAdapter(abc.ABC):
    """Lightweight adapter that knows how to call a single provider's chat API."""

    provider: str = ""

    @abc.abstractmethod
    async def chat(
        self, messages: list[dict], model: str, temperature: float = 0.3,
        timeout: float = 300,
    ) -> str:
        """Return the assistant content string from a chat completion."""
        ...

    @abc.abstractmethod
    async def health(self) -> bool:
        """Quick reachability check."""
        ...


class OllamaAdapter(BackendAdapter):
    """Adapter for local Ollama instances (OpenAI-compat /v1/ endpoint)."""

    provider = "ollama"

    def __init__(self, base_url: str = ""):
        cfg = get_config()
        self.base_url = (
            base_url
            or os.environ.get("OLLAMA_URL")
            or cfg.get("backend", {}).get("url", "http://localhost:11434")
        ).rstrip("/")

    async def chat(self, messages, model, temperature=0.3, timeout=300):
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
            "options": {"num_ctx": 8192},
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{self.base_url}/v1/chat/completions", json=payload,
            )
            resp.raise_for_status()
            return normalize_response(resp.json()).content

    async def health(self):
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                return resp.status_code == 200
        except Exception:
            return False


class OpenRouterAdapter(BackendAdapter):
    """Adapter for OpenRouter API."""

    provider = "openrouter"

    def __init__(self, api_key: str = "", base_url: str = ""):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self.base_url = (base_url or "https://openrouter.ai/api/v1").rstrip("/")

    def _headers(self):
        h: dict[str, str] = {
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/RALaBarge/beigebox",
            "X-Title": "BeigeBox",
        }
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def chat(self, messages, model, temperature=0.3, timeout=300):
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(), json=payload,
            )
            resp.raise_for_status()
            return normalize_response(resp.json()).content

    async def health(self):
        if not self.api_key:
            return False
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(
                    f"{self.base_url}/models", headers=self._headers(),
                )
                return resp.status_code == 200
        except Exception:
            return False


class OpenAIAdapter(BackendAdapter):
    """Adapter for the official OpenAI API."""

    provider = "openai"

    def __init__(self, api_key: str = "", base_url: str = ""):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")

    def _headers(self):
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def chat(self, messages, model, temperature=0.3, timeout=300):
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(), json=payload,
            )
            resp.raise_for_status()
            return normalize_response(resp.json()).content

    async def health(self):
        if not self.api_key:
            return False
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(
                    f"{self.base_url}/models", headers=self._headers(),
                )
                return resp.status_code == 200
        except Exception:
            return False


class AnthropicAdapter(BackendAdapter):
    """Adapter for the Anthropic Messages API."""

    provider = "anthropic"

    def __init__(self, api_key: str = "", base_url: str = ""):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.base_url = (base_url or "https://api.anthropic.com").rstrip("/")

    def _headers(self):
        return {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }

    async def chat(self, messages, model, temperature=0.3, timeout=300):
        # Anthropic uses a different schema: system is separate, roles are
        # "user"/"assistant" only (no "system" role in messages list).
        system_text = ""
        filtered: list[dict] = []
        for m in messages:
            if m["role"] == "system":
                system_text += m["content"] + "\n"
            else:
                filtered.append({"role": m["role"], "content": m["content"]})

        payload: dict[str, Any] = {
            "model": model,
            "messages": filtered,
            "max_tokens": 4096,
            "temperature": temperature,
        }
        if system_text.strip():
            payload["system"] = system_text.strip()

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{self.base_url}/v1/messages",
                headers=self._headers(), json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            # Anthropic returns content as a list of blocks
            blocks = data.get("content", [])
            return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")

    async def health(self):
        # Anthropic has no lightweight health endpoint; we just check if key
        # is configured. Actual availability is tested on first call.
        return bool(self.api_key)


class BeigeBoxRouterAdapter(BackendAdapter):
    """
    Adapter that delegates to BeigeBox's own MultiBackendRouter.

    This reuses the already-configured backends from config.yaml so the tool
    doesn't need to duplicate connection/auth logic. It injects
    ``_bb_force_backend`` into the request body when a specific backend name
    is requested.
    """

    provider = "beigebox"

    def __init__(self, router=None, force_backend: str = ""):
        self._router = router
        self._force_backend = force_backend

    async def chat(self, messages, model, temperature=0.3, timeout=300):
        if self._router is None:
            raise RuntimeError("BeigeBox router not available")
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
        }
        if self._force_backend:
            body["_bb_force_backend"] = self._force_backend
        resp = await self._router.forward(body)
        if not resp.ok:
            raise RuntimeError(f"Router returned error: {resp.error}")
        return resp.content

    async def health(self):
        return self._router is not None


# ── Adapter registry ────────────────────────────────────────────────────────

ADAPTER_REGISTRY: dict[str, type[BackendAdapter]] = {
    "ollama": OllamaAdapter,
    "openrouter": OpenRouterAdapter,
    "openai": OpenAIAdapter,
    "anthropic": AnthropicAdapter,
    "beigebox": BeigeBoxRouterAdapter,
}


def parse_backend_spec(spec: str) -> tuple[str, str]:
    """
    Parse ``"provider:model"`` into (provider, model).

    Examples:
        "openrouter:arcee/trinity"  → ("openrouter", "arcee/trinity")
        "ollama:neural-chat"        → ("ollama", "neural-chat")
        "anthropic:claude-opus"     → ("anthropic", "claude-opus")
        "neural-chat"               → ("", "neural-chat")  (no provider)
        ""                          → ("", "")
    """
    if not spec:
        return ("", "")
    # Only split on the first colon; model names may contain colons (e.g. ollama tags)
    parts = spec.split(":", 1)
    if len(parts) == 2 and parts[0] in ADAPTER_REGISTRY:
        return (parts[0], parts[1])
    # Not a recognized provider prefix — treat entire string as model name
    return ("", spec)


def build_adapter(
    provider: str,
    *,
    router=None,
    force_backend: str = "",
) -> BackendAdapter:
    """
    Instantiate an adapter for the given provider.

    Falls back to ``BeigeBoxRouterAdapter`` if the provider is unknown.
    """
    if provider == "beigebox" or (provider and provider not in ADAPTER_REGISTRY):
        return BeigeBoxRouterAdapter(router=router, force_backend=force_backend)

    cls = ADAPTER_REGISTRY.get(provider)
    if cls is None:
        return BeigeBoxRouterAdapter(router=router, force_backend=force_backend)

    if cls is BeigeBoxRouterAdapter:
        return cls(router=router, force_backend=force_backend)
    return cls()


# ═══════════════════════════════════════════════════════════════════════════
#  Research Agent (flexible)
# ═══════════════════════════════════════════════════════════════════════════

class ResearchAgentFlexibleTool:
    """
    Backend-agnostic research agent.

    Identical research loop to ``ResearchAgentTool`` but can target any
    supported inference backend at runtime via the ``backend`` parameter.
    """

    description = (
        'Launch a backend-agnostic research agent. '
        'input MUST be a JSON object with keys: '
        '"topic" (string), "research_questions" (list of strings). '
        'Optional: "max_turns" (int, default 10), "depth" ("quick"|"medium"|"deep"), '
        '"backend" (string, format "provider:model" e.g. "openrouter:arcee/trinity", '
        '"ollama:neural-chat", "openai:gpt-4", "anthropic:claude-opus"). '
        'Falls back to configured default if backend not specified or unavailable. '
        'Returns structured findings with sources and confidence score.'
    )

    def __init__(self, workspace_out: Path | None = None, router=None):
        self._root = (workspace_out or _WORKSPACE_OUT).resolve()
        cfg = get_config()
        models_cfg = cfg.get("models", {})

        # Default model + backend URL for fallback
        self._default_model = (
            models_cfg.get("profiles", {}).get("agentic")
            or models_cfg.get("default")
            or cfg.get("backend", {}).get("default_model", "")
        )
        self._default_backend_url = (
            cfg.get("embedding", {}).get("backend_url")
            or cfg.get("backend", {}).get("url", "http://localhost:11434")
        ).rstrip("/")
        self._timeout = cfg.get("operator", {}).get("timeout", 300)
        self._router = router  # Optional: BeigeBox MultiBackendRouter

    def _resolve_adapter(self, backend_spec: str) -> tuple[BackendAdapter, str]:
        """
        Resolve a backend spec into (adapter, model).

        Returns the adapter and the model to use. On failure, falls back to
        the default Ollama adapter with the configured default model.
        """
        provider, model = parse_backend_spec(backend_spec)

        if not provider and not model:
            # No spec at all — use default
            return OllamaAdapter(base_url=self._default_backend_url), self._default_model

        if not provider:
            # Model specified but no provider — use default adapter with given model
            return OllamaAdapter(base_url=self._default_backend_url), model or self._default_model

        adapter = build_adapter(
            provider, router=self._router, force_backend=provider,
        )
        return adapter, model or self._default_model

    async def _try_adapter_with_fallback(
        self, backend_spec: str
    ) -> tuple[BackendAdapter, str]:
        """
        Resolve adapter, check health, fall back to default if unhealthy.
        """
        adapter, model = self._resolve_adapter(backend_spec)

        try:
            healthy = await adapter.health()
        except Exception:
            healthy = False

        if healthy:
            return adapter, model

        # Fallback to default
        logger.warning(
            "Backend '%s' unavailable, falling back to default (ollama @ %s)",
            backend_spec, self._default_backend_url,
        )
        fallback = OllamaAdapter(base_url=self._default_backend_url)
        return fallback, self._default_model

    def _parse_findings(self, raw: str, topic: str) -> dict:
        """Extract structured findings from LLM output."""
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3].rstrip()

        try:
            parsed = json.loads(raw)
            return {
                "topic": topic,
                "findings": parsed.get("findings", raw),
                "sources": parsed.get("sources", []),
                "confidence": float(parsed.get("confidence", 0.5)),
                "next_questions": parsed.get("next_questions", []),
            }
        except (json.JSONDecodeError, TypeError, ValueError):
            return {
                "topic": topic,
                "findings": raw,
                "sources": [],
                "confidence": 0.3,
                "next_questions": [],
            }

    def _save_findings(self, result: dict) -> str:
        """Save findings to workspace/out/{topic}_research.md."""
        self._root.mkdir(parents=True, exist_ok=True)
        safe_name = "".join(c if c.isalnum() or c in "-_ " else "" for c in result["topic"])
        safe_name = safe_name.strip().replace(" ", "_").lower()[:60]
        filepath = self._root / f"{safe_name}_research.md"

        lines = [
            f"# Research: {result['topic']}",
            f"*Generated: {datetime.now(timezone.utc).isoformat()}*",
            f"*Confidence: {result['confidence']:.0%}*",
            f"*Backend: {result.get('backend_used', 'unknown')}*",
            "",
            "## Findings",
            result["findings"],
            "",
        ]
        if result["sources"]:
            lines.append("## Sources")
            for src in result["sources"]:
                lines.append(f"- {src}")
            lines.append("")
        if result["next_questions"]:
            lines.append("## Open Questions")
            for q in result["next_questions"]:
                lines.append(f"- {q}")
            lines.append("")

        filepath.write_text("\n".join(lines), encoding="utf-8")
        return str(filepath)

    async def _execute_research_async(
        self,
        topic: str,
        research_questions: list[str],
        max_turns: int,
        depth: str,
        backend: str,
    ) -> dict:
        """Core async research loop."""
        multiplier, depth_instruction = _DEPTH_PRESETS.get(depth, _DEPTH_PRESETS["medium"])
        effective_turns = max(1, int(max_turns * multiplier))

        # Resolve backend
        adapter, model = await self._try_adapter_with_fallback(backend)
        backend_used = f"{adapter.provider}:{model}"

        questions_block = "\n".join(
            f"  {i+1}. {q}" for i, q in enumerate(research_questions)
        )
        system_prompt = _RESEARCH_SYSTEM.format(
            topic=topic,
            questions=questions_block,
            depth_instruction=depth_instruction,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": (
                f"Research the topic '{topic}' and answer the questions listed above. "
                f"Provide structured findings."
            )},
        ]

        last_raw = ""
        for turn in range(effective_turns):
            try:
                last_raw = await adapter.chat(
                    messages, model,
                    temperature=0.3, timeout=self._timeout,
                )
            except Exception as e:
                logger.error(
                    "Research agent LLM call failed (turn %d, backend %s): %s",
                    turn, backend_used, e,
                )
                if last_raw:
                    break
                return {
                    "topic": topic,
                    "findings": f"Research failed: {e}",
                    "sources": [],
                    "confidence": 0.0,
                    "next_questions": research_questions,
                    "backend_used": backend_used,
                }

            result = self._parse_findings(last_raw, topic)

            if result["confidence"] >= 0.7 or effective_turns == 1:
                break

            if result["next_questions"] and turn < effective_turns - 1:
                follow_up = "; ".join(result["next_questions"][:3])
                messages.append({"role": "assistant", "content": last_raw})
                messages.append({"role": "user", "content": (
                    f"Good findings so far. Now dig deeper into these gaps: {follow_up}. "
                    f"Integrate new findings with the previous ones and return updated JSON."
                )})
            else:
                break

        result = self._parse_findings(last_raw, topic)
        result["backend_used"] = backend_used
        filepath = self._save_findings(result)
        result["saved_to"] = filepath
        return result

    async def execute(
        self,
        topic: str,
        research_questions: list[str],
        max_turns: int = 10,
        depth: str = "medium",
        backend: str = "",
    ) -> dict:
        """Async entry point for direct Python callers."""
        return await self._execute_research_async(
            topic, research_questions, max_turns, depth, backend,
        )

    def run(self, input_text: str) -> str:
        """Synchronous entry point for the Operator tool registry."""
        try:
            params = json.loads(input_text)
            if not isinstance(params, dict):
                raise ValueError("not a dict")
        except (json.JSONDecodeError, TypeError, ValueError):
            return (
                'Error: input must be a JSON object. '
                'Example: {"topic":"RAG poisoning","research_questions":["What are the vectors?"],'
                '"backend":"openrouter:arcee/trinity"}'
            )

        topic = params.get("topic", "").strip()
        if not topic:
            return 'Error: "topic" is required.'

        questions = params.get("research_questions", [])
        if not questions or not isinstance(questions, list):
            return 'Error: "research_questions" must be a non-empty list of strings.'

        max_turns = int(params.get("max_turns", 10))
        depth = params.get("depth", "medium")
        if depth not in _DEPTH_PRESETS:
            depth = "medium"
        backend = params.get("backend", "")

        t0 = time.monotonic()
        try:
            result = asyncio.get_event_loop().run_until_complete(
                self._execute_research_async(topic, questions, max_turns, depth, backend)
            )
        except RuntimeError:
            # No running event loop — create one
            result = asyncio.run(
                self._execute_research_async(topic, questions, max_turns, depth, backend)
            )
        except Exception as e:
            logger.error("ResearchAgentFlexibleTool failed: %s", e)
            return json.dumps({
                "topic": topic,
                "findings": f"Research failed: {e}",
                "sources": [],
                "confidence": 0.0,
                "next_questions": questions,
                "error": str(e),
            })

        result["execution_time"] = round(time.monotonic() - t0, 2)
        return json.dumps(result)
