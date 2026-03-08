"""
Proxy: the core of BeigeBox.
Intercepts OpenAI-compatible requests, logs both sides, forwards to backend.
Handles streaming (SSE) transparently.
Now with:
  - Decision LLM routing (pick the right model per request)
  - Pre/post hooks (extensible processing pipeline)
  - Synthetic request filtering (skip Open WebUI's internal requests)
  - Token tracking (approximate token counts for stats)
  - Session-aware routing (sticky model within a conversation)
  - Multi-backend routing with fallback (v0.6)
  - Cost tracking for API backends (v0.6)
  - Streaming latency tracking — wall-clock duration stored as latency_ms (v0.8)
  - Streaming cost capture via OpenRouter sentinel (v0.8)
"""
import json
import logging
import asyncio
import time
from datetime import datetime, timezone
from uuid import uuid4
import httpx
from beigebox.config import get_config, get_runtime_config
from beigebox.storage.models import Message
from beigebox.storage.sqlite_store import SQLiteStore
from beigebox.storage.vector_store import VectorStore
from beigebox.agents.decision import DecisionAgent, Decision
from beigebox.agents.zcommand import parse_z_command, ZCommand, HELP_TEXT
from beigebox.agents.embedding_classifier import EmbeddingClassifier, EmbeddingDecision
from beigebox.hooks import HookManager
from beigebox.wiretap import WireLog
from beigebox.wasm_runtime import WasmRuntime
from beigebox.agents.agentic_scorer import score_agentic_intent
from beigebox.agents.routing_rules import (
    evaluate_routing_rules,
    BB_FORCE_BACKEND,
    BB_SKIP_SEMANTIC_CACHE,
    BB_RULE_TAG,
    BB_FORCED_TOOLS,
)
from beigebox.backends.openrouter import _COST_SENTINEL_PREFIX
from beigebox.cache import SemanticCache, ToolResultCache
logger = logging.getLogger(__name__)
def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English text."""
    return max(1, len(text) // 4)
class Proxy:
    """Transparent proxy between frontend and Ollama backend."""
    def __init__(
        self,
        sqlite: SQLiteStore,
        vector: VectorStore,
        decision_agent: DecisionAgent | None = None,
        hook_manager: HookManager | None = None,
        embedding_classifier: EmbeddingClassifier | None = None,
        tool_registry=None,
        backend_router=None,
        blob_store=None,
    ):
        self.sqlite = sqlite
        self.vector = vector
        self.blob_store = blob_store
        self.decision_agent = decision_agent
        self.hook_manager = hook_manager
        self.embedding_classifier = embedding_classifier
        self.tool_registry = tool_registry
        self.backend_router = backend_router  # MultiBackendRouter or None
        self.cfg = get_config()
        self.backend_url = self.cfg["backend"]["url"].rstrip("/")
        self.timeout = self.cfg["backend"].get("timeout", 120)
        self.default_model = self.cfg["backend"].get("default_model", "")
        self.log_enabled = self.cfg["storage"].get("log_conversations", True)
        # Cost tracking config
        self._cost_tracking = self.cfg.get("cost_tracking", {}).get("enabled", False)
        # Route config for z-command model resolution
        d_cfg = self.cfg.get("decision_llm", {})
        self.routes = d_cfg.get("routes", {})
        # Wire log — structured tap of everything on the line
        wire_path = self.cfg.get("wiretap", {}).get("path", "./data/wire.jsonl")
        self.wire = WireLog(wire_path)
        # WASM transform runtime
        self.wasm_runtime = WasmRuntime(self.cfg)
        if self.wasm_runtime.enabled:
            logger.info("WasmRuntime: %d module(s) loaded: %s", len(self.wasm_runtime.list_modules()), self.wasm_runtime.list_modules())
        # Session routing cache — sticky model within a conversation
        # {conversation_id: (model_string, timestamp)}
        self._session_cache: dict[str, tuple[str, float]] = {}
        self._session_ttl: int = self.cfg.get("routing", {}).get("session_ttl_seconds", 1800)
        # Semantic response cache + tool result cache
        self.semantic_cache = SemanticCache(self.cfg)
        self.tool_cache = ToolResultCache(
            ttl=self.cfg.get("semantic_cache", {}).get("tool_ttl_seconds", 300.0),
        )

    # ------------------------------------------------------------------
    # Session cache helpers
    # ------------------------------------------------------------------

    def _get_session_model(self, conversation_id: str) -> str | None:
        """Return cached model for this conversation if still fresh."""
        if not conversation_id or conversation_id not in self._session_cache:
            return None
        model, ts = self._session_cache[conversation_id]
        if time.time() - ts > self._session_ttl:
            del self._session_cache[conversation_id]
            return None
        return model

    def _set_session_model(self, conversation_id: str, model: str):
        """Cache the routing decision for this conversation."""
        if conversation_id and model:
            self._session_cache[conversation_id] = (model, time.time())
            # Proactive eviction: sweep stale entries every ~100 writes
            if len(self._session_cache) % 100 == 0:
                self._evict_session_cache()
            # Hard cap: if still over limit after TTL eviction, drop oldest by timestamp
            if len(self._session_cache) > 1000:
                oldest = sorted(self._session_cache.items(), key=lambda x: x[1][1])
                for k, _ in oldest[:len(self._session_cache) - 800]:
                    del self._session_cache[k]
                logger.debug("Session cache hard-capped: trimmed to %d entries", len(self._session_cache))

    def _evict_session_cache(self):
        """Remove all expired entries from the session cache."""
        cutoff = time.time() - self._session_ttl
        stale = [k for k, (_, ts) in self._session_cache.items() if ts < cutoff]
        for k in stale:
            del self._session_cache[k]
        if stale:
            logger.debug("Session cache evicted %d stale entries", len(stale))

    # ------------------------------------------------------------------
    # Request helpers
    # ------------------------------------------------------------------

    def _extract_conversation_id(self, body: dict) -> str:
        """
        Try to extract a conversation ID from the request.
        Open WebUI doesn't always send one, so we generate if missing.
        """
        conv_id = body.get("conversation_id") or body.get("session_id") or ""
        if not conv_id:
            messages = body.get("messages", [])
            if messages:
                conv_id = uuid4().hex
        return conv_id

    def _get_model(self, body: dict) -> str:
        """Extract model from request, fall back to config default."""
        return body.get("model") or self.default_model

    def _get_latest_user_message(self, body: dict) -> str:
        """Extract the last user message from the request."""
        messages = body.get("messages", [])
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                return content if isinstance(content, str) else json.dumps(content)
        return ""

    def _is_synthetic(self, body: dict) -> bool:
        """Check if this request was tagged as synthetic by a hook."""
        return body.get("_beigebox_synthetic", False)

    def _log_messages(self, conversation_id: str, messages: list[dict], model: str):
        """Store the user messages from the request."""
        if not self.log_enabled:
            return
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if not content or role == "system":
                continue
            content_str = content if isinstance(content, str) else json.dumps(content)
            tokens = _estimate_tokens(content_str)
            message = Message(
                conversation_id=conversation_id,
                role=role,
                content=content_str,
                model=model,
                token_count=tokens,
            )
            self.sqlite.store_message(message)
            # Wire tap
            self.wire.log(
                direction="inbound",
                role=role,
                content=message.content,
                model=model,
                conversation_id=conversation_id,
                token_count=tokens,
            )
            # Embed async in background — avoids blocking the event loop
            asyncio.create_task(self.vector.store_message_async(
                message_id=message.id,
                conversation_id=conversation_id,
                role=role,
                content=message.content,
                model=model,
                timestamp=message.timestamp,
            ))

    def _log_response(self, conversation_id: str, content: str, model: str, cost_usd: float | None = None, latency_ms: float | None = None, ttft_ms: float | None = None):
        """Store the assistant response."""
        if not self.log_enabled or not content.strip():
            return
        tokens = _estimate_tokens(content)
        message = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            model=model,
            token_count=tokens,
        )
        self.sqlite.store_message(message, cost_usd=cost_usd, latency_ms=latency_ms, ttft_ms=ttft_ms)
        # Wire tap
        cost_info = f" cost=${cost_usd:.6f}" if cost_usd else ""
        self.wire.log(
            direction="outbound",
            role="assistant",
            content=content,
            model=model,
            conversation_id=conversation_id,
            token_count=tokens,
        )
        if cost_usd:
            self.wire.log(
                direction="internal",
                role="system",
                content=f"cost_usd={cost_usd:.6f} model={model}",
                model="cost-tracker",
                conversation_id=conversation_id,
            )
        # Embed async in background — avoids blocking the event loop
        asyncio.create_task(self.vector.store_message_async(
            message_id=message.id,
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            model=model,
            timestamp=message.timestamp,
        ))

    def _build_hook_context(
        self,
        body: dict,
        conversation_id: str,
        model: str,
        decision: Decision | None = None,
    ) -> dict:
        """Build the context dict passed to hooks."""
        return {
            "conversation_id": conversation_id,
            "model": model,
            "user_message": self._get_latest_user_message(body),
            "decision": decision,
            "config": self.cfg,
            "vector_store": self.vector,
        }

    async def _run_decision(self, body: dict) -> Decision | None:
        """Run the decision LLM if enabled."""
        if not self.decision_agent or not self.decision_agent.enabled:
            return None
        user_msg = self._get_latest_user_message(body)
        if not user_msg:
            return None
        rt = get_runtime_config()
        timeout_override = rt.get("decision_llm_timeout")
        decision = await self.decision_agent.decide(user_msg, timeout=timeout_override)
        # Log the decision to wiretap
        if not decision.fallback:
            self.wire.log(
                direction="internal",
                role="decision",
                content=f"route={decision.model} search={decision.needs_search} "
                        f"rag={decision.needs_rag} tools={decision.tools} — {decision.reasoning}",
                model=self.decision_agent.model,
                conversation_id="",
            )
        return decision

    def _resolve_route_to_model(self, route_name: str) -> str:
        """Resolve a route name to an actual model string."""
        if route_name in self.routes:
            return self.routes[route_name].get("model", self.default_model)
        return self.default_model

    def _process_z_command(self, body: dict) -> tuple[ZCommand, dict]:
        """
        Check for z: prefix in the user's message.
        If found, parse it, strip the prefix, and return the command + modified body.
        """
        user_msg = self._get_latest_user_message(body)
        zcmd = parse_z_command(user_msg)
        if not zcmd.active:
            return zcmd, body
        # Log the z-command to wiretap
        self.wire.log(
            direction="internal",
            role="decision",
            content=f"z-command: {zcmd.raw_directives} → route={zcmd.route or 'none'} "
                    f"model={zcmd.model or 'none'} tools={zcmd.tools or 'none'}",
            model="z-command",
            conversation_id="",
        )
        # Strip the z: prefix from the actual message sent to the LLM
        if zcmd.message and not zcmd.is_help:
            messages = body.get("messages", [])
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    msg["content"] = zcmd.message
                    break
        return zcmd, body

    def _apply_z_command(self, body: dict, zcmd: ZCommand) -> dict:
        """Apply z-command routing overrides to the request body."""
        if not zcmd.active:
            return body
        # Help — return immediately (handled in forward methods)
        if zcmd.is_help:
            return body
        # Specific model override (e.g. z: llama3:8b)
        if zcmd.model:
            body["model"] = zcmd.model
            return body
        # Route alias override (e.g. z: complex → large route)
        if zcmd.route:
            body["model"] = self._resolve_route_to_model(zcmd.route)
        return body

    def _run_forced_tools(self, zcmd: ZCommand, user_msg: str) -> str:
        """Run tools forced by z-command and return results as context."""
        if not zcmd.tools or not self.tool_registry:
            return ""
        results = []
        for tool_name in zcmd.tools:
            tool_input = zcmd.tool_input if zcmd.tool_input else user_msg
            result = self.tool_registry.run_tool(tool_name, tool_input)
            if result:
                results.append(f"[{tool_name}]: {result}")
        return "\n".join(results)

    def _inject_tool_context(self, body: dict, tool_results: str) -> dict:
        """Inject tool results as a system message into the request."""
        if not tool_results:
            return body
        messages = body.get("messages", [])
        # Insert tool results as a system message before the last user message
        tool_msg = {
            "role": "system",
            "content": f"The following tool results are available:\n\n{tool_results}",
        }
        # Insert before the last message
        if messages:
            messages.insert(-1, tool_msg)
        body["messages"] = messages
        return body

    # ------------------------------------------------------------------
    # Routing pipeline
    # ------------------------------------------------------------------

    async def _hybrid_route(self, body: dict, zcmd: ZCommand, conversation_id: str) -> tuple[dict, Decision | None]:
        """
        Hybrid routing pipeline:
          0. Session cache — if we've routed this conversation before, stay sticky
          1. z-command (user override) — highest priority, skips everything
          2. Agentic pre-filter — near-zero cost keyword scorer
          3. Embedding classifier (fast path) — ~50ms, handles clear cases
          4. Decision LLM (slow path) — only for borderline cases
        Returns (modified body, decision or None).
        """
        rt = get_runtime_config()
        force_decision = rt.get("force_decision", False)

        # 0. Session cache — sticky model within a conversation (skipped when force_decision)
        if not force_decision:
            cached_model = self._get_session_model(conversation_id)
            if cached_model:
                body["model"] = cached_model
                self.wire.log(
                    direction="internal",
                    role="decision",
                    content=f"session cache hit: model={cached_model}",
                    model="session-cache",
                    conversation_id=conversation_id,
                )
                return body, None

        # 1. Z-command takes absolute priority
        if zcmd.active and (zcmd.route or zcmd.model):
            body = self._apply_z_command(body, zcmd)
            # Z-command overrides are not cached — user is being explicit
            return body, None

        # 1.75. Routing rules — hot-reloaded from runtime_config.routing_rules
        #       Evaluated after z-commands (user intent takes priority) and before
        #       the ML stack.  A matching rule with pass_through=false skips the
        #       classifier and decision LLM entirely.
        rules = rt.get("routing_rules", [])
        if rules:
            body, matched_rules, skip_sc, pass_through = evaluate_routing_rules(
                rules, body, routes=self.routes,
            )
            if matched_rules:
                rule_tag = body.pop(BB_RULE_TAG, None)
                self.wire.log(
                    direction="internal",
                    role="decision",
                    content=(
                        f"routing_rules: matched {matched_rules} → "
                        f"model={body.get('model', self.default_model)}"
                        + (f" backend={body.get(BB_FORCE_BACKEND, '')}" if BB_FORCE_BACKEND in body else "")
                        + (f" tag={rule_tag}" if rule_tag else "")
                        + (" [pass_through]" if pass_through else "")
                    ),
                    model="routing-rules",
                    conversation_id=conversation_id,
                )
                if rule_tag:
                    body[BB_RULE_TAG] = rule_tag  # restore for wiretap downstream
                if not pass_through:
                    if not skip_sc:
                        self._set_session_model(conversation_id, body.get("model", self.default_model))
                    return body, None

        # 1.5. Agentic pre-filter — near-zero cost, runs before embedding classifier
        #      High agentic score means the user wants tool use — log it and let
        #      the embedding classifier / decision LLM decide the route, but the
        #      score is available for future forced-tool logic.
        user_msg_for_scoring = self._get_latest_user_message(body)
        if user_msg_for_scoring:
            agentic = score_agentic_intent(user_msg_for_scoring)
            if agentic.is_agentic:
                self.wire.log(
                    direction="internal",
                    role="decision",
                    content=f"agentic_scorer: score={agentic.score:.2f} matched={agentic.matched}",
                    model="agentic-scorer",
                    conversation_id="",
                )

        # 2. Try embedding classifier first (fast path) — skipped when force_decision
        if not force_decision and self.embedding_classifier and self.embedding_classifier.ready:
            user_msg = self._get_latest_user_message(body)
            if user_msg:
                emb_result = self.embedding_classifier.classify(user_msg)
                self.wire.log(
                    direction="internal",
                    role="decision",
                    content=f"embedding: tier={emb_result.tier} "
                            f"confidence={emb_result.confidence:.4f} "
                            f"borderline={emb_result.borderline} ({emb_result.latency_ms}ms)",
                    model="embedding-classifier",
                    conversation_id="",
                )
                if not emb_result.borderline:
                    # Clear classification — use it, skip decision LLM
                    if emb_result.model:
                        body["model"] = emb_result.model
                    self._set_session_model(conversation_id, body.get("model", self.default_model))
                    return body, None
                # Borderline — fall through to decision LLM
                logger.debug(
                    "Embedding borderline (confidence=%.4f), escalating to decision LLM",
                    emb_result.confidence,
                )

        # 3. Decision LLM (slow path for borderline cases)
        decision = await self._run_decision(body)
        if decision and not decision.fallback:
            body = await self._apply_decision(body, decision)

        # Cache whatever model we landed on
        final_model = body.get("model", self.default_model)
        self._set_session_model(conversation_id, final_model)

        return body, decision

    async def _apply_decision(self, body: dict, decision: Decision) -> dict:
        """Apply the decision LLM's routing to the request."""
        if decision.fallback:
            return body

        # Route to the decided model
        if decision.model:
            body["model"] = decision.model

        # Run requested tools and inject results
        if decision.tools and self.tool_registry:
            tool_results = []
            for tool_name in decision.tools:
                result = self.tool_registry.run_tool(tool_name, self._get_latest_user_message(body))
                if result:
                    tool_results.append(f"[{tool_name}]: {result}")
            if tool_results:
                body = self._inject_tool_context(body, "\n".join(tool_results))

        # Web search augmentation
        if decision.needs_search and self.tool_registry:
            user_msg = self._get_latest_user_message(body)
            if user_msg:
                search_results = self.tool_registry.run_tool("web_search", user_msg)
                if search_results:
                    body = self._inject_tool_context(body, f"[web_search]: {search_results}")
                    self.wire.log(
                        direction="internal",
                        role="tool",
                        content=f"web_search injected ({len(search_results)} chars)",
                        model="",
                        conversation_id="",
                    )

        # RAG context injection
        if decision.needs_rag and self.tool_registry:
            user_msg = self._get_latest_user_message(body)
            if user_msg:
                rag_results = self.tool_registry.run_tool("memory", user_msg)
                if rag_results:
                    body = self._inject_tool_context(body, f"[memory]: {rag_results}")
                    self.wire.log(
                        direction="internal",
                        role="tool",
                        content=f"memory/RAG injected ({len(rag_results)} chars)",
                        model="",
                        conversation_id="",
                    )

        return body

    def _inject_generation_params(self, body: dict) -> dict:
        """
        Inject runtime generation parameters into the request body.

        Reads from runtime_config.yaml so changes apply immediately without restart.
        Only injects keys that are explicitly set (non-None). Frontend values are
        NOT overridden — if the frontend already sent temperature, we leave it alone
        unless the runtime config is set to force it.

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

    def _inject_model_options(self, body: dict) -> dict:
        """
        Inject per-model Ollama options from config and runtime_config.

        Priority (highest wins):
          1. runtime_config model_options  — set via UI, hot-reloaded
          2. config.yaml models.<name>.options  — static, requires restart

        runtime_config structure (flat num_gpu per model):
            runtime:
              model_options:
                llama3.2:3b: 0      # CPU
                mistral:7b: 99      # all GPU layers
                llama2:70b: 20      # partial offload
        """
        from beigebox.config import get_runtime_config
        model = body.get("model", "")
        if not model:
            return body

        # Layer 1: static config options
        model_cfg = self.cfg.get("models", {}).get(model, {})
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

    def _apply_window_config(self, body: dict) -> tuple[dict, bool]:
        """
        Apply per-pane window config sent by the frontend as ``_window_config``.

        The frontend embeds a ``_window_config`` dict in the request body for any
        pane that has non-default settings.  These override all other config layers
        (global runtime config, per-model options) since they represent an explicit
        per-session user choice.  The key is stripped before the body is forwarded.

        Supported fields (all optional, null/missing = skip):
            temperature, top_p, top_k, num_ctx, max_tokens,
            repeat_penalty, seed  — top-level body params
            num_gpu               — goes into body["options"]["num_gpu"]
            force_reload          — if true, caller should evict model before forwarding
            system_prompt         — handled by the frontend (not re-injected here)

        Returns: (body, force_reload) — force_reload signals the caller to evict
        the model from Ollama first so it reloads fresh with the new options.
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

    async def _evict_model(self, model: str) -> None:
        """
        Evict a model from Ollama by sending keep_alive=0.

        Ollama unloads the model immediately; the next request will reload it
        fresh, picking up any new options (e.g. num_gpu).  Fires a best-effort
        request to the native /api/generate endpoint — errors are logged but do
        not block the follow-up chat request.
        """
        if not model:
            return
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                await client.post(
                    f"{self.backend_url}/api/generate",
                    json={"model": model, "keep_alive": 0},
                )
            logger.info("Evicted model '%s' from Ollama (will reload with new options)", model)
        except Exception as e:
            logger.warning("Failed to evict model '%s': %s", model, e)

    async def _run_operator_pre_hook(self, body: dict) -> dict:
        """Run operator as a pre-processing pass — enriches the last user message before it reaches the LLM."""
        pre_hook_cfg = self.cfg.get("operator", {}).get("pre_hook", {})
        if not pre_hook_cfg.get("enabled", False):
            return body

        user_msg = self._get_latest_user_message(body)
        if not user_msg:
            return body

        from beigebox.agents.operator import Operator
        import asyncio

        op = Operator(
            vector_store=self.vector,
            blob_store=self.blob_store,
            model_override=pre_hook_cfg.get("model"),
            max_iterations_override=pre_hook_cfg.get("max_iterations", 3),
            pre_hook=True,
        )

        loop = asyncio.get_event_loop()
        try:
            enriched = await loop.run_in_executor(None, op.run, user_msg)
        except Exception as e:
            logger.warning("operator pre_hook failed: %s", e)
            return body

        if not enriched or enriched.strip() == user_msg.strip():
            return body

        # Replace last user message with enriched version
        messages = list(body.get("messages", []))
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                messages[i] = {**messages[i], "content": enriched}
                break

        self.wire.log(
            direction="internal", role="proxy",
            content=f"pre_hook enriched: {user_msg[:120]!r} → {enriched[:120]!r}",
            model=op._model,
        )
        return {**body, "messages": messages}

    async def _run_operator_post_hook(self, body: dict, response_text: str) -> None:
        """Fire-and-forget operator pass that runs after the LLM has responded.

        Receives the completed response so the operator can extract facts,
        write notes, or trigger side effects.  Never modifies the response.
        Tool I/O is dumped to workspace/out/.posthook/ — not stored in ChromaDB.
        """
        post_hook_cfg = self.cfg.get("operator", {}).get("post_hook", {})
        if not post_hook_cfg.get("enabled", False):
            return

        user_msg = self._get_latest_user_message(body)
        if not user_msg or not response_text.strip():
            return

        from beigebox.agents.operator import Operator
        import asyncio

        op = Operator(
            vector_store=self.vector,
            blob_store=self.blob_store,
            model_override=post_hook_cfg.get("model"),
            max_iterations_override=post_hook_cfg.get("max_iterations", 3),
            post_hook=True,
        )

        # Compose context: user question + assistant response
        combined = (
            f"[USER MESSAGE]\n{user_msg}\n\n"
            f"[ASSISTANT RESPONSE]\n{response_text}"
        )

        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, op.run, combined)
        except Exception as e:
            logger.warning("operator post_hook failed: %s", e)

    def _inject_system_context(self, body: dict) -> dict:
        """Inject system_context.md content into the request (hot-reloaded)."""
        try:
            from beigebox.system_context import inject_system_context
            return inject_system_context(body, self.cfg)
        except Exception as e:
            logger.debug("system_context injection skipped: %s", e)
            return body

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------

    async def forward_chat_completion(self, body: dict) -> dict:
        """Forward a non-streaming chat completion request."""
        import time as _time
        _t0 = _time.monotonic()
        _stages: dict[str, float] = {}

        model = self._get_model(body)
        conversation_id = self._extract_conversation_id(body)

        # Z-command parsing
        zcmd, body = self._process_z_command(body)
        _stages["z_command"] = (_time.monotonic() - _t0) * 1000

        # Handle z: help — return help text directly without calling LLM
        if zcmd.is_help:
            return {
                "choices": [{"message": {"role": "assistant", "content": HELP_TEXT}}],
                "model": "beigebox",
            }

        # Handle z: fork — branch this conversation into a new ID
        if zcmd.is_fork:
            from uuid import uuid4 as _uuid4
            new_id = _uuid4().hex
            try:
                n = self.sqlite.fork_conversation(conversation_id, new_id)
                fork_msg = (
                    f"🔀 **Forked.** Copied {n} message(s) into new conversation `{new_id[:12]}…`\n\n"
                    f"Open a new chat and set the conversation ID to `{new_id}` to continue on the branch, "
                    f"or use the Conversations tab to find it.\n\n"
                    f"This conversation continues unchanged."
                )
            except Exception as _e:
                logger.error("z: fork failed: %s", _e)
                fork_msg = f"❌ Fork failed: {_e}"
            self.wire.log(
                direction="internal",
                role="system",
                content=f"z: fork → new_id={new_id} source={conversation_id}",
                model="beigebox",
                conversation_id=conversation_id,
            )
            return {
                "choices": [{"message": {"role": "assistant", "content": fork_msg}}],
                "model": "beigebox",
            }

        # Pre-request hooks
        _t_hooks = _time.monotonic()
        if self.hook_manager:
            context = self._build_hook_context(body, conversation_id, model, None)
            body = self.hook_manager.run_pre_request(body, context)
        _stages["pre_hooks"] = (_time.monotonic() - _t_hooks) * 1000

        # Check for hook-initiated block (e.g. prompt injection detection)
        if "_beigebox_block" in body:
            block = body["_beigebox_block"]
            self.wire.log(
                direction="internal",
                role="system",
                content=f"request blocked: reason={block.get('reason')} score={block.get('score')} patterns={block.get('patterns')}",
                model="prompt-injection-guard",
                conversation_id=conversation_id,
            )
            return {
                "choices": [{"message": {"role": "assistant", "content": block.get("message", "Request blocked.")}}],
                "model": "beigebox",
            }

        # Check if synthetic (tagged by hook)
        is_synthetic = self._is_synthetic(body)

        # Run forced tools from z-command
        if zcmd.active and zcmd.tools:
            tool_results = self._run_forced_tools(zcmd, self._get_latest_user_message(body))
            if tool_results:
                body = self._inject_tool_context(body, tool_results)

        # Hybrid routing: session cache → z-command → routing rules → embedding classifier → decision LLM
        _t_route = _time.monotonic()
        body, decision = await self._hybrid_route(body, zcmd, conversation_id)
        model = body.get("model", model)  # Update model after routing
        _stages["routing"] = (_time.monotonic() - _t_route) * 1000

        # Run tools forced by routing rules
        _rule_tools = body.pop(BB_FORCED_TOOLS, None)
        if _rule_tools and self.tool_registry:
            _user_msg = self._get_latest_user_message(body)
            _rule_tool_results = []
            for _tool_name in _rule_tools:
                _result = self.tool_registry.run_tool(_tool_name, _user_msg)
                if _result:
                    _rule_tool_results.append(f"[{_tool_name}]: {_result}")
            if _rule_tool_results:
                body = self._inject_tool_context(body, "\n".join(_rule_tool_results))

        # Strip any remaining internal BB keys that routing rules may have written
        body.pop(BB_SKIP_SEMANTIC_CACHE, None)

        # Auto-summarize if conversation exceeds token budget
        try:
            from beigebox.summarizer import maybe_summarize
            body["messages"] = await maybe_summarize(body.get("messages", []), self.cfg)
        except Exception as _e:
            logger.debug("auto_summarizer skipped: %s", _e)

        # Operator pre-hook — enrich/modify message before it reaches the LLM
        body = await self._run_operator_pre_hook(body)

        # Inject system context (hot-reloaded from system_context.md)
        body = self._inject_system_context(body)

        # Inject runtime generation parameters (temperature, top_p, etc.)
        body = self._inject_generation_params(body)

        # Inject per-model Ollama options (num_gpu, num_ctx, etc.)
        body = self._inject_model_options(body)

        # Apply per-pane window config (highest priority — overrides all other layers)
        body, _force_reload = self._apply_window_config(body)
        if _force_reload:
            await self._evict_model(body.get("model", ""))

        # Log incoming user messages (skip synthetic)
        if not is_synthetic:
            self._log_messages(conversation_id, body.get("messages", []), model)

        # Forward to backend — use router if available, otherwise direct
        cost_usd = None
        backend_name = "direct"
        _t_backend = _time.monotonic()

        if self.backend_router:
            response = await self.backend_router.forward(body)
            _stages["backend"] = (_time.monotonic() - _t_backend) * 1000
            if not response.ok:
                # Return error as a chat response so clients handle it gracefully
                return {
                    "choices": [{"message": {"role": "assistant",
                                             "content": f"[BeigeBox] Backend error: {response.error}"}}],
                    "model": model,
                }
            data = response.data
            cost_usd = response.cost_usd
            backend_name = response.backend_name
        else:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.backend_url}/v1/chat/completions",
                    json=body,
                )
                resp.raise_for_status()
                data = resp.json()
            _stages["backend"] = (_time.monotonic() - _t_backend) * 1000

        # WASM transform (non-streaming) — operates on full response dict
        wasm_mod = decision.wasm_module if decision else ""
        if not is_synthetic and wasm_mod:
            data = await self.wasm_runtime.transform_response(wasm_mod, data)
            self.wire.log(
                direction="internal",
                role="wasm",
                content=f"transform applied: module={wasm_mod}",
                model=model,
                conversation_id=conversation_id,
            )

        # Log assistant response (skip synthetic)
        if not is_synthetic:
            choices = data.get("choices", [])
            if choices:
                assistant_content = choices[0].get("message", {}).get("content", "")
                response_latency = response.latency_ms if self.backend_router and response.ok else None
                self._log_response(conversation_id, assistant_content, model, cost_usd=cost_usd, latency_ms=response_latency)

        # Post-response hooks
        _t_post = _time.monotonic()
        if self.hook_manager and not is_synthetic:
            context = self._build_hook_context(body, conversation_id, model, decision)
            data = self.hook_manager.run_post_response(body, data, context)
        _stages["post_hooks"] = (_time.monotonic() - _t_post) * 1000

        # Operator post-hook — fire-and-forget, does not modify response
        if assistant_content:
            import asyncio as _asyncio
            _asyncio.ensure_future(self._run_operator_post_hook(body, assistant_content))

        # Emit timing summary to wiretap
        total_ms = (_time.monotonic() - _t0) * 1000
        cost_str = f" · ${cost_usd:.6f}" if cost_usd else ""
        self.wire.log(
            direction="internal",
            role="system",
            content=f"completed via '{backend_name}' · {total_ms:.0f}ms total{cost_str}",
            model=model,
            conversation_id=conversation_id,
            latency_ms=total_ms,
            timing=_stages,
        )

        return data

    async def forward_chat_completion_stream(self, body: dict):
        """
        Forward a streaming chat completion request.
        Yields SSE chunks to the client while buffering the full response for logging.
        """
        import time as _time
        _t0 = _time.monotonic()
        _stages: dict[str, float] = {}

        model = self._get_model(body)
        conversation_id = self._extract_conversation_id(body)

        # Z-command parsing
        zcmd, body = self._process_z_command(body)
        _stages["z_command"] = (_time.monotonic() - _t0) * 1000

        # Handle z: help — return as a single SSE chunk
        if zcmd.is_help:
            chunk = json.dumps({
                "choices": [{"delta": {"content": HELP_TEXT}, "index": 0}],
                "model": "beigebox",
            })
            yield f"data: {chunk}\n"
            yield "data: [DONE]\n"
            return

        # Pre-request hooks
        _t_hooks = _time.monotonic()
        if self.hook_manager:
            context = self._build_hook_context(body, conversation_id, model, None)
            body = self.hook_manager.run_pre_request(body, context)
        _stages["pre_hooks"] = (_time.monotonic() - _t_hooks) * 1000

        # Check for hook-initiated block (e.g. prompt injection detection)
        if "_beigebox_block" in body:
            block = body["_beigebox_block"]
            self.wire.log(
                direction="internal",
                role="system",
                content=f"stream blocked: reason={block.get('reason')} score={block.get('score')}",
                model="prompt-injection-guard",
                conversation_id=conversation_id,
            )
            import json as _json
            chunk = _json.dumps({
                "choices": [{"delta": {"content": block.get("message", "Request blocked.")}, "index": 0}],
                "model": "beigebox",
            })
            yield f"data: {chunk}\n"
            yield "data: [DONE]\n"
            return

        # Check if synthetic
        is_synthetic = self._is_synthetic(body)

        # Run forced tools from z-command
        if zcmd.active and zcmd.tools:
            tool_results = self._run_forced_tools(zcmd, self._get_latest_user_message(body))
            if tool_results:
                body = self._inject_tool_context(body, tool_results)

        # Hybrid routing
        _t_route = _time.monotonic()
        body, decision = await self._hybrid_route(body, zcmd, conversation_id)
        model = body.get("model", model)
        _stages["routing"] = (_time.monotonic() - _t_route) * 1000

        # Run tools forced by routing rules (after routing so model is resolved)
        _rule_tools = body.pop(BB_FORCED_TOOLS, None)
        if _rule_tools and self.tool_registry:
            _user_msg = self._get_latest_user_message(body)
            _rule_tool_results = []
            for _tool_name in _rule_tools:
                _result = self.tool_registry.run_tool(_tool_name, _user_msg)
                if _result:
                    _rule_tool_results.append(f"[{_tool_name}]: {_result}")
            if _rule_tool_results:
                body = self._inject_tool_context(body, "\n".join(_rule_tool_results))

        # Auto-summarize if conversation exceeds token budget
        try:
            from beigebox.summarizer import maybe_summarize
            body["messages"] = await maybe_summarize(body.get("messages", []), self.cfg)
        except Exception as _e:
            logger.debug("auto_summarizer skipped: %s", _e)

        # Operator pre-hook — enrich/modify message before it reaches the LLM
        body = await self._run_operator_pre_hook(body)

        # Inject system context (hot-reloaded from system_context.md)
        body = self._inject_system_context(body)

        # Inject runtime generation parameters (temperature, top_p, etc.)
        body = self._inject_generation_params(body)

        # Inject per-model Ollama options (num_gpu, num_ctx, etc.)
        body = self._inject_model_options(body)

        # Apply per-pane window config (highest priority — overrides all other layers)
        body, _force_reload = self._apply_window_config(body)
        if _force_reload:
            await self._evict_model(body.get("model", ""))

        # Emit routing metadata so the UI can show which model/path is handling this
        if not is_synthetic:
            routing_meta = {"bb_type": "routing", "model": model}
            if zcmd.active and zcmd.route:
                routing_meta["via"] = zcmd.route  # e.g. "operator"
            yield f"data: {json.dumps(routing_meta)}\n\n"

        # Semantic cache lookup (before logging or backend call)
        user_message = self._get_latest_user_message(body)
        _skip_sem_cache = body.pop(BB_SKIP_SEMANTIC_CACHE, False)
        if not is_synthetic and not _skip_sem_cache:
            cache_hit = await self.semantic_cache.lookup(user_message)
            if cache_hit is not None:
                cached_text, cached_model = cache_hit
                self.wire.log(
                    direction="internal", role="system",
                    content=f"semantic cache hit — serving cached response (model={cached_model})",
                    model=cached_model, conversation_id=conversation_id,
                )
                chunk = json.dumps({
                    "choices": [{"delta": {"content": cached_text}, "finish_reason": "stop", "index": 0}],
                    "model": cached_model,
                })
                yield f"data: {chunk}\n"
                yield "data: [DONE]\n"
                return

        # Log incoming user messages (skip synthetic)
        if not is_synthetic:
            self._log_messages(conversation_id, body.get("messages", []), model)

        # Buffer for the full response
        full_response = []
        stream_cost_usd: float | None = None
        backend_name = "direct"
        _t_backend = _time.monotonic()
        _first_chunk = True  # TTFT sentinel
        # When a WASM module is active, buffer the stream and re-emit after
        # transformation so the client receives the transformed content.
        wasm_mod = decision.wasm_module if decision else ""
        _wasm_buffer_mode = bool(wasm_mod and self.wasm_runtime.enabled)

        if self.backend_router:
            # Stream via multi-backend router
            async for line in self.backend_router.forward_stream(body):
                if not line:
                    continue
                # Intercept cost sentinel — never forward to client
                if line.startswith(_COST_SENTINEL_PREFIX):
                    try:
                        stream_cost_usd = float(line[len(_COST_SENTINEL_PREFIX):])
                        logger.debug("Stream cost captured: $%.6f", stream_cost_usd)
                    except (ValueError, TypeError):
                        pass
                    continue
                if _first_chunk:
                    _stages["ttft_ms"] = (_time.monotonic() - _t_backend) * 1000
                    _first_chunk = False
                # In WASM buffer mode, collect but don't yield to client yet
                if not _wasm_buffer_mode:
                    yield line + "\n"
                # Parse to buffer content
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data_str)
                        delta = (
                            chunk.get("choices", [{}])[0]
                            .get("delta", {})
                            .get("content", "")
                        )
                        if delta:
                            full_response.append(delta)
                    except (json.JSONDecodeError, IndexError):
                        pass
        else:
            # Direct backend (legacy single-backend path)
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self.backend_url}/v1/chat/completions",
                    json=body,
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        if _first_chunk:
                            _stages["ttft_ms"] = (_time.monotonic() - _t_backend) * 1000
                            _first_chunk = False
                        # In WASM buffer mode, collect but don't yield to client yet
                        if not _wasm_buffer_mode:
                            yield line + "\n"
                        # Parse to buffer content
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str.strip() == "[DONE]":
                                continue
                            try:
                                chunk = json.loads(data_str)
                                delta = (
                                    chunk.get("choices", [{}])[0]
                                    .get("delta", {})
                                    .get("content", "")
                                )
                                if delta:
                                    full_response.append(delta)
                            except (json.JSONDecodeError, IndexError):
                                pass

        # Wall-clock stream duration
        _stages["backend"] = (_time.monotonic() - _t_backend) * 1000
        total_ms = (_time.monotonic() - _t0) * 1000

        # Log the complete response after streaming finishes (skip synthetic)
        if not is_synthetic:
            complete_text = "".join(full_response)
            # WASM transform — runs on assembled text in both modes.
            # In buffer mode the transformed text is re-emitted to the client below.
            if complete_text and wasm_mod:
                complete_text = await self.wasm_runtime.transform_text(wasm_mod, complete_text)
                self.wire.log(
                    direction="internal",
                    role="wasm",
                    content=f"transform applied: module={wasm_mod}",
                    model=model,
                    conversation_id=conversation_id,
                )
            # Re-emit transformed content to client (WASM buffer mode only)
            if _wasm_buffer_mode and complete_text:
                emit_chunk = json.dumps({
                    "choices": [{"delta": {"content": complete_text}, "finish_reason": "stop", "index": 0}],
                    "model": model,
                })
                yield f"data: {emit_chunk}\n"
                yield "data: [DONE]\n"
            if complete_text:
                self._log_response(
                    conversation_id,
                    complete_text,
                    model,
                    cost_usd=stream_cost_usd,
                    latency_ms=round(_stages["backend"], 1),
                    ttft_ms=round(_stages["ttft_ms"], 1) if "ttft_ms" in _stages else None,
                )
                # Store in semantic cache for future similar queries
                if not is_synthetic:
                    self.semantic_cache.store(user_message, complete_text, model)

                # Operator post-hook — fire-and-forget, does not modify response
                import asyncio as _asyncio
                _asyncio.ensure_future(self._run_operator_post_hook(body, complete_text))

        # Emit timing summary to wiretap
        cost_str = f" · ${stream_cost_usd:.6f}" if stream_cost_usd else ""
        ttft_str = f" · TTFT {_stages['ttft_ms']:.0f}ms" if "ttft_ms" in _stages else ""
        self.wire.log(
            direction="internal",
            role="system",
            content=f"stream completed via '{backend_name}' · {total_ms:.0f}ms total{cost_str}{ttft_str}",
            model=model,
            conversation_id=conversation_id,
            latency_ms=total_ms,
            timing=_stages,
        )

    async def list_models(self) -> dict:
        """Forward /v1/models request to backend(s), optionally rewriting model names.

        Always fetches from the direct Ollama backend URL first so local models
        are visible regardless of whether multi-backend routing is enabled.
        Router backends (e.g. pinned OpenRouter models) are merged on top.
        """
        seen: set[str] = set()
        all_models: list[dict] = []

        # Always include local Ollama models
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.backend_url}/v1/models")
                resp.raise_for_status()
                for m in resp.json().get("data", []):
                    mid = m.get("id") or m.get("name") or ""
                    if mid and mid not in seen:
                        seen.add(mid)
                        all_models.append(m)
        except Exception:
            pass

        # Merge in router backends (pinned OR models, vLLM, etc.)
        if self.backend_router:
            router_data = await self.backend_router.list_all_models()
            for m in router_data.get("data", []):
                mid = m.get("id") or m.get("name") or ""
                if mid and mid not in seen:
                    seen.add(mid)
                    all_models.append(m)

        data = {"object": "list", "data": all_models}
        data = self._transform_model_names(data)
        return data
    
    def _transform_model_names(self, data: dict) -> dict:
        """
        Rewrite model names in the response based on config.
        Supports two modes:
          1. advertise: prepend "beigebox:" to all model names
          2. hidden: don't advertise beigebox's presence
        """
        cfg = self.cfg.get("model_advertising", {})
        mode = cfg.get("mode", "hidden")  # "advertise" or "hidden"
        prefix = cfg.get("prefix", "beigebox:")
        
        if mode == "hidden":
            # Don't modify model names — just pass through
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
