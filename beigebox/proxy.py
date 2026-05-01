"""
BeigeBox Proxy — Core request pipeline

LICENSING: Dual-licensed under AGPL-3.0 (free) and Commercial License (proprietary).
See LICENSE.md and COMMERCIAL_LICENSE.md for details.

Intercepts OpenAI-compatible requests, logs both sides, forwards to backend.
Handles streaming (SSE) transparently.

The agentic decision layer (z-commands, hybrid routing, decision LLM,
embedding classifier, agentic scorer, routing rules) was deleted in v3.
Agent loops moved out of the proxy and now run in whatever MCP client
is driving — BeigeBox is back to being a thin OpenAI-compatible proxy
with memory, observability, and an MCP tool server.
"""
import json
import logging
import asyncio
import time
import fnmatch
import copy
from collections import deque
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from beigebox.config import get_config, get_runtime_config
from beigebox.storage.models import Message
from beigebox.storage.repos.conversations import ConversationRepo
from beigebox.storage.vector_store import VectorStore
from beigebox.hooks import HookManager
from beigebox.wiretap import WireLog
from beigebox.wasm_runtime import WasmRuntime
from beigebox.logging import (
    log_request_started,
    log_request_completed, log_payload_event,
    log_hook_execution, log_extraction_attempt,
)
from beigebox.backends.openrouter import _COST_SENTINEL_PREFIX
from beigebox.cache import ToolResultCache
from beigebox.aliases import AliasResolver
from beigebox.guardrails import Guardrails
from beigebox.response_normalizer import (
    estimate_tokens as _estimate_tokens,
    normalize_response,
    normalize_stream_delta,
)
from beigebox.capture import (
    CaptureContext,
    CapturedRequest,
    CapturedResponse,
    attach_response_timing,
)
from beigebox.validation.format import ResponseValidator
from beigebox.security.anomaly_detector import APIAnomalyDetector

logger = logging.getLogger(__name__)
class Proxy:
    """Transparent proxy between frontend and Ollama backend."""
    def __init__(
        self,
        conversations: ConversationRepo,
        vector: VectorStore,
        hook_manager: HookManager | None = None,
        tool_registry=None,
        backend_router=None,
        blob_store=None,
        egress_hooks=None,
        extraction_detector=None,
        wire_events=None,
        capture=None,
    ):
        self.conversations = conversations
        self.vector = vector
        # CaptureFanout — single chokepoint for request/response telemetry.
        # When provided (production), the proxy emits CapturedRequest /
        # CapturedResponse envelopes here instead of calling _log_messages /
        # _log_response directly. When None (legacy tests), the proxy falls
        # back to the old per-call paths.
        self.capture = capture
        self.blob_store = blob_store
        self.hook_manager = hook_manager
        self.tool_registry = tool_registry
        self.backend_router = backend_router  # MultiBackendRouter or None
        self.extraction_detector = extraction_detector  # ExtractionDetector or None
        self.cfg = get_config()
        self.backend_url = self.cfg["backend"]["url"].rstrip("/")
        self.timeout = self.cfg["backend"].get("timeout", 120)
        self.default_model = self.cfg["backend"].get("default_model", "")
        self.log_enabled = self.cfg["storage"].get("log_conversations", True)
        # Cost tracking config
        self._cost_tracking = self.cfg.get("cost_tracking", {}).get("enabled", False)
        # Wire log — structured tap of everything on the line
        wire_cfg = self.cfg.get("wiretap", {})
        wire_path = wire_cfg.get("path", "./data/wire.jsonl")
        self.wire = WireLog(
            wire_path,
            wire_events=wire_events,
            egress_hooks=egress_hooks or [],
            max_lines=int(wire_cfg.get("max_lines", 100_000)),
            rotation_enabled=bool(wire_cfg.get("rotation_enabled", True)),
        )
        # WASM transform runtime
        self.wasm_runtime = WasmRuntime(self.cfg)
        if self.wasm_runtime.enabled:
            logger.info("WasmRuntime: %d module(s) loaded: %s", len(self.wasm_runtime.list_modules()), self.wasm_runtime.list_modules())
        # (Session routing cache removed in v3 — its only writer was the routing
        # decision layer, which was deleted. Backend selection is now determined
        # solely by body['model'] passed in by the caller.)

        # Tool result cache (deterministic tools only; see beigebox/cache.py)
        self.tool_cache = ToolResultCache(
            ttl=self.cfg.get("tool_cache", {}).get("ttl_seconds", 300.0),
        )
        # Model alias resolver — virtual names like "fast", "smart" → real model IDs
        self.alias_resolver = AliasResolver(self.cfg)
        # Guardrails — input/output content filtering
        self.guardrails = Guardrails(self.cfg)
        # Response format validator — optional, non-blocking
        self.response_validator = ResponseValidator(self.cfg)
        # Request inspector — ring buffer of last N outbound payloads
        self._request_inspector: deque = deque(maxlen=5)
        self._inspector_counter: int = 0
        # API Anomaly Detection — behavioral analysis for token extraction attacks
        anom_cfg = self.cfg.get("security", {}).get("api_anomaly", {})
        if anom_cfg.get("enabled", True):
            self.anomaly_detector = APIAnomalyDetector(
                window_seconds=anom_cfg.get("baseline_window_seconds", 300),
                request_rate_threshold=anom_cfg.get("request_rate_threshold", 5),
                error_rate_threshold=anom_cfg.get("error_rate_threshold", 0.30),
                model_switch_threshold=anom_cfg.get("model_switch_threshold", 8),
                latency_z_threshold=anom_cfg.get("latency_z_threshold", 3.0),
                payload_min_chars=anom_cfg.get("payload_min_chars", 50),
                payload_max_bytes=anom_cfg.get("payload_max_bytes", 100000),
                ip_instability_threshold=anom_cfg.get("ip_instability_threshold", 5),
            )
            logger.info(
                "APIAnomalyDetector initialized (mode=%s, window=%ds)",
                anom_cfg.get("detection_mode", "warn"),
                anom_cfg.get("baseline_window_seconds", 300),
            )
        else:
            self.anomaly_detector = None
        self._anomaly_cfg = anom_cfg
    # ------------------------------------------------------------------
    # Request helpers
    # ------------------------------------------------------------------

    def _extract_conversation_id(self, body: dict) -> str:
        """
        Try to extract a conversation ID from the request.
        Open WebUI doesn't always send one, so we generate if missing.

        A stable ID is required so wiretap rows / vector-store messages /
        sqlite log entries can be correlated to a single session. We only
        generate when messages are present (skip empty bodies from
        health-check-style callers that don't represent real sessions).
        """
        conv_id = body.get("conversation_id") or body.get("session_id") or ""
        if not conv_id:
            messages = body.get("messages", [])
            if messages:
                conv_id = uuid4().hex
        return conv_id

    def _get_model(self, body: dict) -> str:
        """Extract model from request, resolve any alias, fall back to config default."""
        raw = body.get("model") or self.default_model
        return self.alias_resolver.resolve(raw)

    def _get_latest_user_message(self, body: dict) -> str:
        """Extract the last user message from the request."""
        messages = body.get("messages", [])
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                # OpenAI vision format sends content as a list of typed parts.
                # JSON-serialise so downstream code always receives a plain string.
                return content if isinstance(content, str) else json.dumps(content)
        return ""

    def _is_synthetic(self, body: dict) -> bool:
        """Check if this request was tagged as synthetic by a hook."""
        return body.get("_beigebox_synthetic", False)

    def _dedupe_consecutive_messages(self, body: dict) -> dict:
        """
        Drop consecutive (role, content) duplicates from body.messages.

        A buggy or replaying client (e.g. UI double-fire) can send the same
        user message twice in one request body, which then propagates into
        every following turn (the dup sticks in the conversation history).
        This collapses adjacent duplicates so the backend, the wire tap, the
        SQLite store, and the vector index all see a clean sequence.

        Mutates body['messages'] in place and returns body.
        """
        messages = body.get("messages", [])
        if len(messages) < 2:
            return body
        cleaned: list[dict] = []
        prev_key: tuple[str, str] | None = None
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            content_str = content if isinstance(content, str) else json.dumps(content)
            key = (role, content_str)
            if key == prev_key:
                logger.info("dedupe: dropped consecutive duplicate role=%s len=%d",
                            role, len(content_str))
                continue
            cleaned.append(msg)
            prev_key = key
        if len(cleaned) != len(messages):
            body["messages"] = cleaned
        return body

    # ------------------------------------------------------------------
    # Capture pipeline helpers (v1.4)
    # ------------------------------------------------------------------
    # These build the CapturedRequest / CapturedResponse envelopes for the
    # CaptureFanout. The fanout is only wired in production (main.py
    # lifespan); tests that instantiate Proxy without it fall through to
    # the legacy _log_messages / _log_response paths below.

    def _build_capture_context(
        self,
        conversation_id: str,
        model: str,
        backend: str = "",
        run_id: str | None = None,
        user_id: str | None = None,
    ) -> CaptureContext:
        return CaptureContext(
            conv_id=conversation_id,
            turn_id=uuid4().hex,
            model=model,
            backend=backend,
            started_at=datetime.now(timezone.utc),
            run_id=run_id,
            user_id=user_id,
        )

    def _build_captured_request(
        self,
        ctx: CaptureContext,
        body: dict,
        response,
    ) -> CapturedRequest:
        """Construct a CapturedRequest from the body + (optional) NormalizedRequest.

        When ``response`` is the BackendResponse from the router, prefer the
        NR it stashed (full transforms/errors/target). When unavailable
        (direct httpx fallback or upstream error before router returned),
        fall back to a best-effort envelope built from the request body
        alone.
        """
        nr = getattr(response, "normalized_request", None) if response is not None else None
        messages = body.get("messages", []) if isinstance(body, dict) else []
        if nr is not None:
            return CapturedRequest.from_normalized(nr, ctx, messages)
        return CapturedRequest(
            ctx=ctx,
            target="(unavailable)",
            transforms=[],
            errors=[],
            messages=list(messages),
            has_tools=bool(body.get("tools")) if isinstance(body, dict) else False,
            stream=bool(body.get("stream")) if isinstance(body, dict) else False,
        )

    def _capture_stream_response(
        self,
        ctx: CaptureContext,
        body: dict,
        full_response: list[str],
        stages: dict[str, float],
        backend_name: str,
        cost_usd: float | None,
        prompt_tokens: int,
        *,
        outcome: str,
        error: BaseException | None,
        req_already_captured: bool,
    ) -> None:
        """Emit the streaming response capture (success or failure path).

        On normal completion, synthesizes a NormalizedResponse from the
        accumulated text + token estimates so the row carries the same
        fields the non-streaming path produces. On failure outcomes
        (stream_aborted / client_disconnect), uses ``from_partial`` so
        partial content is preserved with the right outcome marker.
        """
        if self.capture is None:
            return
        ttft_ms = stages.get("ttft_ms")
        ctx_after = attach_response_timing(ctx, ttft_ms=ttft_ms)
        # Make sure backend got populated in ctx_after; carry the resolved
        # name through if the caller already knows it.
        if backend_name and ctx_after.backend != backend_name:
            ctx_after = CaptureContext(
                conv_id=ctx_after.conv_id,
                turn_id=ctx_after.turn_id,
                model=ctx_after.model,
                backend=backend_name,
                started_at=ctx_after.started_at,
                run_id=ctx_after.run_id,
                request_id=ctx_after.request_id,
                ended_at=ctx_after.ended_at,
                ttft_ms=ctx_after.ttft_ms,
                latency_ms=ctx_after.latency_ms,
                user_id=ctx_after.user_id,
            )

        # Belt-and-suspenders: emit a request envelope if the chunk loop
        # never reached its first chunk (upstream errored before any
        # output arrived).
        if not req_already_captured:
            try:
                self.capture.capture_request(
                    self._build_captured_request(ctx, body, None)
                )
            except Exception as _exc:
                logger.warning("stream capture_request fallback failed: %s", _exc)

        complete_text = "".join(full_response)
        try:
            if outcome == "ok":
                synth = normalize_response({
                    "choices": [{
                        "message": {"role": "assistant", "content": complete_text},
                        "finish_reason": "stop",
                    }],
                    "usage": {
                        "prompt_tokens": prompt_tokens or 0,
                        "completion_tokens": _estimate_tokens(complete_text),
                    },
                })
                # OpenRouter cost arrives via the cost sentinel (not the
                # synthesized data dict) — slot it onto the normalized
                # response so the captured row's cost_usd is populated.
                synth.cost_usd = cost_usd
                self.capture.capture_response(
                    CapturedResponse.from_normalized(synth, ctx_after, "ok")
                )
            else:
                self.capture.capture_response(
                    CapturedResponse.from_partial(
                        ctx=ctx_after,
                        outcome=outcome,
                        content=complete_text,
                        error=error,
                    )
                )
        except Exception as _exc:
            logger.warning("stream capture_response failed: %s", _exc)

    async def _log_messages(self, conversation_id: str, messages: list[dict], model: str):
        """Store the user messages from the request."""
        if not self.log_enabled:
            return
        loop = asyncio.get_event_loop()
        # Defense in depth: collapse consecutive identical (role, content) pairs
        # before logging. A buggy or replaying client can send the same user
        # message twice in one body — without this, every following turn would
        # carry the dup forward and pollute the conversation store, the wire
        # tap, the vector index, and the token accounting.
        _prev_key: tuple[str, str] | None = None
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            # Skip system messages: they're injected by BeigeBox itself and
            # re-logged on every turn, which would pollute the conversation store
            # and double-count tokens.
            if not content or role == "system":
                continue
            content_str = content if isinstance(content, str) else json.dumps(content)
            _key = (role, content_str)
            if _key == _prev_key:
                logger.debug("_log_messages: dropping consecutive duplicate role=%s len=%d",
                             role, len(content_str))
                continue
            _prev_key = _key
            tokens = _estimate_tokens(content_str)
            message = Message(
                conversation_id=conversation_id,
                role=role,
                content=content_str,
                model=model,
                token_count=tokens,
            )
            try:
                await loop.run_in_executor(None, self.conversations.store_message, message)
            except Exception as e:
                logger.warning("_log_messages: SQLite store_message failed (conv=%s role=%s): %s",
                               conversation_id, role, e)
            # Wire tap
            try:
                self.wire.log(
                    direction="inbound",
                    role=role,
                    content=message.content,
                    model=model,
                    conversation_id=conversation_id,
                    token_count=tokens,
                )
            except Exception as e:
                logger.debug("_log_messages: wire.log failed: %s", e)
            # Embed async in background — avoids blocking the event loop
            _t = asyncio.create_task(self.vector.store_message_async(
                message_id=message.id,
                conversation_id=conversation_id,
                role=role,
                content=message.content,
                model=model,
                timestamp=message.timestamp,
            ))
            _t.add_done_callback(
                lambda t: t.exception() and logger.warning("vector embed failed: %s", t.exception())
            )

    async def _log_response(self, conversation_id: str, content: str, model: str, cost_usd: float | None = None, latency_ms: float | None = None, ttft_ms: float | None = None):
        """Store the assistant response."""
        if not self.log_enabled or not content.strip():
            return
        loop = asyncio.get_event_loop()
        tokens = _estimate_tokens(content)
        message = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            model=model,
            token_count=tokens,
        )
        try:
            await loop.run_in_executor(
                None,
                lambda: self.conversations.store_message(message, cost_usd=cost_usd, latency_ms=latency_ms, ttft_ms=ttft_ms),
            )
        except Exception as e:
            logger.warning("_log_response: SQLite store_message failed (conv=%s model=%s): %s",
                           conversation_id, model, e)
        # Wire tap
        cost_info = f" cost=${cost_usd:.6f}" if cost_usd else ""
        try:
            self.wire.log(
                direction="outbound",
                role="assistant",
                content=content,
                model=model,
                conversation_id=conversation_id,
                token_count=tokens,
            )
        except Exception as e:
            logger.debug("_log_response: wire.log failed: %s", e)
        if cost_usd:
            self.wire.log(
                direction="internal",
                role="system",
                content=f"cost_usd={cost_usd:.6f} model={model}",
                model="cost-tracker",
                conversation_id=conversation_id,
            )
        # Embed async in background — avoids blocking the event loop
        _t = asyncio.create_task(self.vector.store_message_async(
            message_id=message.id,
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            model=model,
            timestamp=message.timestamp,
        ))
        _t.add_done_callback(
            lambda t: t.exception() and logger.warning("vector embed failed: %s", t.exception())
        )

    def _build_hook_context(
        self,
        body: dict,
        conversation_id: str,
        model: str,
        decision: object | None = None,  # historical, always None now
    ) -> dict:
        """Build the context dict passed to hooks.

        The ``decision`` arg used to carry a routing-LLM Decision; that layer
        was deleted in v3. Hook callers may still pass ``None`` positionally,
        which is preserved for compatibility.
        """
        return {
            "conversation_id": conversation_id,
            "model": model,
            "user_message": self._get_latest_user_message(body),
            "decision": decision,
            "config": self.cfg,
            "vector_store": self.vector,
        }

    def _run_hooks_with_logging(
        self,
        stage: str,                     # "pre_request" | "post_response"
        body: dict,
        target: dict,
        context: dict,
        conversation_id: str,
    ) -> tuple[dict, float]:
        """Invoke the HookManager batch for *stage* and emit one wire event.

        Single source of truth so the (1) framework-crash path,
        (2) per-batch latency, and (3) wire-event emission stay in lockstep
        across all callsites. Returns ``(result, latency_ms)`` where
        ``result`` is the body for pre_request or the response dict for
        post_response.
        """
        _t = time.monotonic()
        if not self.hook_manager:
            return target, (time.monotonic() - _t) * 1000
        try:
            if stage == "pre_request":
                result, hook_meta = self.hook_manager.run_pre_request_with_meta(target, context)
            else:
                result, hook_meta = self.hook_manager.run_post_response_with_meta(body, target, context)
        except Exception as exc:
            logger.error("hook_manager.run_%s crashed: %s", stage, exc)
            result = target
            hook_meta = {
                "hook_names": [],
                "errors": [{"hook": "__framework_crash__", "phase": stage, "error": str(exc)[:200]}],
            }
        latency_ms = (time.monotonic() - _t) * 1000
        try:
            log_hook_execution(
                stage=stage,
                hook_names=hook_meta.get("hook_names", []),
                total_latency_ms=latency_ms,
                hook_errors=hook_meta.get("errors") or None,
                conversation_id=conversation_id,
            )
        except Exception:
            logger.debug("hook wire emit failed", exc_info=True)
        return result, latency_ms

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

        # Normalize missing/empty model to the configured default so the router
        # always has a non-empty model name to match against backend model lists.
        if not body.get("model"):
            default = (
                rt.get("default_model")
                or self.cfg.get("backend", {}).get("default_model", "")
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

    def _inject_model_options(self, body: dict) -> dict:
        """
        Inject per-model Ollama options from config and runtime_config.

        Priority (highest wins):
          1. runtime_config model_options  — set via UI, hot-reloaded
          2. config.yaml models.<name>.options  — static, requires restart

        runtime_config structure (flat num_gpu per model):
            runtime:
              model_options:
                qwen3:4b: 0      # CPU
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

    def _inject_system_context(self, body: dict) -> dict:
        """Inject system_context.md content into the request (hot-reloaded)."""
        try:
            from beigebox.system_context import inject_system_context
            return inject_system_context(body, self.cfg)
        except Exception as e:
            logger.debug("system_context injection skipped: %s", e)
            return body

    # ------------------------------------------------------------------
    # Shared request pipeline
    # ------------------------------------------------------------------

    async def _run_request_pipeline(
        self,
        body: dict,
        conversation_id: str,
        model: str,
        stages: dict,
    ) -> tuple[dict, bool, str]:
        """
        Shared pipeline for both streaming and non-streaming paths.

        Runs from synthetic-check through window-config application:
          guardrails → extraction-detection → key-stripping → summarization →
          operator pre-hook → system context → generation params → model options →
          window config

        Returns (body, is_synthetic, model). Caller is responsible for timing
        stages before this call. (The agentic decision layer — z-commands,
        hybrid routing, decision LLM, embedding classifier, routing rules —
        was deleted in v3.)
        """
        is_synthetic = self._is_synthetic(body)

        # Guardrail — input check (bypassed for synthetic requests)
        if not is_synthetic:
            _gr = self.guardrails.check_input(body.get("messages", []))
            if not _gr.allowed:
                logger.warning(
                    "Guardrail blocked input: rule=%s reason=%s", _gr.rule_name, _gr.reason
                )
                self.wire.log(
                    direction="internal",
                    role="guardrails",
                    content=_gr.reason,
                    model="guardrails",
                    conversation_id=conversation_id,
                    event_type="guardrail_block",
                    source="guardrails",
                    meta={"direction": "input", "rule": _gr.rule_name, "reason": _gr.reason},
                )
                raise ValueError(f"Request blocked by guardrails: {_gr.reason}")

        # Extraction-attack detection (OWASP LLM10) — observe-only by default.
        # Always emit HIGH/CRITICAL. During the per-session baseline window
        # (≤20 messages) also emit MEDIUM, so operators see the detector
        # working before its baseline stabilises (Grok 2026-04-26 review).
        # Past baseline, MEDIUM stays off the bus to keep noise down.
        if not is_synthetic and self.extraction_detector:
            try:
                _user_msg = self._get_latest_user_message(body) or ""
                _score = self.extraction_detector.check_request(
                    session_id=conversation_id or "",
                    user_id="",
                    prompt=_user_msg,
                    model=model,
                )
                _level = getattr(_score.risk_level, "value", str(_score.risk_level))
                _emit = _level in ("high", "critical")
                if not _emit and _level == "medium":
                    if not self.extraction_detector.is_baseline_established(conversation_id or ""):
                        _emit = True
                if _emit:
                    log_extraction_attempt(
                        session_id=conversation_id or "",
                        risk_level=_level,
                        confidence=float(_score.confidence),
                        triggers=list(_score.triggers),
                        reason=_score.reason,
                    )
            except Exception:
                logger.debug("extraction_detector check failed", exc_info=True)

        # The model already comes from body["model"] (caller resolved it).
        model = body.get("model", model)
        stages["routing"] = 0.0  # kept for backward-compat with stage timers

        # Strip internal metadata keys — must not reach backends
        body.pop("_bb_auth_key", None)
        body.pop("_beigebox_synthetic", None)
        body.pop("_bb_injection_flag", None)
        body.pop("_beigebox_direct", None)  # historical override; now a no-op

        # Auto-summarize if conversation exceeds token budget
        try:
            from beigebox.summarizer import maybe_summarize
            body["messages"] = await maybe_summarize(body.get("messages", []), self.cfg)
        except Exception as _e:
            logger.debug("auto_summarizer skipped: %s", _e)

        # Aggressive summarization — bullet-compress history on every request
        try:
            from beigebox.summarizer import aggressive_summarize
            body["messages"] = await aggressive_summarize(body.get("messages", []), self.cfg)
        except Exception as _e:
            logger.debug("aggressive_summarizer skipped: %s", _e)

        # Inject system context, generation params, per-model options, window config
        body = self._inject_system_context(body)
        body = self._inject_generation_params(body)
        body = self._inject_model_options(body)
        body, _force_reload = self._apply_window_config(body)
        if _force_reload:
            await self._evict_model(body.get("model", ""))

        return body, is_synthetic, model

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------

    async def forward_chat_completion(
        self, body: dict, client_ip: str = "unknown", user_agent: str = ""
    ) -> dict:
        """Forward a non-streaming chat completion request."""
        import time as _time
        _t0 = _time.monotonic()
        _stages: dict[str, float] = {}

        model = self._get_model(body)
        conversation_id = self._extract_conversation_id(body)

        # Build a CaptureContext at request entry. ``backend`` and
        # ``request_id`` are populated post-router (see below). The fanout
        # only fires when self.capture is wired; legacy tests with
        # capture=None fall through to the old _log_messages path.
        _capture_ctx = self._build_capture_context(
            conversation_id, model,
            user_id=body.get("_bb_user_id"),
        ) if self.capture is not None else None
        _req_captured = False
        _resp_captured = False

        # Drop consecutive duplicate messages from a buggy/replaying client
        body = self._dedupe_consecutive_messages(body)

        # Pre-request hooks
        context = self._build_hook_context(body, conversation_id, model, None)
        body, _stages["pre_hooks"] = self._run_hooks_with_logging(
            stage="pre_request",
            body=body,
            target=body,
            context=context,
            conversation_id=conversation_id,
        )

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

        # API Anomaly Detection — check for suspicious patterns
        _t_anom = _time.monotonic()
        if self.anomaly_detector:
            _req_bytes = len(str(body).encode("utf-8"))
            is_anom, triggered_rules = self.anomaly_detector.is_anomalous(client_ip, user_agent, _req_bytes)
            if is_anom:
                from beigebox.security.anomaly_detector import _compute_risk_score, _recommended_action
                _risk = _compute_risk_score(triggered_rules)
                mode = self._anomaly_cfg.get("detection_mode", "warn")
                _action = _recommended_action(_risk, mode)
                self.wire.log(
                    direction="internal",
                    role="system",
                    content=f"anomaly detected: {', '.join(triggered_rules)} (ip={client_ip}, risk={_risk:.2f}, action={_action})",
                    model=model,
                    conversation_id=conversation_id,
                    event_type="security_anomaly",
                    meta={
                        "rules_triggered": triggered_rules,
                        "client_ip": client_ip,
                        "risk_score": _risk,
                        "recommended_action": _action,
                        "detection_mode": mode,
                    },
                )
                if _action == "block":
                    return {
                        "choices": [{"message": {"role": "assistant", "content": "Request blocked due to suspicious activity."}}],
                        "model": "beigebox",
                    }
        _stages["anomaly_check"] = (_time.monotonic() - _t_anom) * 1000

        try:
            body, is_synthetic, model = await self._run_request_pipeline(
                body, conversation_id, model, _stages
            )
        except ValueError as _gr_err:
            # Guardrail block — surface as a normal assistant response
            return {
                "choices": [{"message": {"role": "assistant", "content": str(_gr_err)}}],
                "model": "beigebox",
            }

        # Log incoming user messages (skip synthetic).
        # Capture pipeline: defer the request envelope until after router
        # returns so it carries the NormalizedRequest. Legacy path runs here.
        if not is_synthetic and self.capture is None:
            await self._log_messages(conversation_id, body.get("messages", []), model)

        # The request-side wire event will be enriched with normalizer
        # transforms after the backend call (see below) — at this point we
        # haven't yet selected a backend, so the per-backend normalizer hasn't
        # run. We emit the bare event here for chronology; the post-call
        # event below carries the full picture.
        log_payload_event("proxy", payload=body, model=model,
                           backend=self.backend_url, conversation_id=conversation_id)

        # Inspector ring buffer — capture final outbound payload
        _insp_entry = None
        if not is_synthetic:
            self._inspector_counter += 1
            _insp_entry = {
                "idx": self._inspector_counter,
                "ts": datetime.now(timezone.utc).isoformat(),
                "model": body.get("model", model),
                "backend_url": self.backend_url if not self.backend_router else "(via router)",
                "conv_id": conversation_id,
                "messages": copy.deepcopy(body.get("messages", [])),
                "generation_params": {
                    k: body[k] for k in
                    ["temperature", "top_p", "top_k", "max_tokens", "num_ctx",
                     "repeat_penalty", "seed", "stop", "stream", "options"]
                    if k in body
                },
                "message_count": len(body.get("messages", [])),
                "total_chars": sum(len(str(m.get("content", ""))) for m in body.get("messages", [])),
                "latency_ms": None,
                "ttft_ms": None,
                "status": "pending",
            }
            self._request_inspector.append(_insp_entry)

        # Forward to backend — use router if available, otherwise direct
        # H3: response is only assigned inside the router branch; initialise to
        # None here so any future code that references it outside that branch
        # gets a clear NameError rather than a silent undefined-variable crash.
        response = None
        cost_usd = None
        backend_name = "direct"
        _t_backend = _time.monotonic()

        if self.backend_router:
            try:
                response = await self.backend_router.forward(body)
            except Exception as _exc:
                # Router itself raised (rare — most failures land in
                # response.ok=False below). Capture the failure and re-raise.
                if self.capture is not None and not is_synthetic:
                    if not _req_captured:
                        self.capture.capture_request(
                            self._build_captured_request(_capture_ctx, body, None)
                        )
                        _req_captured = True
                    if not _resp_captured:
                        ctx_after = attach_response_timing(_capture_ctx)
                        self.capture.capture_response(
                            CapturedResponse.from_partial(
                                ctx=ctx_after, outcome="upstream_error", error=_exc,
                            )
                        )
                        _resp_captured = True
                raise
            _stages["backend"] = (_time.monotonic() - _t_backend) * 1000
            if not response.ok:
                # Inspector — finalize error
                if _insp_entry is not None:
                    _insp_entry["latency_ms"] = round(_stages.get("backend", 0), 1)
                    _insp_entry["status"] = "error"
                # Capture the failed request + response when fanout is wired.
                # Soft failures (response.ok=False) don't raise — the proxy
                # returns a synthetic error reply — but we still want a row.
                if self.capture is not None and not is_synthetic:
                    _capture_ctx_err = CaptureContext(
                        conv_id=_capture_ctx.conv_id,
                        turn_id=_capture_ctx.turn_id,
                        model=_capture_ctx.model,
                        backend=response.backend_name or "(unknown)",
                        started_at=_capture_ctx.started_at,
                        run_id=_capture_ctx.run_id,
                        user_id=_capture_ctx.user_id,
                    )
                    if not _req_captured:
                        self.capture.capture_request(
                            self._build_captured_request(_capture_ctx_err, body, response)
                        )
                        _req_captured = True
                    ctx_after = attach_response_timing(_capture_ctx_err)
                    self.capture.capture_response(
                        CapturedResponse.from_partial(
                            ctx=ctx_after,
                            outcome="upstream_error",
                            error=RuntimeError(response.error or "backend error"),
                        )
                    )
                    _resp_captured = True
                # Return error as a chat response so clients handle it gracefully
                return {
                    "choices": [{"message": {"role": "assistant",
                                             "content": f"[BeigeBox] Backend error: {response.error}"}}],
                    "model": model,
                }
            data = response.data
            cost_usd = response.cost_usd
            backend_name = response.backend_name
            # Backend resolved → enrich the capture context with backend name
            # before building the request envelope.
            if _capture_ctx is not None:
                _capture_ctx = CaptureContext(
                    conv_id=_capture_ctx.conv_id,
                    turn_id=_capture_ctx.turn_id,
                    model=_capture_ctx.model,
                    backend=backend_name,
                    started_at=_capture_ctx.started_at,
                    run_id=_capture_ctx.run_id,
                    user_id=_capture_ctx.user_id,
                )
        else:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.backend_url}/v1/chat/completions",
                    json=body,
                )
                resp.raise_for_status()
                data = resp.json()
            _stages["backend"] = (_time.monotonic() - _t_backend) * 1000

        # Capture pipeline: request envelope (post-router so transforms/errors
        # from the NormalizedRequest are available). Best-effort: if the
        # direct httpx path was taken, NR is unavailable and the envelope is
        # built from the body alone.
        if self.capture is not None and not is_synthetic and not _req_captured:
            try:
                self.capture.capture_request(
                    self._build_captured_request(_capture_ctx, body, response)
                )
            except Exception as _exc:
                logger.warning("capture_request failed: %s", _exc)
            _req_captured = True

        # Inspector — finalize latency
        if _insp_entry is not None:
            _insp_entry["latency_ms"] = round(_stages.get("backend", 0), 1)
            _insp_entry["status"] = "complete"

        # WASM response transform path — historically driven by decision.wasm_module
        # from the routing LLM. With routing gone in v3 nothing currently sets a
        # per-request WASM module, so this is dormant. The runtime is still
        # initialised; a future branch can wire body["_beigebox_wasm_module"]
        # (or similar) to re-enable per-request transforms.
        wasm_mod = ""

        # Log assistant response (skip synthetic)
        # assistant_content is set inside the if-block; guard below prevents
        # NameError on the post-hook line when is_synthetic=True or choices=[].
        assistant_content = ""
        normalized = None
        if not is_synthetic:
            try:
                normalized = normalize_response(
                    data,
                    fix_bpe_artifacts=getattr(response, "fix_bpe_artifacts", False),
                )
            except Exception as _exc:
                # normalize_response is total by contract, but defend in depth:
                # any unexpected failure here gets a partial capture so we
                # don't lose the response row.
                logger.warning("normalize_response failed: %s", _exc)
                if self.capture is not None and not _resp_captured:
                    ctx_after = attach_response_timing(_capture_ctx)
                    self.capture.capture_response(
                        CapturedResponse.from_partial(
                            ctx=ctx_after, outcome="upstream_error", error=_exc,
                        )
                    )
                    _resp_captured = True
                raise

            if normalized.content or normalized.tool_calls:
                assistant_content = normalized.content
                response_latency = response.latency_ms if self.backend_router and response.ok else None

                if self.capture is not None and not _resp_captured:
                    # New capture pipeline: full canonical fields land in
                    # messages + wire_events + vector embedding via the fanout.
                    ctx_after = attach_response_timing(
                        _capture_ctx,
                        ttft_ms=None,  # non-streaming path has no separate TTFT
                    )
                    captured_resp = CapturedResponse.from_normalized(
                        normalized, ctx_after, "ok",
                    )
                    try:
                        self.capture.capture_response(captured_resp)
                    except Exception as _exc:
                        logger.warning("capture_response failed: %s", _exc)
                    _resp_captured = True
                else:
                    # Legacy path (no fanout wired) — keep existing behaviour.
                    await self._log_response(
                        conversation_id, assistant_content, model,
                        cost_usd=cost_usd, latency_ms=response_latency,
                    )

                # Side-channel payload log — separate subsystem, opt-in via
                # payload_log_enabled. Stays even when capture is wired.
                _resp_meta = normalized.summary({
                    "model": model,
                    "backend": backend_name,
                    "conversation_id": conversation_id,
                    "latency_ms": round(_stages.get("backend", 0), 1),
                })
                if response is not None and response.request_summary:
                    _resp_meta["request"] = response.request_summary
                log_payload_event("proxy_response", response=assistant_content, model=model,
                               backend=backend_name, conversation_id=conversation_id,
                        latency_ms=round(_stages.get("backend", 0), 1),
                        extra_meta=_resp_meta,
                    )

        # Response format validation (non-blocking, log-only)
        if not is_synthetic:
            _val = self.response_validator.validate_response(data, model=model)
            if not _val.valid:
                self.wire.log(
                    direction="internal",
                    role="validation",
                    content=f"format validation failed: fmt={_val.format} error={_val.error}",
                    model=model,
                    conversation_id=conversation_id,
                    event_type="validation_warn",
                    source="response_validator",
                    meta={"format": _val.format, "error": _val.error,
                          "schema_errors": _val.schema_errors},
                )

        # Post-response hooks
        if not is_synthetic:
            context = self._build_hook_context(body, conversation_id, model, None)
            data, _stages["post_hooks"] = self._run_hooks_with_logging(
                stage="post_response",
                body=body,
                target=data,
                context=context,
                conversation_id=conversation_id,
            )
        else:
            _stages["post_hooks"] = 0.0

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

        # Record request metrics for anomaly detection baseline updates
        if self.anomaly_detector:
            request_body_bytes = len(str(body).encode("utf-8"))
            # Extract API key if available (use placeholder if not)
            api_key = body.get("_bb_auth_key", "anonymous")
            # Determine status code (200 for success, 5xx for error)
            status_code = 200 if "error" not in data else 500
            self.anomaly_detector.record_request(
                ip=client_ip,
                user_agent=user_agent,
                api_key=api_key,
                model=model,
                request_bytes=request_body_bytes,
                status_code=status_code,
                latency_ms=total_ms,
                conversation_id=conversation_id,
            )

        return data

    async def forward_chat_completion_stream(
        self, body: dict, client_ip: str = "unknown", user_agent: str = ""
    ):
        """
        Forward a streaming chat completion request.
        Yields SSE chunks to the client while buffering the full response for logging.
        """
        import time as _time
        _t0 = _time.monotonic()
        _stages: dict[str, float] = {}

        model = self._get_model(body)
        conversation_id = self._extract_conversation_id(body)

        # Capture pipeline state for streaming. The streaming router doesn't
        # surface NormalizedRequest (forward_stream is an async generator
        # with no return value), so the request envelope is best-effort —
        # transforms/errors come back as empty lists. Response capture is
        # full-fidelity (synthesized via normalize_response on assembled
        # text). Error/disconnect outcomes captured in the try/except below.
        _capture_ctx = self._build_capture_context(
            conversation_id, model,
            user_id=body.get("_bb_user_id"),
        ) if self.capture is not None else None
        _stream_req_captured = False
        _stream_resp_captured = False
        _stream_outcome = "ok"
        _stream_error: BaseException | None = None

        # Drop consecutive duplicate messages from a buggy/replaying client
        body = self._dedupe_consecutive_messages(body)

        # Log request start
        prompt_tokens = _estimate_tokens(body.get("messages", []))
        try:
            log_request_started(model, prompt_tokens)
        except Exception:
            pass  # Don't block on logging

        # Pre-request hooks
        context = self._build_hook_context(body, conversation_id, model, None)
        body, _stages["pre_hooks"] = self._run_hooks_with_logging(
            stage="pre_request",
            body=body,
            target=body,
            context=context,
            conversation_id=conversation_id,
        )

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

        # API Anomaly Detection — check for suspicious patterns
        _t_anom = _time.monotonic()
        _anomaly_triggered = False
        if self.anomaly_detector:
            _req_bytes = len(str(body).encode("utf-8"))
            is_anom, triggered_rules = self.anomaly_detector.is_anomalous(client_ip, user_agent, _req_bytes)
            if is_anom:
                _anomaly_triggered = True
                from beigebox.security.anomaly_detector import _compute_risk_score, _recommended_action
                _risk = _compute_risk_score(triggered_rules)
                mode = self._anomaly_cfg.get("detection_mode", "warn")
                _action = _recommended_action(_risk, mode)
                self.wire.log(
                    direction="internal",
                    role="system",
                    content=f"anomaly detected: {', '.join(triggered_rules)} (ip={client_ip}, risk={_risk:.2f}, action={_action})",
                    model=model,
                    conversation_id=conversation_id,
                    event_type="security_anomaly",
                    meta={
                        "rules_triggered": triggered_rules,
                        "client_ip": client_ip,
                        "risk_score": _risk,
                        "recommended_action": _action,
                        "detection_mode": mode,
                    },
                )
                if _action == "block":
                    chunk = json.dumps({
                        "choices": [{"delta": {"content": "Request blocked due to suspicious activity."}, "index": 0}],
                        "model": "beigebox",
                    })
                    yield f"data: {chunk}\n"
                    yield "data: [DONE]\n"
                    return
        _stages["anomaly_check"] = (_time.monotonic() - _t_anom) * 1000

        try:
            body, is_synthetic, model = await self._run_request_pipeline(
                body, conversation_id, model, _stages
            )
        except ValueError as _gr_err:
            # Guardrail block — yield as a single streamed assistant chunk
            chunk = json.dumps({
                "choices": [{"delta": {"content": str(_gr_err)}, "index": 0}],
                "model": "beigebox",
            })
            yield f"data: {chunk}\n"
            yield "data: [DONE]\n"
            return

        # Emit a routing metadata chunk before the real stream starts. The
        # frontend reads bb_type=="routing" and updates the model badge in the
        # pane header immediately — before any tokens arrive. Not sent for
        # synthetic requests because synthetic clients don't have a UI.
        if not is_synthetic:
            yield f"data: {json.dumps({'bb_type': 'routing', 'model': model})}\n\n"

        user_message = self._get_latest_user_message(body)

        # Log incoming user messages (skip synthetic).
        # Capture pipeline: defer until after first chunk arrives so we
        # know upstream is alive. Legacy path (no capture) writes here.
        if not is_synthetic and self.capture is None:
            await self._log_messages(conversation_id, body.get("messages", []), model)

        if not is_synthetic:
            log_payload_event("proxy_stream", payload=body, model=model,
                              backend=self.backend_url, conversation_id=conversation_id)

        # Inspector ring buffer — capture final outbound payload
        _insp_entry = None
        if not is_synthetic:
            self._inspector_counter += 1
            _insp_entry = {
                "idx": self._inspector_counter,
                "ts": datetime.now(timezone.utc).isoformat(),
                "model": body.get("model", model),
                "backend_url": self.backend_url if not self.backend_router else "(via router)",
                "conv_id": conversation_id,
                "messages": copy.deepcopy(body.get("messages", [])),
                "generation_params": {
                    k: body[k] for k in
                    ["temperature", "top_p", "top_k", "max_tokens", "num_ctx",
                     "repeat_penalty", "seed", "stop", "stream", "options"]
                    if k in body
                },
                "message_count": len(body.get("messages", [])),
                "total_chars": sum(len(str(m.get("content", ""))) for m in body.get("messages", [])),
                "latency_ms": None,
                "ttft_ms": None,
                "status": "pending",
            }
            self._request_inspector.append(_insp_entry)

        # full_response accumulates token deltas so we can log and cache the
        # complete assistant message after streaming. It's also the WASM input
        # buffer when a module is active.
        full_response = []
        stream_cost_usd: float | None = None
        backend_name = "direct"
        _t_backend = _time.monotonic()
        _first_chunk = True  # TTFT sentinel — cleared after the first yielded chunk
        # WASM buffer mode: historically driven by decision.wasm_module from the
        # routing LLM. Routing was removed in v3, so nothing currently sets a
        # per-request WASM module — always pass-through. A future branch can wire
        # body["_beigebox_wasm_module"] (or similar) to re-enable per-request
        # transforms; the runtime itself is still alive.
        wasm_mod = ""
        _wasm_buffer_mode = False

        if self.backend_router:
            # Stream via multi-backend router. The try/except tracks the
            # outcome (ok / client_disconnect / stream_aborted) so the
            # post-loop capture path can emit the right CapturedResponse.
            try:
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
                        # First chunk arrived → upstream is alive. Capture
                        # the request envelope now (best-effort: NR not
                        # surfaced through forward_stream).
                        if self.capture is not None and not is_synthetic and not _stream_req_captured:
                            try:
                                self.capture.capture_request(
                                    self._build_captured_request(_capture_ctx, body, None)
                                )
                            except Exception as _exc:
                                logger.warning("stream capture_request failed: %s", _exc)
                            _stream_req_captured = True
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
                            nd = normalize_stream_delta(chunk)
                            if nd.content_delta:
                                full_response.append(nd.content_delta)
                        except (json.JSONDecodeError, IndexError):
                            pass
            except (asyncio.CancelledError, GeneratorExit):
                # Client closed the connection (or task was cancelled).
                # Capture whatever we assembled and re-raise so the runtime
                # can clean up. asyncio.CancelledError must propagate;
                # GeneratorExit must NOT be suppressed.
                _stream_outcome = "client_disconnect"
                if self.capture is not None and not is_synthetic and not _stream_resp_captured:
                    self._capture_stream_response(
                        _capture_ctx, body, full_response, _stages,
                        backend_name, stream_cost_usd, prompt_tokens,
                        outcome="client_disconnect", error=None,
                        req_already_captured=_stream_req_captured,
                    )
                    _stream_resp_captured = True
                raise
            except Exception as exc:
                _stream_outcome = "stream_aborted"
                _stream_error = exc
                if self.capture is not None and not is_synthetic and not _stream_resp_captured:
                    self._capture_stream_response(
                        _capture_ctx, body, full_response, _stages,
                        backend_name, stream_cost_usd, prompt_tokens,
                        outcome="stream_aborted", error=exc,
                        req_already_captured=_stream_req_captured,
                    )
                    _stream_resp_captured = True
                raise
        else:
            # Direct backend (legacy single-backend path)
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    async with client.stream(
                        "POST",
                        f"{self.backend_url}/v1/chat/completions",
                        json=body,
                    ) as resp:
                        if resp.status_code >= 400:
                            err_body = await resp.aread()
                            err_text = err_body.decode(errors="replace")[:300]
                            logger.error(
                                "Backend error %d for model '%s': %s",
                                resp.status_code, model, err_text,
                            )
                            err_chunk = json.dumps({
                                "choices": [{"delta": {"content": f"[Backend error {resp.status_code}: {err_text}]"}, "finish_reason": "stop", "index": 0}],
                                "model": model,
                            })
                            yield f"data: {err_chunk}\n"
                            yield "data: [DONE]\n\n"
                            return
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
                                    nd = normalize_stream_delta(chunk)
                                    if nd.content_delta:
                                        full_response.append(nd.content_delta)
                                except (json.JSONDecodeError, IndexError):
                                    pass
            except httpx.TimeoutException:
                logger.error("Backend timeout after %ss for model '%s'", self.timeout, model)
                err_chunk = json.dumps({
                    "choices": [{"delta": {"content": f"[Backend timed out after {self.timeout}s — model may be loading, try again]"}, "finish_reason": "stop", "index": 0}],
                    "model": model,
                })
                yield f"data: {err_chunk}\n"
                yield "data: [DONE]\n\n"
                if _insp_entry is not None:
                    _insp_entry["latency_ms"] = round((_time.monotonic() - _t_backend) * 1000, 1)
                    _insp_entry["status"] = "error"
                return
            except httpx.RequestError as e:
                logger.error("Backend connection error for model '%s': %s", model, e)
                err_chunk = json.dumps({
                    "choices": [{"delta": {"content": f"[Backend connection error: {e}]"}, "finish_reason": "stop", "index": 0}],
                    "model": model,
                })
                yield f"data: {err_chunk}\n"
                yield "data: [DONE]\n\n"
                if _insp_entry is not None:
                    _insp_entry["latency_ms"] = round((_time.monotonic() - _t_backend) * 1000, 1)
                    _insp_entry["status"] = "error"
                return

        # Wall-clock stream duration
        _stages["backend"] = (_time.monotonic() - _t_backend) * 1000
        total_ms = (_time.monotonic() - _t0) * 1000

        # Inspector — finalize latency
        if _insp_entry is not None:
            _insp_entry["latency_ms"] = round(total_ms, 1)
            _insp_entry["ttft_ms"] = round(_stages.get("ttft_ms", 0), 1)
            _insp_entry["status"] = "complete"

        # Log request completion
        try:
            completion_tokens = _estimate_tokens("".join(full_response))
            log_request_completed(
                model=model,
                latency_ms=total_ms,
                tokens_in=prompt_tokens,
                tokens_out=completion_tokens,
                cost=stream_cost_usd,
            )
        except Exception:
            pass  # Don't block on logging

        # Log the complete response after streaming finishes (skip synthetic).
        # Synthesize a one-choice response shape so we can reuse the same
        # normalize_response → .summary(...) path the non-streaming code uses;
        # the assembled text is the only field we have post-stream, but the
        # wire event still gets the consistent shape (kind, role, finish_reason
        # if available, content_length, has_reasoning, tool_calls_count, usage).
        if not is_synthetic:
            complete_text = "".join(full_response)
            if complete_text:
                _stream_meta = normalize_response({
                    "choices": [{
                        "message": {"role": "assistant", "content": complete_text},
                        # Streams that complete without an explicit finish_reason
                        # default to "stop" — matches OpenAI/OpenRouter semantics
                        # and keeps the summary consistent with non-streaming.
                        "finish_reason": "stop",
                    }],
                    "usage": {
                        "prompt_tokens": prompt_tokens or 0,
                        "completion_tokens": _estimate_tokens(complete_text),
                    },
                }).summary({
                    "model": model,
                    "backend": backend_name,
                    "conversation_id": conversation_id,
                    "latency_ms": round(_stages.get("backend", 0), 1),
                    "stream": True,
                    "cost_usd": stream_cost_usd,
                })
                log_payload_event("proxy_stream_response", response=complete_text, model=model,
                                  backend=backend_name, conversation_id=conversation_id,
                    latency_ms=round(_stages.get("backend", 0), 1),
                    extra_meta=_stream_meta,
                )
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
            # Guardrail — output check (after WASM transform, before logging/cache)
            if complete_text:
                _gr_out, complete_text = self.guardrails.check_output(complete_text)
                if not _gr_out.allowed:
                    logger.warning(
                        "Guardrail blocked output: rule=%s reason=%s",
                        _gr_out.rule_name, _gr_out.reason,
                    )
                    self.wire.log(
                        direction="internal",
                        role="guardrails",
                        content=_gr_out.reason,
                        model="guardrails",
                        conversation_id=conversation_id,
                        event_type="guardrail_block",
                        source="guardrails",
                        meta={"direction": "output", "rule": _gr_out.rule_name, "reason": _gr_out.reason},
                    )

            # Response format validation on assembled stream buffer (non-blocking)
            _val = self.response_validator.validate_stream_buffer(complete_text, model=model)
            if complete_text and not _val.valid:
                self.wire.log(
                    direction="internal",
                    role="validation",
                    content=f"stream format validation failed: fmt={_val.format} error={_val.error}",
                    model=model,
                    conversation_id=conversation_id,
                    event_type="validation_warn",
                    source="response_validator",
                    meta={"format": _val.format, "error": _val.error,
                          "schema_errors": _val.schema_errors},
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
                if self.capture is not None and not _stream_resp_captured:
                    # New capture pipeline: synthesize a NormalizedResponse
                    # from assembled text + token estimates so the row
                    # carries the v1.4 fields (tokens, finish_reason, etc.)
                    self._capture_stream_response(
                        _capture_ctx, body, full_response, _stages,
                        backend_name, stream_cost_usd, prompt_tokens,
                        outcome="ok", error=None,
                        req_already_captured=_stream_req_captured,
                    )
                    _stream_resp_captured = True
                else:
                    # Legacy path (no fanout wired) — keep existing behaviour.
                    await self._log_response(
                        conversation_id,
                        complete_text,
                        model,
                        cost_usd=stream_cost_usd,
                        latency_ms=round(_stages["backend"], 1),
                        ttft_ms=round(_stages["ttft_ms"], 1) if "ttft_ms" in _stages else None,
                    )

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

        # Record request metrics for anomaly detection baseline updates
        if self.anomaly_detector:
            request_body_bytes = len(str(body).encode("utf-8"))
            # Extract API key if available (use placeholder if not)
            api_key = body.get("_bb_auth_key", "anonymous")
            self.anomaly_detector.record_request(
                ip=client_ip,
                user_agent=user_agent,
                api_key=api_key,
                model=model,
                request_bytes=request_body_bytes,
                status_code=200,  # Stream completed successfully
                latency_ms=total_ms,
                conversation_id=conversation_id,
            )

    async def list_models(self) -> dict:
        """Forward /v1/models request to backend(s), optionally rewriting model names.

        Always fetches from the direct Ollama backend URL first so local models
        are visible regardless of whether multi-backend routing is enabled.
        Router backends (e.g. pinned OpenRouter models) are merged on top.
        """
        # `seen` deduplicates by model ID so a model available on both Ollama
        # and a router backend (e.g. an OR alias for a local model) only appears
        # once. Ollama wins because it's fetched first.
        seen: set[str] = set()
        all_models: list[dict] = []

        # Always include local Ollama models
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.backend_url}/v1/models")
                resp.raise_for_status()

                # Check if local model filtering is enabled (with runtime override support)
                from beigebox.config import get_runtime_config
                rt_cfg = get_runtime_config()
                local_cfg = self.cfg.get("local_models", {})
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
