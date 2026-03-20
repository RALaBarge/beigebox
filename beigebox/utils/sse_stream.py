"""
Server-Sent Events (SSE) stream parsing.

Consolidates SSE parsing logic used across backends and agents.
Handles streaming responses from OpenAI-compatible APIs.
"""

import json
import logging
from typing import AsyncGenerator, Optional

_log = logging.getLogger(__name__)


async def parse_sse_stream(response_iter) -> AsyncGenerator[dict, None]:
    """
    Parse Server-Sent Events stream from OpenAI-compatible API.

    Yields JSON objects from SSE stream until [DONE] marker or end of stream.

    Args:
        response_iter: Async iterator of bytes (e.g., from httpx streaming response)

    Yields:
        Parsed JSON dict from each SSE event (delta/usage/finish_reason)

    Example:
        async with httpx.stream("POST", url, ...) as response:
            async for chunk in parse_sse_stream(response.aiter_raw()):
                print(chunk)
    """
    async for line in response_iter:
        if isinstance(line, bytes):
            line = line.decode("utf-8")

        line = line.rstrip("\n\r")

        # Skip empty lines and comments
        if not line or line.startswith(":"):
            continue

        # Handle "data: " prefix
        if line.startswith("data: "):
            data = line[6:]  # Strip "data: " prefix
        else:
            continue

        # Check for [DONE] marker
        if data == "[DONE]":
            break

        # Parse JSON
        try:
            chunk = json.loads(data)
            yield chunk
        except json.JSONDecodeError:
            _log.debug("Failed to parse SSE chunk: %s", data)
            continue


async def parse_sse_stream_text(response_iter) -> AsyncGenerator[str, None]:
    """
    Parse SSE stream and yield only delta text content.

    Useful for extracting just the text tokens from a streaming response.

    Args:
        response_iter: Async iterator from SSE stream

    Yields:
        Text content from each delta chunk
    """
    async for chunk in parse_sse_stream(response_iter):
        if "choices" in chunk:
            for choice in chunk.get("choices", []):
                delta = choice.get("delta", {})
                if "content" in delta and delta["content"]:
                    yield delta["content"]


async def parse_sse_stream_until(
    response_iter,
    stop_condition: Optional[callable] = None,
) -> AsyncGenerator[dict, None]:
    """
    Parse SSE stream with custom stop condition.

    Args:
        response_iter: Async iterator from SSE stream
        stop_condition: Callable(chunk) -> bool; yield until returns True

    Yields:
        Parsed JSON chunks until stop condition or [DONE]
    """
    async for chunk in parse_sse_stream(response_iter):
        yield chunk

        if stop_condition and stop_condition(chunk):
            break
