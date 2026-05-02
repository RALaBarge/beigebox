"""FastAPI APIRouter modules extracted from beigebox/main.py.

Each router groups endpoints by functional area (auth, openai-compat,
analytics, etc.). main.py imports each router and calls
``app.include_router(...)`` after middleware registration but before
the catch-all route.

State is reached via ``beigebox.state.get_state()`` (NOT
``beigebox.main.get_state``) — the dedicated state module breaks the
import cycle that would otherwise form (routers → main → routers).

Shared helpers (``_require_admin``, ``_emit_auth_denied``,
``_wire_and_forward``, ``_index_document``) live in ``_shared.py``;
each gets extracted from main.py when the first router that needs it
lands.
"""
