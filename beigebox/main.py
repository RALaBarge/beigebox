"""
BeigeBox — LLM Middleware Control Plane

LICENSING: Dual-licensed under AGPL-3.0 (free) and Commercial License (proprietary).
See LICENSE.md and COMMERCIAL_LICENSE.md for details.

FastAPI application — the BeigeBox entry point. Wires app construction,
middleware, lifespan, and router registration. Endpoint handlers live in
beigebox/routers/; storage, security, and proxy initialization runs through
the lifespan() startup path which delegates to ``beigebox.bootstrap``.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from beigebox import __version__ as _BB_VERSION
from beigebox.config import get_config


logger = logging.getLogger(__name__)

# Application state singleton lives in beigebox/state.py so router modules
# can reach it without an import cycle through main.py. Re-exported here so
# `from beigebox.main import get_state` keeps working.
from beigebox.state import get_state, maybe_state, set_state  # noqa: F401, E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle — thin orchestrator over ``bootstrap``."""
    from beigebox import bootstrap
    state = await bootstrap.startup(app)
    set_state(state)
    yield
    await bootstrap.shutdown(state)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="BeigeBox",
    description="Tap the line. Control the carrier.",
    version=_BB_VERSION,
    lifespan=lifespan,
)

from beigebox.middleware import (  # noqa: E402
    ApiKeyMiddleware,
    SecurityHeadersMiddleware,
    WebAuthMiddleware,
)

app.add_middleware(ApiKeyMiddleware)
app.add_middleware(WebAuthMiddleware)
app.add_middleware(SecurityHeadersMiddleware)


# ---------------------------------------------------------------------------
# Router registration — order matters. All include_router() calls happen
# AFTER middleware registration (so routes attach to the configured stack)
# but BEFORE the catch-all route at the bottom of this file (so they don't
# shadow specific paths).
# ---------------------------------------------------------------------------

from beigebox.routers.openai import router as openai_router  # noqa: E402
from beigebox.routers.auth import router as auth_router  # noqa: E402
from beigebox.routers.security import router as security_router  # noqa: E402
from beigebox.routers.workspace import router as workspace_router  # noqa: E402
from beigebox.routers.analytics import router as analytics_router  # noqa: E402
from beigebox.routers.tools import router as tools_router  # noqa: E402
from beigebox.routers.config import router as config_router  # noqa: E402
from beigebox.routers.misc import router as misc_router  # noqa: E402

app.include_router(openai_router)
app.include_router(auth_router)
app.include_router(security_router)
app.include_router(workspace_router)
app.include_router(analytics_router)
app.include_router(tools_router)
app.include_router(config_router)
app.include_router(misc_router)


# Serve static web assets (vi.js etc.) — must come before catch-all routes.
# Stays in main.py because StaticFiles is a mount, not an APIRouter route.
_web_dir = Path(__file__).parent / "web"
if _web_dir.exists():
    app.mount("/web", StaticFiles(directory=str(_web_dir)), name="web")


# _wire_and_forward backs the catch-all route below.
from beigebox.routers._shared import _wire_and_forward  # noqa: E402


# ---------------------------------------------------------------------------
# Catch-all — anything not explicitly handled above is forwarded transparently.
# Logged to wiretap as "passthrough/unknown" for observability.
# MUST be the last route registered.
# ---------------------------------------------------------------------------

@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
)
async def catch_all(path: str, request: Request):
    """
    Transparent passthrough for any endpoint not explicitly handled.
    Keeps BeigeBox invisible to frontends and backends that use endpoints
    we haven't specifically implemented. All traffic is logged to wiretap.
    """
    # Don't forward requests to our own beigebox/* or api/v1/* endpoints —
    # those are handled above; if we're here it means they 404'd internally.
    if path.startswith("beigebox/") or path.startswith("api/v1/"):
        return JSONResponse({"error": "not found", "path": path}, status_code=404)

    # Silently reject common browser noise (don't log to wiretap, don't forward to backend)
    noise_paths = {
        "favicon.ico",
        ".well-known/appspecific/com.chrome.devtools.json",
        ".well-known/webfinger",
        "robots.txt",
        "sitemap.xml",
        ".git/config",
        ".env",
        "web.config",
    }
    if path in noise_paths or path.startswith(".well-known/") and "chrome" in path.lower():
        return JSONResponse({"error": "not found"}, status_code=404)

    return await _wire_and_forward(request, f"passthrough/{path}")


# ---------------------------------------------------------------------------
# Run with: python -m beigebox.main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    cfg = get_config()
    uvicorn.run(
        "beigebox.main:app",
        host=cfg["server"]["host"],
        port=cfg["server"]["port"],
        reload=False,
    )
