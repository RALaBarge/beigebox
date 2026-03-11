"""
Backend plugin auto-discovery and registration.

Users can drop custom backend implementations into backends/plugins/
and they'll be automatically discovered and registered with the router.

Example: backends/plugins/llama_cpp.py

    from beigebox.backends.base import BaseBackend, BackendResponse
    import httpx

    class LlamaCppBackend(BaseBackend):
        '''Interface to llama.cpp HTTP server'''

        async def forward(self, body: dict) -> BackendResponse:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self.url}/v1/chat/completions",
                    json=body,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                return BackendResponse(
                    ok=True,
                    data=resp.json(),
                    backend_name=self.name,
                )

        # ... implement other abstract methods
"""

import importlib.util
import logging
from pathlib import Path

from beigebox.backends.base import BaseBackend

logger = logging.getLogger(__name__)


def load_backend_plugins(plugins_dir: str = "backends/plugins") -> dict[str, type[BaseBackend]]:
    """
    Discover and load custom backend implementations from a plugins directory.

    Args:
        plugins_dir: Path to directory containing backend plugin modules

    Returns:
        Dict mapping provider name → backend class
    """
    plugins = {}
    plugins_path = Path(plugins_dir)

    if not plugins_path.exists():
        logger.debug(f"Backend plugins directory not found: {plugins_dir}")
        return plugins

    # Find all Python files in plugins directory
    for py_file in plugins_path.glob("*.py"):
        if py_file.name.startswith("_"):
            continue

        try:
            # Dynamically import module
            spec = importlib.util.spec_from_file_location(
                f"beigebox.backends.plugins.{py_file.stem}",
                py_file,
            )
            if not spec or not spec.loader:
                logger.warning(f"Could not load spec for {py_file}")
                continue

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Find all BaseBackend subclasses in the module
            for attr_name in dir(module):
                attr = getattr(module, attr_name)

                # Check if it's a class and subclass of BaseBackend (but not BaseBackend itself)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, BaseBackend)
                    and attr is not BaseBackend
                ):
                    # Provider name: convert class name to snake_case
                    # LlamaCppBackend → llama_cpp
                    provider_name = _camel_to_snake(attr.__name__.replace("Backend", ""))

                    plugins[provider_name] = attr
                    logger.info(f"Loaded backend plugin: {provider_name} ({attr.__name__})")

        except Exception as e:
            logger.error(f"Failed to load backend plugin from {py_file}: {e}")

    return plugins


def _camel_to_snake(name: str) -> str:
    """Convert CamelCase to snake_case."""
    import re
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()
