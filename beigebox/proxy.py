"""
Proxy: the core of BeigeBox.
Intercepts OpenAI-compatible requests, logs both sides, forwards to backend.
Handles streaming (SSE) transparently.

Now with:
  - Decision LLM routing (pick the right model per request)
  - Pre/post hooks (extensible processing pipeline)
  - Synthetic request filtering (skip Open WebUI's internal requests)
  - Token tracking (approximate token counts for stats)
"""

import json
import logging
import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import httpx

from beigebox.config import get_config
from beigebox.storage.models import Message
from beigebox.storage.sqlite_store import SQLiteStore
from beigebox.storage.vector_store import VectorStore
from beigebox.agents.decision import DecisionAgent, Decision
from beigebox.agents.zcommand import parse_z_command, ZCommand, HELP_TEXT
from beigebox.agents.embedding_classifier import EmbeddingClassifier, EmbeddingDecision
from beigebox.hooks import HookManager
from beigebox.wiretap import WireLog

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
    ):
        self.sqlite = sqlite
        self.vector = vector
        self.decision_agent = decision_agent
        self.hook_manager = hook_manager
        self.embedding_classifier = embedding_classifier
        self.tool_registry = tool_registry
        self.cfg = get_config()
        self.backend_url = self.cfg["backend"]["url"].rstrip("/")
        self.timeout = self.cfg["backend"].get("timeout", 120)
        self.default_model = self.cfg["backend"].get("default_model", "")
        self.log_enabled = self.cfg["storage"].get("log_conversations", True)

        # Route config for z-command model resolution
        d_cfg = self.cfg.get("decision_llm", {})
        self.routes = d_cfg.get("routes", {})

        # Wire log — structured tap of everything on the line
        wire_path = self.cfg.get("wiretap", {}).get("path", "./data/wire.jsonl")
        self.wire = WireLog(wire_path)

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

            # Embed in background
            self.vector.store_message(
                message_id=message.id,
                conversation_id=conversation_id,
                role=role,
                content=message.content,
                model=model,
                timestamp=message.timestamp,
            )

    def _log_response(self, conversation_id: str, content: str, model: str):
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
        self.sqlite.store_message(message)

        # Wire tap
        self.wire.log(
            direction="outbound",
            role="assistant",
            content=content,
            model=model,
            conversation_id=conversation_id,
            token_count=tokens,
        )

        self.vector.store_message(
            message_id=message.id,
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            model=model,
            timestamp=message.timestamp,
        )

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

        decision = await self.decision_agent.decide(user_msg)

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

    async def _hybrid_route(self, body: dict, zcmd: ZCommand) -> tuple[dict, Decision | None]:
        """
        Hybrid routing pipeline:
          1. z-command (user override) — highest priority, skips everything
          2. Embedding classifier (fast path) — ~50ms, handles clear cases
          3. Decision LLM (slow path) — only for borderline cases

        Returns (modified body, decision or None).
        """
        # 1. Z-command takes absolute priority
        if zcmd.active and (zcmd.route or zcmd.model):
            body = self._apply_z_command(body, zcmd)
            return body, None

        # 2. Try embedding classifier first (fast path)
        if self.embedding_classifier and self.embedding_classifier.ready:
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
        return body, decision

    async def _apply_decision(self, body: dict, decision: Decision) -> dict:
        """Apply the decision LLM's routing to the request."""
        if decision.fallback:
            return body

        # Route to the decided model
        if decision.model:
            body["model"] = decision.model

        # Run requested tools and inject results
        if decision.tools and self.hook_manager:
            # Tools are handled through the hook/tool pipeline
            pass

        # Web search augmentation
        if decision.needs_search:
            # TODO: invoke web search tool, inject results into context
            logger.debug("Decision requested web search (not yet wired)")

        return body

    async def forward_chat_completion(self, body: dict) -> dict:
        """Forward a non-streaming chat completion request."""
        model = self._get_model(body)
        conversation_id = self._extract_conversation_id(body)

        # Z-command parsing
        zcmd, body = self._process_z_command(body)

        # Handle z: help — return help text directly without calling LLM
        if zcmd.is_help:
            return {
                "choices": [{"message": {"role": "assistant", "content": HELP_TEXT}}],
                "model": "beigebox",
            }

        # Pre-request hooks
        if self.hook_manager:
            context = self._build_hook_context(body, conversation_id, model, None)
            body = self.hook_manager.run_pre_request(body, context)

        # Check if synthetic (tagged by hook)
        is_synthetic = self._is_synthetic(body)

        # Run forced tools from z-command
        if zcmd.active and zcmd.tools:
            tool_results = self._run_forced_tools(zcmd, self._get_latest_user_message(body))
            if tool_results:
                body = self._inject_tool_context(body, tool_results)

        # Hybrid routing: z-command → embedding classifier → decision LLM
        body, decision = await self._hybrid_route(body, zcmd)
        model = body.get("model", model)  # Update model after routing

        # Log incoming user messages (skip synthetic)
        if not is_synthetic:
            self._log_messages(conversation_id, body.get("messages", []), model)

        # Forward to backend
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.backend_url}/v1/chat/completions",
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()

        # Log assistant response (skip synthetic)
        if not is_synthetic:
            choices = data.get("choices", [])
            if choices:
                assistant_content = choices[0].get("message", {}).get("content", "")
                self._log_response(conversation_id, assistant_content, model)

        # Post-response hooks
        if self.hook_manager and not is_synthetic:
            context = self._build_hook_context(body, conversation_id, model, decision)
            data = self.hook_manager.run_post_response(body, data, context)

        return data

    async def forward_chat_completion_stream(self, body: dict):
        """
        Forward a streaming chat completion request.
        Yields SSE chunks to the client while buffering the full response for logging.
        """
        model = self._get_model(body)
        conversation_id = self._extract_conversation_id(body)

        # Z-command parsing
        zcmd, body = self._process_z_command(body)

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
        if self.hook_manager:
            context = self._build_hook_context(body, conversation_id, model, None)
            body = self.hook_manager.run_pre_request(body, context)

        # Check if synthetic
        is_synthetic = self._is_synthetic(body)

        # Run forced tools from z-command
        if zcmd.active and zcmd.tools:
            tool_results = self._run_forced_tools(zcmd, self._get_latest_user_message(body))
            if tool_results:
                body = self._inject_tool_context(body, tool_results)

        # Hybrid routing
        body, decision = await self._hybrid_route(body, zcmd)
        model = body.get("model", model)

        # Log incoming user messages (skip synthetic)
        if not is_synthetic:
            self._log_messages(conversation_id, body.get("messages", []), model)

        # Buffer for the full response
        full_response = []

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

                    # Yield raw SSE line to client
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

        # Log the complete response after streaming finishes (skip synthetic)
        if not is_synthetic:
            complete_text = "".join(full_response)
            if complete_text:
                self._log_response(conversation_id, complete_text, model)

    async def list_models(self) -> dict:
        """Forward /v1/models request to backend."""
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{self.backend_url}/v1/models")
            resp.raise_for_status()
            return resp.json()
