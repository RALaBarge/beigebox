"""
MLX backend — local Apple Silicon inference via mlx-lm server.

mlx-lm exposes an OpenAI-compatible endpoint, so this is a thin wrapper
around OpenAICompatibleBackend with two differences:

1. Longer default timeout (300s) — MLX on M1/M2 is CPU-bound for large
   models; 24B at 6-bit does ~8 tok/s, so 1000-token responses take ~2min.

2. BPE artifact cleanup — mlx-lm's Mistral tokenizer has a broken regex
   (upstream issue) that emits GPT-2 byte-level BPE unicode surrogates
   (Ġ for space, Ċ for newline) instead of real text.  We rewrite each
   choice's content in place before the response leaves this backend so
   the client sees clean text and the normalizer also sees clean text.
   The fix_bpe_artifacts flag on BackendResponse is still set so the
   streaming path (finalize_stream) can apply the same cleanup on the
   assembled stream tail.
"""
from __future__ import annotations

from beigebox.backends.base import BackendResponse
from beigebox.backends.openai_compat import OpenAICompatibleBackend
from beigebox.response_normalizer import _decode_bpe_artifacts


def _clean_choices_inplace(data: dict) -> None:
    """Rewrite message.content / delta.content for every choice."""
    choices = data.get("choices")
    if not isinstance(choices, list):
        return
    for ch in choices:
        if not isinstance(ch, dict):
            continue
        for key in ("message", "delta"):
            obj = ch.get(key)
            if isinstance(obj, dict):
                content = obj.get("content")
                if isinstance(content, str) and content:
                    obj["content"] = _decode_bpe_artifacts(content)


class MlxBackend(OpenAICompatibleBackend):
    """OpenAI-compat backend tuned for mlx-lm with BPE artifact cleanup."""

    egress_profile = "openai_compat"

    def __init__(
        self,
        name: str,
        url: str,
        timeout: int = 300,
        priority: int = 2,
        api_key: str = "",
        timeout_ms: int | None = None,
    ):
        super().__init__(name, url, timeout, priority, api_key=api_key, timeout_ms=timeout_ms)

    async def forward(self, body: dict) -> BackendResponse:
        resp = await super().forward(body)
        if resp.ok and isinstance(resp.data, dict):
            _clean_choices_inplace(resp.data)
        resp.fix_bpe_artifacts = True
        return resp
