"""Shared helpers used across multiple router modules.

Currently empty — helpers are extracted from beigebox/main.py
incrementally, alongside the first router commit that needs each one:

- ``_require_admin``      → moves with routers/auth.py (B-3)
- ``_emit_auth_denied``   → moves with routers/auth.py (B-3)
- ``_wire_and_forward``   → moves with routers/openai.py (B-2)
- ``_index_document``     → moves with routers/workspace.py (B-4)

This module intentionally has no imports yet; each router that needs
to add a helper imports it here and updates main.py to point at the
new location in the same commit.
"""
from __future__ import annotations
