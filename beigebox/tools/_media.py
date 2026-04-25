"""
Helpers for tools that emit MCP-native multimedia content (images today;
audio/video extensible). The output dict carries a sentinel key so the MCP
server can route it as a real `image` content block to vision-capable clients,
while text-only consumers (the operator, log lines) read `_text_fallback`.

Sentinel: __beigebox_mcp_content__ (namespaced to avoid collision).
"""
from __future__ import annotations

import base64
import io
import logging

logger = logging.getLogger(__name__)

# Namespaced sentinel — any tool result dict containing this key is treated
# as a structured MCP content envelope by mcp_server.py and operator.py.
MCP_CONTENT_KEY = "__beigebox_mcp_content__"
TEXT_FALLBACK_KEY = "_text_fallback"

# Soft cap aligned with common MCP client limits (Claude Desktop ~5 MB).
DEFAULT_MAX_IMAGE_BYTES = 5 * 1024 * 1024


def image_content(
    png_bytes: bytes,
    summary: str,
    mime: str = "image/png",
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
    downscale: bool = True,
) -> dict:
    """
    Build an MCP image-content envelope.

    If the image exceeds *max_bytes* and *downscale* is True, attempt a
    Pillow-based downscale. If Pillow is not installed, we pass the original
    bytes through and rely on the MCP client to enforce its own limit.
    """
    if not summary:
        summary = "Image captured"

    if len(png_bytes) > max_bytes and downscale and mime == "image/png":
        png_bytes, summary = _maybe_downscale_png(png_bytes, max_bytes, summary)
        if not png_bytes:
            # Pillow missing + over cap; refuse rather than emit an oversized image.
            return {
                MCP_CONTENT_KEY: [{"type": "text", "text": summary}],
                TEXT_FALLBACK_KEY: summary,
            }

    # Hard cap: even with downscale=False or non-PNG mime, never emit more
    # than max_bytes. (Same limit Claude Desktop and most MCP clients enforce.)
    if len(png_bytes) > max_bytes:
        msg = (
            f"image refused: {len(png_bytes)/1024:.0f} KB exceeds the "
            f"{max_bytes/1024:.0f} KB hard cap (downscale={downscale}, mime={mime})"
        )
        return {
            MCP_CONTENT_KEY: [{"type": "text", "text": msg}],
            TEXT_FALLBACK_KEY: msg,
        }

    try:
        b64 = base64.b64encode(png_bytes).decode("ascii")
    except Exception as exc:  # pragma: no cover — base64 of bytes is total
        logger.warning("image_content: base64 encode failed: %s", exc)
        return {
            MCP_CONTENT_KEY: [{"type": "text", "text": f"Error encoding image: {exc}"}],
            TEXT_FALLBACK_KEY: f"Error encoding image: {exc}",
        }

    return {
        MCP_CONTENT_KEY: [
            {"type": "image", "data": b64, "mimeType": mime},
            {"type": "text", "text": summary},
        ],
        TEXT_FALLBACK_KEY: summary,
    }


def _maybe_downscale_png(png_bytes: bytes, max_bytes: int, summary: str) -> tuple[bytes, str]:
    """Downscale a PNG until it fits under max_bytes, or hard-fail if Pillow missing.

    Returns a (bytes, summary) pair. On failure (Pillow absent and
    image > max_bytes) returns an empty-bytes tuple with a clear error
    summary so the caller can short-circuit instead of emitting an
    oversized image to the MCP client.
    """
    try:
        from PIL import Image  # type: ignore[import]
    except ImportError:
        logger.warning(
            "image_content: image is %d bytes (>%d) and Pillow not installed; refusing to emit",
            len(png_bytes), max_bytes,
        )
        return b"", (
            f"image refused: {len(png_bytes)/1024:.0f} KB exceeds the {max_bytes/1024:.0f} KB "
            f"cap and Pillow is not installed (pip install pillow) to downscale"
        )

    try:
        img = Image.open(io.BytesIO(png_bytes))
        orig_w, orig_h = img.size
        scale = 0.75
        for _ in range(6):  # cap iterations
            new_w = max(64, int(img.width * scale))
            new_h = max(64, int(img.height * scale))
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            data = buf.getvalue()
            if len(data) <= max_bytes:
                summary += f" (downscaled {orig_w}x{orig_h} -> {new_w}x{new_h} to fit cap)"
                return data, summary
        # Could not get under cap — return last attempt
        summary += f" (downscaled but still {len(data)/1024:.0f} KB)"
        return data, summary
    except Exception as exc:
        logger.warning("image_content: downscale failed (%s); passing original", exc)
        return png_bytes, summary + " (downscale failed)"


def is_mcp_content(result: object) -> bool:
    """True iff result is a dict carrying the MCP content envelope."""
    return isinstance(result, dict) and MCP_CONTENT_KEY in result


def text_fallback(result: dict) -> str:
    """Extract the text fallback from a content envelope."""
    return result.get(TEXT_FALLBACK_KEY) or "(image-only result)"


def extract_image_bytes(result: dict, mime_prefix: str = "image/") -> bytes | None:
    """Return raw bytes of the first image part, or None if absent/decode-fail."""
    parts = result.get(MCP_CONTENT_KEY) or []
    for part in parts:
        if isinstance(part, dict) and str(part.get("type")) == "image" and str(part.get("mimeType", "")).startswith(mime_prefix):
            try:
                return base64.b64decode(part.get("data", ""), validate=True)
            except Exception:
                return None
    return None
