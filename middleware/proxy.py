"""
Proxy: the core of the middleware.
Intercepts OpenAI-compatible requests, logs both sides, forwards to backend.
Handles streaming (SSE) transparently.
"""

import json
import logging
import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import httpx

from middleware.config import get_config
from middleware.storage.models import Message
from middleware.storage.sqlite_store import SQLiteStore
from middleware.storage.vector_store import VectorStore

logger = logging.getLogger(__name__)


class Proxy:
    """Transparent proxy between frontend and Ollama backend."""

    def __init__(self, sqlite: SQLiteStore, vector: VectorStore):
        self.sqlite = sqlite
        self.vector = vector
        self.cfg = get_config()
        self.backend_url = self.cfg["backend"]["url"].rstrip("/")
        self.timeout = self.cfg["backend"].get("timeout", 120)
        self.default_model = self.cfg["backend"].get("default_model", "")
        self.log_enabled = self.cfg["storage"].get("log_conversations", True)

    def _extract_conversation_id(self, body: dict) -> str:
        """
        Try to extract a conversation ID from the request.
        Open WebUI doesn't always send one, so we generate if missing.
        """
        # Some frontends include a conversation/session identifier
        # Check common locations
        conv_id = body.get("conversation_id") or body.get("session_id") or ""
        if not conv_id:
            # Use the hash of the system message + first user message as a stable ID
            # This groups messages in the same chat together
            messages = body.get("messages", [])
            if messages:
                # Simple approach: generate based on first message content
                first_content = messages[0].get("content", "")[:100]
                conv_id = uuid4().hex  # For now, just generate unique per request
        return conv_id

    def _get_model(self, body: dict) -> str:
        """Extract model from request, fall back to config default."""
        return body.get("model") or self.default_model

    def _log_messages(self, conversation_id: str, messages: list[dict], model: str):
        """Store the user messages from the request."""
        if not self.log_enabled:
            return

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if not content or role == "system":
                continue  # Skip system prompts and empty messages

            message = Message(
                conversation_id=conversation_id,
                role=role,
                content=content if isinstance(content, str) else json.dumps(content),
                model=model,
            )
            self.sqlite.store_message(message)

            # Embed in background (sync for now, async later)
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

        message = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            model=model,
        )
        self.sqlite.store_message(message)
        self.vector.store_message(
            message_id=message.id,
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            model=model,
            timestamp=message.timestamp,
        )

    async def forward_chat_completion(self, body: dict) -> dict:
        """Forward a non-streaming chat completion request."""
        model = self._get_model(body)
        conversation_id = self._extract_conversation_id(body)

        # Log incoming user messages
        self._log_messages(conversation_id, body.get("messages", []), model)

        # Forward to backend
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.backend_url}/v1/chat/completions",
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()

        # Log assistant response
        choices = data.get("choices", [])
        if choices:
            assistant_content = choices[0].get("message", {}).get("content", "")
            self._log_response(conversation_id, assistant_content, model)

        return data

    async def forward_chat_completion_stream(self, body: dict):
        """
        Forward a streaming chat completion request.
        Yields SSE chunks to the client while buffering the full response for logging.
        """
        model = self._get_model(body)
        conversation_id = self._extract_conversation_id(body)

        # Log incoming user messages
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

        # Log the complete response after streaming finishes
        complete_text = "".join(full_response)
        if complete_text:
            self._log_response(conversation_id, complete_text, model)

    async def list_models(self) -> dict:
        """Forward /v1/models request to backend."""
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{self.backend_url}/v1/models")
            resp.raise_for_status()
            return resp.json()
