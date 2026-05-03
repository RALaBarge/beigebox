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

import httpx
# get_config / get_runtime_config: imported here so test fixtures can
# ``patch("beigebox.proxy.core.get_config")`` and reach the binding the
# Proxy class actually uses.
from beigebox.config import get_config, get_runtime_config  # noqa: F401
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
from beigebox.backends.router import evict_ollama_model
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
    CapturedResponse,
    attach_response_timing,
    build_capture_context,
    build_captured_request,
    capture_stream_response,
)
from beigebox.validation.format import ResponseValidator
from beigebox.security.anomaly_detector import APIAnomalyDetector
from beigebox.proxy.body_pipeline import (
    apply_window_config,
    inject_generation_params,
    inject_model_options,
)
from beigebox.proxy.model_listing import list_models as _list_models
from beigebox.proxy.request_helpers import (
    dedupe_consecutive_messages,
    extract_conversation_id,
    get_latest_user_message,
    get_model,
    is_synthetic,
)
from beigebox.proxy.request_inspector import RequestInspector

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
        # The proxy emits CapturedRequest / CapturedResponse envelopes here
        # for every chat completion. Tests must wire a real CaptureFanout
        # before invoking forward methods.
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
        self._inspector = RequestInspector(maxlen=5)
        # Back-compat: expose the underlying deque so existing routers
        # (analytics.py) keep reading proxy._request_inspector unchanged.
        self._request_inspector = self._inspector._buf
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
    # Request helpers — thin instance wrappers around proxy/request_helpers.py
    # ------------------------------------------------------------------

    def _extract_conversation_id(self, body: dict) -> str:
        return extract_conversation_id(body)

    def _get_model(self, body: dict) -> str:
        return get_model(body, self.alias_resolver, self.default_model)

    def _get_latest_user_message(self, body: dict) -> str:
        return get_latest_user_message(body)

    def _is_synthetic(self, body: dict) -> bool:
        return is_synthetic(body)

    def _dedupe_consecutive_messages(self, body: dict) -> dict:
        return dedupe_consecutive_messages(body)

    # ------------------------------------------------------------------
    # Capture pipeline helpers — thin instance wrappers around capture.py
    # ------------------------------------------------------------------
    # The envelope builders themselves live in beigebox.capture; the proxy
    # just supplies its own state where needed.

    def _build_capture_context(
        self,
        conversation_id: str,
        model: str,
        backend: str = "",
        run_id: str | None = None,
        user_id: str | None = None,
    ) -> CaptureContext:
        return build_capture_context(
            conversation_id, model,
            backend=backend, run_id=run_id, user_id=user_id,
        )

    def _build_captured_request(self, ctx, body, response):
        return build_captured_request(ctx, body, response)

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
        capture_stream_response(
            self.capture,
            ctx, body, full_response, stages,
            backend_name, cost_usd, prompt_tokens,
            outcome=outcome, error=error,
            req_already_captured=req_already_captured,
        )

    def _build_hook_context(
        self,
        body: dict,
        conversation_id: str,
        model: str,
    ) -> dict:
        """Build the context dict passed to hooks.

        The ``decision`` key is preserved (always None) so any external hook
        relying on its presence keeps reading None rather than KeyError; the
        agentic decision layer was deleted in v3.
        """
        return {
            "conversation_id": conversation_id,
            "model": model,
            "user_message": self._get_latest_user_message(body),
            "decision": None,
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

    # Body pipeline — thin instance wrappers around proxy/body_pipeline.py
    def _inject_generation_params(self, body: dict) -> dict:
        return inject_generation_params(body, self.cfg)

    def _inject_model_options(self, body: dict) -> dict:
        return inject_model_options(body, self.cfg)

    def _apply_window_config(self, body: dict) -> tuple[dict, bool]:
        return apply_window_config(body)

    async def _evict_model(self, model: str) -> None:
        await evict_ollama_model(self.backend_url, model)

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

        # Strip internal metadata keys — must not reach backends
        body.pop("_bb_auth_key", None)
        body.pop("_beigebox_synthetic", None)
        body.pop("_bb_injection_flag", None)

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
        # (inlined from former _inject_system_context wrapper — silent on failure)
        try:
            from beigebox.system_context import inject_system_context
            body = inject_system_context(body, self.cfg)
        except Exception as e:
            logger.debug("system_context injection skipped: %s", e)
        body = self._inject_generation_params(body)
        body = self._inject_model_options(body)
        body, _force_reload = self._apply_window_config(body)
        if _force_reload:
            await self._evict_model(body.get("model", ""))

        return body, is_synthetic, model

    # ------------------------------------------------------------------
    # Phase helpers (G-3) — shared between streaming and non-streaming
    # ------------------------------------------------------------------

    def _check_hook_block(self, body: dict, conversation_id: str) -> dict | None:
        """Return the block payload if pre-request hooks set ``_beigebox_block``.

        Hooks (e.g. the prompt-injection guard) can short-circuit a request
        by writing ``body['_beigebox_block']``. This helper just inspects
        the body and emits the wire event; the orchestrator decides what
        synthetic response to yield.
        """
        if "_beigebox_block" not in body:
            return None
        block = body["_beigebox_block"]
        self.wire.log(
            direction="internal",
            role="system",
            content=(
                f"request blocked: reason={block.get('reason')} "
                f"score={block.get('score')} patterns={block.get('patterns')}"
            ),
            model="prompt-injection-guard",
            conversation_id=conversation_id,
        )
        return block

    def _check_and_record_anomaly(
        self,
        body: dict,
        conversation_id: str,
        model: str,
        client_ip: str,
        user_agent: str,
        *,
        post_call_status_code: int | None = None,
        post_call_latency_ms: float | None = None,
    ) -> tuple[bool, dict | None]:
        """Single anomaly entry point for both pre- and post-call phases.

        Pre-call (``post_call_status_code is None``): runs ``is_anomalous``,
        emits a ``security_anomaly`` wire event when triggered, and returns
        ``(block_now, block_response)``. ``block_now=True`` means the
        orchestrator should short-circuit with the returned synthetic body.

        Post-call (``post_call_status_code is not None``): records baseline
        metrics on the detector. Always returns ``(False, None)`` — there's
        nothing to block after the upstream call has already completed.
        """
        if self.anomaly_detector is None:
            return False, None

        # Post-call: baseline-update path.
        if post_call_status_code is not None:
            request_body_bytes = len(str(body).encode("utf-8"))
            api_key = body.get("_bb_auth_key", "anonymous")
            self.anomaly_detector.record_request(
                ip=client_ip,
                user_agent=user_agent,
                api_key=api_key,
                model=model,
                request_bytes=request_body_bytes,
                status_code=post_call_status_code,
                latency_ms=post_call_latency_ms or 0.0,
                conversation_id=conversation_id,
            )
            return False, None

        # Pre-call: inspect, emit, optionally block.
        req_bytes = len(str(body).encode("utf-8"))
        is_anom, triggered_rules = self.anomaly_detector.is_anomalous(
            client_ip, user_agent, req_bytes,
        )
        if not is_anom:
            return False, None
        from beigebox.security.anomaly_detector import (
            _compute_risk_score, _recommended_action,
        )
        risk = _compute_risk_score(triggered_rules)
        mode = self._anomaly_cfg.get("detection_mode", "warn")
        action = _recommended_action(risk, mode)
        self.wire.log(
            direction="internal",
            role="system",
            content=(
                f"anomaly detected: {', '.join(triggered_rules)} "
                f"(ip={client_ip}, risk={risk:.2f}, action={action})"
            ),
            model=model,
            conversation_id=conversation_id,
            event_type="security_anomaly",
            meta={
                "rules_triggered": triggered_rules,
                "client_ip": client_ip,
                "risk_score": risk,
                "recommended_action": action,
                "detection_mode": mode,
            },
        )
        if action == "block":
            return True, {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "Request blocked due to suspicious activity.",
                    },
                }],
                "model": "beigebox",
            }
        return False, None

    def _emit_timing_summary(
        self,
        conversation_id: str,
        model: str,
        backend_name: str,
        total_ms: float,
        stages: dict,
        cost_usd: float | None,
        *,
        ttft_ms: float | None = None,
        stream: bool = False,
    ) -> None:
        """Emit the per-request wire-log timing summary.

        Used by both forward methods. The ``stream`` flag flips the prefix
        ("completed" vs "stream completed") and drops a TTFT suffix into
        the message when one is available.
        """
        cost_str = f" · ${cost_usd:.6f}" if cost_usd else ""
        ttft_str = f" · TTFT {ttft_ms:.0f}ms" if (stream and ttft_ms is not None) else ""
        prefix = "stream completed" if stream else "completed"
        self.wire.log(
            direction="internal",
            role="system",
            content=f"{prefix} via '{backend_name}' · {total_ms:.0f}ms total{cost_str}{ttft_str}",
            model=model,
            conversation_id=conversation_id,
            latency_ms=total_ms,
            timing=stages,
        )

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
        # ``request_id`` are populated post-router (see below). Tests that
        # don't wire a CaptureFanout get None and skip capture entirely.
        _capture_ctx = self._build_capture_context(
            conversation_id, model,
            user_id=body.get("_bb_user_id"),
        ) if self.capture is not None else None
        _req_captured = False
        _resp_captured = False

        # Drop consecutive duplicate messages from a buggy/replaying client
        body = self._dedupe_consecutive_messages(body)

        # Pre-request hooks
        context = self._build_hook_context(body, conversation_id, model)
        body, _stages["pre_hooks"] = self._run_hooks_with_logging(
            stage="pre_request",
            body=body,
            target=body,
            context=context,
            conversation_id=conversation_id,
        )

        # Check for hook-initiated block (e.g. prompt injection detection)
        block = self._check_hook_block(body, conversation_id)
        if block is not None:
            return {
                "choices": [{"message": {"role": "assistant", "content": block.get("message", "Request blocked.")}}],
                "model": "beigebox",
            }

        # API Anomaly Detection (pre-call) — check for suspicious patterns
        _t_anom = _time.monotonic()
        block_now, block_response = self._check_and_record_anomaly(
            body, conversation_id, model, client_ip, user_agent,
        )
        _stages["anomaly_check"] = (_time.monotonic() - _t_anom) * 1000
        if block_now:
            return block_response

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

        # Capture pipeline: request envelope is emitted post-router so it
        # carries the NormalizedRequest (transforms/errors/target).

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
            _insp_entry = self._inspector.start(
                body=body,
                model=model,
                conversation_id=conversation_id,
                backend_label=self.backend_url if not self.backend_router else "(via router)",
            )

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
                RequestInspector.finish(
                    _insp_entry, latency_ms=_stages.get("backend", 0), status="error",
                )
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
        RequestInspector.finish(
            _insp_entry, latency_ms=_stages.get("backend", 0), status="complete",
        )

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

                if self.capture is not None and not _resp_captured:
                    # Capture pipeline: full canonical fields land in
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
            context = self._build_hook_context(body, conversation_id, model)
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
        self._emit_timing_summary(
            conversation_id, model, backend_name, total_ms, _stages, cost_usd,
        )

        # Record request metrics for anomaly detection baseline updates
        status_code = 200 if "error" not in data else 500
        self._check_and_record_anomaly(
            body, conversation_id, model, client_ip, user_agent,
            post_call_status_code=status_code,
            post_call_latency_ms=total_ms,
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

        # Drop consecutive duplicate messages from a buggy/replaying client
        body = self._dedupe_consecutive_messages(body)

        # Log request start
        prompt_tokens = _estimate_tokens(body.get("messages", []))
        try:
            log_request_started(model, prompt_tokens)
        except Exception:
            pass  # Don't block on logging

        # Pre-request hooks
        context = self._build_hook_context(body, conversation_id, model)
        body, _stages["pre_hooks"] = self._run_hooks_with_logging(
            stage="pre_request",
            body=body,
            target=body,
            context=context,
            conversation_id=conversation_id,
        )

        # Check for hook-initiated block (e.g. prompt injection detection)
        block = self._check_hook_block(body, conversation_id)
        if block is not None:
            chunk = json.dumps({
                "choices": [{"delta": {"content": block.get("message", "Request blocked.")}, "index": 0}],
                "model": "beigebox",
            })
            yield f"data: {chunk}\n"
            yield "data: [DONE]\n"
            return

        # API Anomaly Detection (pre-call) — check for suspicious patterns
        _t_anom = _time.monotonic()
        block_now, block_response = self._check_and_record_anomaly(
            body, conversation_id, model, client_ip, user_agent,
        )
        _stages["anomaly_check"] = (_time.monotonic() - _t_anom) * 1000
        if block_now:
            # Translate the helper's non-streaming block dict into an SSE chunk.
            block_text = block_response["choices"][0]["message"]["content"]
            chunk = json.dumps({
                "choices": [{"delta": {"content": block_text}, "index": 0}],
                "model": "beigebox",
            })
            yield f"data: {chunk}\n"
            yield "data: [DONE]\n"
            return

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

        # Capture pipeline: request envelope deferred until first chunk
        # arrives (so we know upstream is alive).

        if not is_synthetic:
            log_payload_event("proxy_stream", payload=body, model=model,
                              backend=self.backend_url, conversation_id=conversation_id)

        # Inspector ring buffer — capture final outbound payload
        _insp_entry = None
        if not is_synthetic:
            _insp_entry = self._inspector.start(
                body=body,
                model=model,
                conversation_id=conversation_id,
                backend_label=self.backend_url if not self.backend_router else "(via router)",
            )

        # full_response accumulates token deltas so we can log the complete
        # assistant message after streaming completes.
        full_response = []
        stream_cost_usd: float | None = None
        backend_name = "direct"
        _t_backend = _time.monotonic()
        _first_chunk = True  # TTFT sentinel — cleared after the first yielded chunk

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
                RequestInspector.finish(
                    _insp_entry,
                    latency_ms=(_time.monotonic() - _t_backend) * 1000,
                    status="error",
                )
                return
            except httpx.RequestError as e:
                logger.error("Backend connection error for model '%s': %s", model, e)
                err_chunk = json.dumps({
                    "choices": [{"delta": {"content": f"[Backend connection error: {e}]"}, "finish_reason": "stop", "index": 0}],
                    "model": model,
                })
                yield f"data: {err_chunk}\n"
                yield "data: [DONE]\n\n"
                RequestInspector.finish(
                    _insp_entry,
                    latency_ms=(_time.monotonic() - _t_backend) * 1000,
                    status="error",
                )
                return

        # Wall-clock stream duration
        _stages["backend"] = (_time.monotonic() - _t_backend) * 1000
        total_ms = (_time.monotonic() - _t0) * 1000

        # Inspector — finalize latency
        RequestInspector.finish(
            _insp_entry,
            latency_ms=total_ms,
            ttft_ms=_stages.get("ttft_ms", 0),
            status="complete",
        )

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
            # Guardrail — output check (before logging/cache)
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

            if complete_text and self.capture is not None and not _stream_resp_captured:
                # Capture pipeline: synthesize a NormalizedResponse from the
                # assembled text + token estimates so the row carries the
                # v1.4 fields (tokens, finish_reason, etc.).
                self._capture_stream_response(
                    _capture_ctx, body, full_response, _stages,
                    backend_name, stream_cost_usd, prompt_tokens,
                    outcome="ok", error=None,
                    req_already_captured=_stream_req_captured,
                )
                _stream_resp_captured = True

        # Emit timing summary to wiretap
        self._emit_timing_summary(
            conversation_id, model, backend_name, total_ms, _stages, stream_cost_usd,
            ttft_ms=_stages.get("ttft_ms"),
            stream=True,
        )

        # Record request metrics for anomaly detection baseline updates
        self._check_and_record_anomaly(
            body, conversation_id, model, client_ip, user_agent,
            post_call_status_code=200,  # Stream completed successfully
            post_call_latency_ms=total_ms,
        )

    # Model listing — thin wrappers around proxy/model_listing.py
    async def list_models(self) -> dict:
        """Forward ``/v1/models`` to backend(s), rewriting names if configured."""
        return await _list_models(self.cfg, self.backend_url, self.backend_router)

    def _transform_model_names(self, data: dict) -> dict:
        """Rewrite model names per ``cfg['model_advertising']`` (kept for tests)."""
        from beigebox.proxy.model_listing import transform_model_names
        return transform_model_names(data, self.cfg)
