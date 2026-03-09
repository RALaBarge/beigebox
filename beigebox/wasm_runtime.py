"""
WASM Runtime — post-processing transform layer for BeigeBox.

WASM modules act as a pipeline transform sitting between the backend response
and logging/forwarding. Each module reads bytes from stdin and writes (possibly
modified) bytes to stdout.

ABI: WASI stdio
  - transform_response: input is JSON-encoded response dict, output is JSON
  - transform_text:     input is UTF-8 text, output is UTF-8 text
  - transform_input:    input is raw bytes (e.g. PDF), output is UTF-8 text
  - Timeout: if a module exceeds timeout_ms the original content passes through

Any failure (load error, exec error, bad output, timeout) is silently swallowed —
WASM is never on the critical path.
"""

import asyncio
import json
import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor

from beigebox.config import get_runtime_config

logger = logging.getLogger(__name__)


class WasmRuntime:
    """
    Manages a pool of compiled WASM modules and executes them as transforms.

    Modules are loaded from disk at startup (via wasmtime-py) and cached.
    Each transform call runs in a thread pool executor so the async event loop
    is never blocked. A configurable timeout aborts slow modules and falls
    through to the original content.
    """

    def __init__(self, cfg: dict):
        self._cfg = cfg.get("wasm", {})
        self._enabled = self._cfg.get("enabled", False)
        self._timeout_ms = self._cfg.get("timeout_ms", 500)
        self._modules_cfg = self._cfg.get("modules", {})
        self._default_module = self._cfg.get("default_module", "")
        self._loaded: dict = {}       # name -> wasmtime.Module
        self._engine = None
        # Two workers: one active transform + one warm slot for back-to-back
        # requests. More workers would spin up idle threads; WASM transforms
        # are CPU-bound so they don't benefit from high parallelism.
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="wasm-rt")

        if self._enabled:
            self._init_engine()
            self._load_modules()

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def _init_engine(self):
        try:
            from wasmtime import Engine
            self._engine = Engine()
            logger.info("WasmRuntime: engine initialized")
        except ImportError:
            logger.error(
                "wasmtime-py not installed — WASM transforms disabled. "
                "Install with: pip install wasmtime"
            )
            self._enabled = False
        except Exception as e:
            logger.error("WasmRuntime: engine init failed: %s", e)
            self._enabled = False

    def _load_modules(self):
        if not self._engine:
            return
        try:
            from wasmtime import Module
        except ImportError:
            return

        for name, mcfg in self._modules_cfg.items():
            if not mcfg.get("enabled", True):
                logger.debug("WASM module '%s' disabled in config", name)
                continue
            path = mcfg.get("path", "")
            if not path:
                logger.warning("WASM module '%s' has no path configured", name)
                continue
            if not os.path.exists(path):
                logger.warning("WASM module '%s' not found at: %s", name, path)
                continue
            try:
                self._loaded[name] = Module.from_file(self._engine, path)
                logger.info("WASM module loaded: %s  (%s)", name, path)
            except Exception as e:
                logger.error("Failed to load WASM module '%s' from %s: %s", name, path, e)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _effective_module(self, requested: str) -> str:
        """Return the module to actually use — requested name, or config default.

        Falls back to the default module when the requested name is empty or
        not loaded (e.g. decision LLM returned a module name that wasn't compiled).
        Returns "" when neither is available, which callers treat as a no-op.
        """
        if requested and requested in self._loaded:
            return requested
        if self._default_module and self._default_module in self._loaded:
            return self._default_module
        return ""

    def _run_wasm_sync(self, module_name: str, input_bytes: bytes) -> bytes:
        """
        Execute a WASM module synchronously (called inside thread pool).

        Uses temp files for stdin/stdout rather than in-process pipes because
        wasmtime-py's WasiConfig requires file paths, not file objects. The temp
        files are always deleted in the finally block, even on exception.

        Returns output bytes, or the original input bytes on any failure.
        """
        from wasmtime import Store, Linker, WasiConfig

        module = self._loaded.get(module_name)
        if not module:
            return input_bytes

        fd_in, stdin_path = tempfile.mkstemp(suffix=".wasm_in")
        fd_out, stdout_path = tempfile.mkstemp(suffix=".wasm_out")
        os.close(fd_in)
        os.close(fd_out)

        try:
            with open(stdin_path, "wb") as f:
                f.write(input_bytes)

            config = WasiConfig()
            config.stdin_file(stdin_path)
            config.stdout_file(stdout_path)
            # inherit_stderr: let the module write to the host's stderr for
            # debugging without capturing it as output to be returned.
            config.inherit_stderr()

            store = Store(self._engine)
            store.set_wasi(config)

            linker = Linker(self._engine)
            linker.define_wasi()

            instance = linker.instantiate(store, module)
            exports = instance.exports(store)
            # WASI modules export "_start" as the entry point (equivalent to
            # main()). Some modules may export a different function — this
            # handles the standard WASI target compiled with Rust/C.
            start = exports.get("_start")
            if start:
                start(store)

            with open(stdout_path, "rb") as f:
                result = f.read()

            return result if result else input_bytes

        except Exception as e:
            logger.warning("WASM module '%s' execution error: %s", module_name, e)
            return input_bytes
        finally:
            for path in (stdin_path, stdout_path):
                try:
                    os.unlink(path)
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def transform_response(self, module_name: str, data: dict) -> dict:
        """
        Transform a full response dict through a WASM module.

        Serializes data to JSON → module stdin.
        Parses module stdout as JSON → returned dict.
        Falls through unmodified on timeout, bad JSON, or any error.
        """
        effective = self._effective_module(module_name)
        if not self._enabled or not effective:
            return data

        input_bytes = json.dumps(data).encode()
        timeout_ms = get_runtime_config().get("wasm_timeout_ms", self._timeout_ms)
        timeout_s = timeout_ms / 1000.0

        try:
            loop = asyncio.get_event_loop()
            output_bytes = await asyncio.wait_for(
                loop.run_in_executor(
                    self._executor, self._run_wasm_sync, effective, input_bytes
                ),
                timeout=timeout_s,
            )
            result = json.loads(output_bytes)
            logger.debug(
                "WASM '%s' transform_response: %d → %d bytes",
                effective, len(input_bytes), len(output_bytes),
            )
            return result
        except asyncio.TimeoutError:
            logger.warning(
                "WASM module '%s' timed out after %dms — passing through",
                effective, timeout_ms,
            )
            return data
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(
                "WASM module '%s' output is not valid JSON: %s — passing through",
                effective, e,
            )
            return data
        except Exception as e:
            logger.warning(
                "WASM transform_response failed (%s): %s — passing through", effective, e
            )
            return data

    async def transform_input(self, module_name: str, raw_bytes: bytes) -> str:
        """
        Pre-process raw input bytes through a WASM module (e.g. PDF → markdown).

        raw_bytes → module stdin.
        Module stdout decoded as UTF-8 → returned string.
        Returns empty string on timeout, decode error, or any failure.
        """
        effective = self._effective_module(module_name)
        if not self._enabled or not effective:
            return ""

        timeout_ms = get_runtime_config().get("wasm_timeout_ms", self._timeout_ms)
        timeout_s = timeout_ms / 1000.0

        try:
            loop = asyncio.get_event_loop()
            output_bytes = await asyncio.wait_for(
                loop.run_in_executor(
                    self._executor, self._run_wasm_sync, effective, raw_bytes
                ),
                timeout=timeout_s,
            )
            result = output_bytes.decode("utf-8")
            logger.debug(
                "WASM '%s' transform_input: %d bytes in → %d chars out",
                effective, len(raw_bytes), len(result),
            )
            return result
        except asyncio.TimeoutError:
            logger.warning(
                "WASM module '%s' timed out after %dms — returning empty",
                effective, timeout_ms,
            )
            return ""
        except UnicodeDecodeError as e:
            logger.warning(
                "WASM module '%s' output is not valid UTF-8: %s — returning empty",
                effective, e,
            )
            return ""
        except Exception as e:
            logger.warning(
                "WASM transform_input failed (%s): %s — returning empty", effective, e
            )
            return ""

    async def transform_text(self, module_name: str, text: str) -> str:
        """
        Transform a text string through a WASM module.

        Encodes text as UTF-8 → module stdin.
        Decodes module stdout as UTF-8 → returned string.
        Falls through unmodified on timeout, decode error, or any error.
        """
        effective = self._effective_module(module_name)
        if not self._enabled or not effective:
            return text

        input_bytes = text.encode("utf-8")
        timeout_ms = get_runtime_config().get("wasm_timeout_ms", self._timeout_ms)
        timeout_s = timeout_ms / 1000.0

        try:
            loop = asyncio.get_event_loop()
            output_bytes = await asyncio.wait_for(
                loop.run_in_executor(
                    self._executor, self._run_wasm_sync, effective, input_bytes
                ),
                timeout=timeout_s,
            )
            result = output_bytes.decode("utf-8")
            logger.debug(
                "WASM '%s' transform_text: %d → %d chars",
                effective, len(text), len(result),
            )
            return result
        except asyncio.TimeoutError:
            logger.warning(
                "WASM module '%s' timed out after %dms — passing through",
                effective, timeout_ms,
            )
            return text
        except UnicodeDecodeError as e:
            logger.warning(
                "WASM module '%s' output is not valid UTF-8: %s — passing through",
                effective, e,
            )
            return text
        except Exception as e:
            logger.warning(
                "WASM transform_text failed (%s): %s — passing through", effective, e
            )
            return text

    def enable(self, cfg: dict) -> bool:
        """
        Lazy-initialize engine and load modules from cfg.
        Safe to call even if already enabled — re-reads modules from disk.
        Returns True if engine is available after the call.
        """
        if self._engine is None:
            self._cfg = cfg.get("wasm", {})
            self._timeout_ms = self._cfg.get("timeout_ms", 500)
            self._modules_cfg = self._cfg.get("modules", {})
            self._default_module = self._cfg.get("default_module", "")
            self._init_engine()
        if self._engine:
            self._load_modules()
            self._enabled = True
        logger.info("WasmRuntime: enabled=%s modules=%s", self._enabled, list(self._loaded.keys()))
        return self._enabled

    def disable(self) -> None:
        """Disable WASM transforms without unloading modules."""
        self._enabled = False
        logger.info("WasmRuntime: disabled")

    def reload(self) -> list[str]:
        """
        Reload all modules from disk, re-reading config for updated paths/enabled flags.
        Safe to call at runtime — existing loaded dict is replaced atomically.
        Returns list of successfully loaded module names.
        """
        from beigebox.config import get_config
        fresh = get_config()
        self._cfg = fresh.get("wasm", {})
        self._modules_cfg = self._cfg.get("modules", {})
        self._default_module = self._cfg.get("default_module", "")
        # Clear before re-loading so modules that were removed from config
        # don't persist in the dict after the reload.
        self._loaded.clear()
        if self._engine:
            self._load_modules()
        loaded = list(self._loaded.keys())
        logger.info("WasmRuntime: reloaded — modules: %s", loaded)
        return loaded

    def list_modules(self) -> list[str]:
        """Return names of all successfully loaded modules."""
        return list(self._loaded.keys())

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def default_module(self) -> str:
        return self._default_module

    @default_module.setter
    def default_module(self, value: str) -> None:
        self._default_module = value
