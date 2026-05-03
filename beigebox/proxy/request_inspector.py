"""Ring buffer of recent outbound payloads, used by the dev inspector UI.

``Proxy`` keeps an instance and calls ``start`` at the point the final
outbound body is known and ``finish`` once the upstream call has
returned (or errored). Each entry carries enough state to reconstruct
what the proxy sent and how long the upstream took — handy for debugging
without wading through the wiretap.

Lifted out of ``Proxy`` during the G-series refactor: the deque + counter
+ entry-construction logic was duplicated between the streaming and
non-streaming forward methods.
"""
from __future__ import annotations

import copy
from collections import deque
from datetime import datetime, timezone


class RequestInspector:
    """Bounded ring buffer of the last N outbound payloads.

    Thread-safety: single asyncio event loop, no locking needed.
    """

    def __init__(self, maxlen: int = 5) -> None:
        self._buf: deque = deque(maxlen=maxlen)
        self._counter: int = 0

    def __len__(self) -> int:
        return len(self._buf)

    def __iter__(self):
        return iter(self._buf)

    def snapshot(self) -> list[dict]:
        """Return a shallow copy of the buffered entries (newest last)."""
        return list(self._buf)

    def start(
        self,
        *,
        body: dict,
        model: str,
        conversation_id: str,
        backend_label: str,
    ) -> dict:
        """Record an outbound request. Returns the entry dict so the caller
        can later mutate ``latency_ms`` / ``ttft_ms`` / ``status``.
        """
        self._counter += 1
        entry: dict = {
            "idx": self._counter,
            "ts": datetime.now(timezone.utc).isoformat(),
            "model": body.get("model", model),
            "backend_url": backend_label,
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
        self._buf.append(entry)
        return entry

    @staticmethod
    def finish(
        entry: dict | None,
        *,
        latency_ms: float,
        status: str = "complete",
        ttft_ms: float | None = None,
    ) -> None:
        """Mutate the entry dict (returned from ``start``) to record outcome.

        Static so callers can pass ``None`` (for synthetic-request paths
        that didn't ``start``) without an attribute error.
        """
        if entry is None:
            return
        entry["latency_ms"] = round(latency_ms, 1)
        entry["status"] = status
        if ttft_ms is not None:
            entry["ttft_ms"] = round(ttft_ms, 1)
