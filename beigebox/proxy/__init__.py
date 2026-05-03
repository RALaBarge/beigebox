"""BeigeBox proxy package.

The orchestrator (``Proxy``) lives in ``core``. Pure helpers extracted from
the original monolithic ``proxy.py`` live in sibling modules:

- ``request_helpers``  — message-shape inspection, conversation-id, model
  resolution, dedupe.
- ``body_pipeline``    — generation params / model options / window config
  injection (mutates outbound body).
- ``model_listing``    — ``/v1/models`` aggregation + advertise-mode rewrite.
- ``request_inspector`` — ring buffer of the last N outbound payloads.

Re-exporting ``Proxy`` here keeps ``from beigebox.proxy import Proxy``
working for the dozens of call sites that already use it.
"""
from beigebox.proxy.core import Proxy

# Re-exports kept for tests that still ``patch("beigebox.proxy.get_config")``
# / ``patch("beigebox.proxy.get_runtime_config")``. The patches need a name
# bound at the package surface to bind onto.
from beigebox.config import get_config, get_runtime_config  # noqa: F401

__all__ = ["Proxy", "get_config", "get_runtime_config"]
