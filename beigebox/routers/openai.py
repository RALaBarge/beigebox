"""OpenAI-compatible + Ollama-compatible proxy endpoints.

Extracted from beigebox/main.py (B-2). Includes:
- /v1/chat/completions (the main proxy entry — handles streaming + non-stream)
- /v1/models, /v1/embeddings, /v1/completions
- OpenAI passthroughs: /v1/files/, /v1/fine_tuning/, /v1/assistants/
- Ollama-native passthroughs: /api/tags, /api/chat, /api/generate,
  /api/pull, /api/push, /api/delete, /api/copy, /api/show, /api/embed,
  /api/embeddings, /api/ps, /api/version

All passthroughs route through ``_wire_and_forward`` (in ``_shared.py``)
which logs to wiretap and streams the upstream response verbatim.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from beigebox.routers._shared import _wire_and_forward
from beigebox.state import get_state


router = APIRouter()


# ---------------------------------------------------------------------------
# Core OpenAI-compat endpoints
# ---------------------------------------------------------------------------

@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """Main proxy endpoint. Accepts OpenAI-format chat completion requests,
    intercepts for logging/embedding, forwards to backend.
    """
    body = await request.json()

    # Model ACL — check here where the body is already parsed
    model = body.get("model", "")
    _auth_key = getattr(request.state, "auth_key", None)
    _st = get_state()
    if _auth_key is not None and model and _st.auth_registry and not _st.auth_registry.check_model(_auth_key, model):
        return JSONResponse(
            {
                "error": {
                    "message": f"Model '{model}' not permitted for key '{_auth_key.name}'.",
                    "type": "invalid_request_error",
                    "code": "model_not_allowed",
                }
            },
            status_code=403,
        )

    # Inject the key name as a special body field so the routing rules engine
    # can match on BB_AUTH_KEY conditions. The proxy strips it before forwarding.
    if _auth_key:
        body["_bb_auth_key"] = _auth_key.name

    stream = body.get("stream", False)

    # Extract client info for anomaly detection
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "")

    if stream:
        return StreamingResponse(
            _st.proxy.forward_chat_completion_stream(body, client_ip=client_ip, user_agent=user_agent),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        data = await _st.proxy.forward_chat_completion(body, client_ip=client_ip, user_agent=user_agent)
        return JSONResponse(data)


@router.get("/v1/models")
async def list_models():
    """Forward model listing to backend."""
    data = await get_state().proxy.list_models()
    return JSONResponse(data)


@router.post("/v1/embeddings")
async def embeddings(request: Request):
    """Embeddings — forward to backend, logged."""
    return await _wire_and_forward(request, "embeddings")


@router.post("/v1/completions")
async def completions(request: Request):
    """Legacy completions — forward to backend."""
    return await _wire_and_forward(request, "completions")


# ---------------------------------------------------------------------------
# OpenAI passthroughs (files, fine-tuning, assistants)
# ---------------------------------------------------------------------------

@router.api_route("/v1/files/{path:path}", methods=["GET", "POST", "DELETE"])
async def files_passthrough(path: str, request: Request):
    return await _wire_and_forward(request, f"files/{path}")


@router.api_route("/v1/fine_tuning/{path:path}", methods=["GET", "POST", "DELETE"])
async def fine_tuning_passthrough(path: str, request: Request):
    return await _wire_and_forward(request, f"fine_tuning/{path}")


@router.api_route("/v1/assistants/{path:path}", methods=["GET", "POST", "DELETE", "PUT"])
async def assistants_passthrough(path: str, request: Request):
    return await _wire_and_forward(request, f"assistants/{path}")


# ---------------------------------------------------------------------------
# Ollama-native passthroughs (frontends that speak Ollama directly)
# ---------------------------------------------------------------------------

@router.api_route("/api/tags", methods=["GET"])
async def ollama_tags(request: Request):
    """Ollama model list — forward and log."""
    return await _wire_and_forward(request, "ollama/tags")


@router.api_route("/api/chat", methods=["POST"])
async def ollama_chat(request: Request):
    """Ollama native chat — forward and log."""
    return await _wire_and_forward(request, "ollama/chat")


@router.api_route("/api/generate", methods=["POST"])
async def ollama_generate(request: Request):
    """Ollama native generate — forward and log."""
    return await _wire_and_forward(request, "ollama/generate")


@router.api_route("/api/pull", methods=["POST"])
async def ollama_pull(request: Request):
    """Ollama model pull — forward and log."""
    return await _wire_and_forward(request, "ollama/pull")


@router.api_route("/api/push", methods=["POST"])
async def ollama_push(request: Request):
    return await _wire_and_forward(request, "ollama/push")


@router.api_route("/api/delete", methods=["DELETE", "POST"])
async def ollama_delete(request: Request):
    return await _wire_and_forward(request, "ollama/delete")


@router.api_route("/api/copy", methods=["POST"])
async def ollama_copy(request: Request):
    return await _wire_and_forward(request, "ollama/copy")


@router.api_route("/api/show", methods=["POST"])
async def ollama_show(request: Request):
    return await _wire_and_forward(request, "ollama/show")


@router.api_route("/api/embed", methods=["POST"])
async def ollama_embed(request: Request):
    """Ollama embed — forward and log."""
    return await _wire_and_forward(request, "ollama/embed")


@router.api_route("/api/embeddings", methods=["POST"])
async def ollama_embeddings(request: Request):
    return await _wire_and_forward(request, "ollama/embeddings")


@router.api_route("/api/ps", methods=["GET"])
async def ollama_ps(request: Request):
    return await _wire_and_forward(request, "ollama/ps")


@router.api_route("/api/version", methods=["GET"])
async def ollama_version(request: Request):
    return await _wire_and_forward(request, "ollama/version")
