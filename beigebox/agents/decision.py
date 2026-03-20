"""
Decision LLM — the brain of BeigeBox.

A small, fast model (configured in config.yaml) reads
the user's message and makes routing decisions:
  - Which model should handle this? (coder, general, large)
  - Does this need web search augmentation?
  - Should we pull relevant conversation history via RAG?
  - Are any tools needed?

Design principles:
  - Fast: decision prompt is tight, output is constrained JSON
  - Fault-tolerant: if the decision LLM fails, fall back to defaults
  - Transparent: every decision is logged to the wiretap
  - Configurable: routes and tools are defined in config.yaml
"""

import json
import logging
from dataclasses import dataclass, field

import httpx

from beigebox.config import get_config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Decision schema
# ---------------------------------------------------------------------------

@dataclass
class Decision:
    """The output of the decision LLM."""
    model: str = ""                  # Which model to route to
    needs_search: bool = False       # Should we run web search first?
    needs_rag: bool = False          # Should we pull conversation history?
    tools: list[str] = field(default_factory=list)  # Which tools to invoke
    reasoning: str = ""              # Brief explanation (for logging/debug)
    confidence: float = 1.0          # 0-1, how sure the router is
    fallback: bool = False           # True = decision was NOT made by the LLM;
                                     # it is the safe pass-through default.
                                     # Callers can use this to suppress routing metrics.
    wasm_module: str = ""            # WASM transform module to apply post-response


DEFAULT_DECISION = Decision(fallback=True)


# ---------------------------------------------------------------------------
# System prompt for the decision LLM
# ---------------------------------------------------------------------------

DECISION_SYSTEM_PROMPT = """You are a routing assistant inside an LLM proxy called BeigeBox. Your ONLY job is to analyze the user's message and decide how to handle it. You must respond with ONLY a JSON object, no other text.

Available routes (models):
{routes_block}

Available tools:
{tools_block}

Available WASM transform modules (applied to the model response after generation):
{wasm_block}

Analyze the user's message and return a JSON object with these fields:
- "model": the route name to use (from the routes above)
- "needs_search": true if the question requires current/recent information from the web
- "needs_rag": true if the question references past conversations or would benefit from conversation history context
- "tools": array of tool names to invoke before sending to the model (empty array if none needed)
- "wasm_module": name of WASM transform module to apply to the response (from the list above), or "" for none
- "reasoning": one sentence explaining your decision

Rules:
- Default to the "default" route unless there's a clear reason to use another
- Only set needs_search=true for questions about current events, recent data, or things that change over time
- Only set needs_rag=true if the user references "we discussed", "earlier", "last time", "remember", or similar
- Only include tools that are clearly needed — when in doubt, use none
- Only select a wasm_module if the content clearly requires post-processing — when in doubt, use ""
- RESPOND ONLY WITH THE JSON OBJECT. No markdown, no explanation, no code fences."""


def _build_routes_block(routes: dict) -> str:
    """Format route config into a block for the system prompt."""
    lines = []
    for name, cfg in routes.items():
        desc = cfg.get("description", "")
        model = cfg.get("model", name)
        wasm_hint = cfg.get("wasm_module", "")
        hint_str = f" [suggest wasm: {wasm_hint}]" if wasm_hint else ""
        lines.append(f'- "{name}": model={model} — {desc}{hint_str}')
    return "\n".join(lines) if lines else "- No custom routes configured. Use the default model."


def _build_tools_block(tool_names: list[str]) -> str:
    """Format available tools into a block for the system prompt."""
    if not tool_names:
        return "- No tools available."
    return "\n".join(f'- "{t}"' for t in tool_names)


def _build_wasm_block(modules: dict) -> str:
    """Format available WASM modules into a block for the system prompt."""
    enabled = {
        name: cfg for name, cfg in modules.items()
        if cfg.get("enabled", True)
    }
    if not enabled:
        return "- No WASM modules configured."
    lines = []
    for name, cfg in enabled.items():
        desc = cfg.get("description", "")
        lines.append(f'- "{name}": {desc}')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Decision Agent
# ---------------------------------------------------------------------------

class DecisionAgent:
    """
    Routes requests using a small local LLM.

    The agent sends the user's latest message (NOT the full history) to a
    fast model and parses its JSON response into a Decision.

    If anything fails — model timeout, parse error, bad JSON — the agent
    returns a default Decision that passes the request through unmodified.
    """

    def __init__(
        self,
        model: str = "",
        backend_url: str = "",
        timeout: int = 5,
        routes: dict | None = None,
        available_tools: list[str] | None = None,
        default_model: str = "",
        wasm_modules: dict | None = None,
    ):
        self.model = model
        self.backend_url = backend_url.rstrip("/")
        self.timeout = timeout
        self.routes = routes or {}
        self.available_tools = available_tools or []
        self.default_model = default_model
        self.wasm_modules = wasm_modules or {}
        self.enabled = bool(model and backend_url)

        # Fallback counters — monotonically increasing, never reset
        self._decisions_total: int = 0
        self._fallbacks_total: int = 0

        # Pre-build the system prompt once at startup — routes and tools
        # don't change at runtime so there's no need to format this on every
        # incoming request. Avoids repeated string formatting on the hot path.
        self._system_prompt = DECISION_SYSTEM_PROMPT.format(
            routes_block=_build_routes_block(self.routes),
            tools_block=_build_tools_block(self.available_tools),
            wasm_block=_build_wasm_block(self.wasm_modules),
        )

        if self.enabled:
            logger.info(
                "DecisionAgent enabled (model=%s, routes=%s, tools=%s)",
                self.model,
                list(self.routes.keys()),
                self.available_tools,
            )
        else:
            logger.info("DecisionAgent disabled (no model configured)")

    @classmethod
    def from_config(cls, available_tools: list[str] | None = None) -> "DecisionAgent":
        """Create a DecisionAgent from config.yaml settings."""
        cfg = get_config()
        d_cfg = cfg.get("decision_llm", {})

        if not d_cfg.get("enabled", False):
            return cls()  # Disabled agent

        wasm_cfg = cfg.get("wasm", {})
        wasm_modules = wasm_cfg.get("modules", {}) if wasm_cfg.get("enabled", False) else {}

        # Resolve judge model from unified models registry (Phase 2 refactoring)
        models_cfg = cfg.get("models", {})
        judge_model = (
            models_cfg.get("profiles", {}).get("routing")
            or d_cfg.get("model")  # fallback to old location for backwards compat
            or models_cfg.get("default", "")
        )

        return cls(
            model=judge_model,
            backend_url=d_cfg.get("backend_url", cfg["backend"]["url"]),
            timeout=d_cfg.get("timeout", 5),
            routes=d_cfg.get("routes", {}),
            available_tools=available_tools or [],
            default_model=models_cfg.get("default", cfg["backend"].get("default_model", "")),
            wasm_modules=wasm_modules,
        )

    def _resolve_model(self, route_name: str) -> str:
        """Resolve a route name to an actual model string."""
        if route_name in self.routes:
            return self.routes[route_name].get("model", self.default_model)
        # If the route name looks like a model string already, use it
        if ":" in route_name or "/" in route_name:
            return route_name
        return self.default_model

    def _parse_response(self, text: str) -> Decision:
        """Parse the LLM's JSON response into a Decision."""
        # Strip markdown fences if present
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines).strip()

        data = json.loads(cleaned)

        route_name = data.get("model", "default")
        resolved_model = self._resolve_model(route_name)

        # Validate wasm_module — ignore hallucinated names
        wasm_module = str(data.get("wasm_module", ""))
        if wasm_module and wasm_module not in self.wasm_modules:
            logger.warning("Decision LLM picked unknown WASM module '%s', ignoring", wasm_module)
            wasm_module = ""

        return Decision(
            model=resolved_model,
            needs_search=bool(data.get("needs_search", False)),
            needs_rag=bool(data.get("needs_rag", False)),
            # Hallucination guard: only pass through tool names that actually
            # exist in the registry. The LLM may invent plausible-sounding names.
            tools=[t for t in data.get("tools", []) if t in self.available_tools],
            reasoning=str(data.get("reasoning", "")),
            confidence=float(data.get("confidence", 0.8)),
            wasm_module=wasm_module,
        )

    async def decide(self, user_message: str, timeout: int | None = None) -> Decision:
        """
        Analyze a user message and return a routing Decision.

        This calls the decision LLM with a tight prompt and parses the
        JSON response. If anything goes wrong, returns the default.

        timeout: optional override (seconds) — takes precedence over self.timeout.
        """
        if not self.enabled:
            return Decision(model=self.default_model, fallback=True)

        self._decisions_total += 1
        effective_timeout = timeout if timeout is not None else self.timeout
        try:
            async with httpx.AsyncClient(timeout=effective_timeout) as client:
                resp = await client.post(
                    f"{self.backend_url}/v1/chat/completions",
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": self._system_prompt},
                            {"role": "user", "content": user_message},
                        ],
                        "temperature": 0.1,  # Low temp for consistent routing
                        "max_tokens": 256,   # Routing decisions are tiny
                        "stream": False,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            content = data["choices"][0]["message"]["content"]
            decision = self._parse_response(content)

            logger.info(
                "Decision: model=%s, search=%s, rag=%s, tools=%s — %s",
                decision.model,
                decision.needs_search,
                decision.needs_rag,
                decision.tools,
                decision.reasoning,
            )
            return decision

        # All three failure modes (timeout, bad JSON, general error) return the
        # same safe default. Routing failures are silent pass-throughs — the
        # request continues with the default model, never surfaces as an error
        # to the client.
        except httpx.TimeoutException:
            self._fallbacks_total += 1
            logger.warning("Decision LLM timed out after %ds, using default", effective_timeout)
            return Decision(model=self.default_model, fallback=True)
        except json.JSONDecodeError as e:
            self._fallbacks_total += 1
            logger.warning("Decision LLM returned invalid JSON: %s", e)
            return Decision(model=self.default_model, fallback=True)
        except Exception as e:
            self._fallbacks_total += 1
            logger.warning("Decision LLM failed: %s", e)
            return Decision(model=self.default_model, fallback=True)

    def fallback_stats(self) -> dict:
        """Return fallback rate metrics for observability."""
        total = self._decisions_total
        fallbacks = self._fallbacks_total
        rate = (fallbacks / total) if total > 0 else 0.0
        return {
            "decisions_total": total,
            "fallbacks_total": fallbacks,
            "fallback_rate": round(rate, 4),
        }

    async def preload(self, retries: int = 5, base_delay: float = 5.0):
        """
        Preload the decision model into Ollama and pin it in memory.
        Retries with exponential backoff — Ollama may still be loading the
        model from disk when beigebox first starts.
        """
        if not self.enabled:
            return

        import asyncio as _aio
        for attempt in range(retries):
            try:
                async with httpx.AsyncClient(timeout=120) as client:
                    resp = await client.post(
                        f"{self.backend_url}/api/generate",
                        json={"model": self.model, "prompt": "", "keep_alive": -1},
                    )
                    resp.raise_for_status()
                logger.info("Decision model '%s' preloaded and pinned", self.model)
                return
            except Exception as e:
                delay = base_delay * (2 ** attempt)
                if attempt < retries - 1:
                    logger.warning(
                        "Decision preload attempt %d/%d failed (%s) — retrying in %.0fs",
                        attempt + 1, retries, e, delay,
                    )
                    await _aio.sleep(delay)
                else:
                    logger.warning("Failed to preload decision model after %d attempts: %s", retries, e)
