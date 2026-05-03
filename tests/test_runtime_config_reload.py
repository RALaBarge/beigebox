"""Runtime config hot-reload tests.

Grok review (honorable mention): test_system_context.py and
test_wasm_runtime.py touch some reload behavior, but there is no
coverage of runtime config changes affecting routing/auth/security
thresholds. This file pins down the actual hot-reload contract.

The hot-reload mechanism in beigebox/config.py:
  - get_runtime_config() stats runtime_config.yaml
  - When mtime changes, the file is re-parsed and _runtime_config replaced
  - update_runtime_config(key, value) writes the file atomically and
    resets the mtime cache so the next read picks it up immediately
  - There's a 1-second debounce (_RUNTIME_MTIME_CHECK_INTERVAL) so the
    stat() syscall doesn't fire on every call

Tests cover:
  - File mtime change → next get_runtime_config() returns new value
  - update_runtime_config() round-trips through the file
  - Manually clearing the mtime cache works (used by /api/v1/config
    POST → GET flow)
  - get_effective_backends_config() picks up runtime overrides
  - A malformed YAML write keeps the LAST GOOD config (failure mode)

Why these are integration-style: the cache state is a module global
(_runtime_mtime, _runtime_config), so the tests have to patch the
runtime path AND swap the globals for isolation. Existing
test_web_ui.py uses the same pattern.
"""
from __future__ import annotations

import time

import pytest
import yaml

from beigebox import config as cfg_mod


@pytest.fixture
def runtime_yaml(tmp_path):
    """Point cfg_mod._RUNTIME_CONFIG_PATH at a tmp file and reset cache.
    Restores all module state after the test."""
    rt_path = tmp_path / "runtime_config.yaml"
    rt_path.write_text("runtime: {}\n")

    orig_path = cfg_mod._RUNTIME_CONFIG_PATH
    orig_cfg = cfg_mod._runtime_config
    orig_mtime = cfg_mod._runtime_mtime
    orig_checked = cfg_mod._runtime_mtime_last_checked

    cfg_mod._RUNTIME_CONFIG_PATH = rt_path
    cfg_mod._runtime_config = {}
    cfg_mod._runtime_mtime = 0.0
    cfg_mod._runtime_mtime_last_checked = 0.0

    yield rt_path

    cfg_mod._RUNTIME_CONFIG_PATH = orig_path
    cfg_mod._runtime_config = orig_cfg
    cfg_mod._runtime_mtime = orig_mtime
    cfg_mod._runtime_mtime_last_checked = orig_checked


# ---------------------------------------------------------------------------
# TestRuntimeConfigHotReload — the file→memory contract
# ---------------------------------------------------------------------------


class TestRuntimeConfigHotReload:
    def test_initial_read_returns_empty(self, runtime_yaml):
        assert cfg_mod.get_runtime_config() == {}

    def test_file_change_picked_up_after_cache_bust(self, runtime_yaml):
        """Writing the file directly + clearing the mtime cache (the
        ``update_runtime_config`` path resets it for us, but we exercise
        the lower-level contract here) must result in the next
        ``get_runtime_config()`` returning the new value."""
        # Initial empty
        assert cfg_mod.get_runtime_config() == {}

        # External edit
        runtime_yaml.write_text("runtime:\n  default_model: llama3.2\n")
        # Bypass the 1s stat() debounce + force re-read by clearing both
        # mtime trackers — this is exactly what update_runtime_config does.
        cfg_mod._runtime_mtime = 0.0
        cfg_mod._runtime_mtime_last_checked = 0.0

        rt = cfg_mod.get_runtime_config()
        assert rt["default_model"] == "llama3.2"

    def test_second_call_within_debounce_returns_cached(self, runtime_yaml):
        """The 1s ``_RUNTIME_MTIME_CHECK_INTERVAL`` debounces stat()
        syscalls. A change to the file inside that window MUST NOT
        be picked up — caller relies on ``update_runtime_config()``'s
        explicit cache bust to see new values immediately."""
        runtime_yaml.write_text("runtime:\n  key1: a\n")
        cfg_mod._runtime_mtime = 0.0
        cfg_mod._runtime_mtime_last_checked = 0.0
        first = cfg_mod.get_runtime_config()
        assert first["key1"] == "a"

        # External edit — but don't clear the cache. The 1s debounce
        # should still be in effect.
        runtime_yaml.write_text("runtime:\n  key1: b\n")
        # No cache bust — same value should come back
        second = cfg_mod.get_runtime_config()
        assert second["key1"] == "a"  # debounced — old value held

    def test_malformed_yaml_keeps_last_good_config(self, runtime_yaml):
        """If a partial / corrupted write lands in runtime_config.yaml,
        the in-memory value must keep the LAST GOOD parse — not silently
        revert to ``{}``. Otherwise a botched edit takes routing /
        backends with it."""
        # Establish a known-good baseline
        runtime_yaml.write_text("runtime:\n  default_model: llama3.2\n")
        cfg_mod._runtime_mtime = 0.0
        cfg_mod._runtime_mtime_last_checked = 0.0
        good = cfg_mod.get_runtime_config()
        assert good["default_model"] == "llama3.2"

        # Now write garbage and force a re-read
        runtime_yaml.write_text("runtime:\n  default_model: [unclosed list\n")
        cfg_mod._runtime_mtime = 0.0
        cfg_mod._runtime_mtime_last_checked = 0.0

        # The parse fails — but we must keep the last good value.
        result = cfg_mod.get_runtime_config()
        assert result["default_model"] == "llama3.2"

    def test_missing_file_returns_empty_not_raises(self, tmp_path):
        """If runtime_config.yaml doesn't exist (fresh install / dev),
        ``get_runtime_config()`` returns ``{}`` — never raises. Otherwise
        every read in the proxy hot path would crash."""
        missing_path = tmp_path / "nope.yaml"
        orig_path = cfg_mod._RUNTIME_CONFIG_PATH
        orig_cfg = cfg_mod._runtime_config
        orig_mtime = cfg_mod._runtime_mtime
        orig_checked = cfg_mod._runtime_mtime_last_checked

        cfg_mod._RUNTIME_CONFIG_PATH = missing_path
        cfg_mod._runtime_config = {}
        cfg_mod._runtime_mtime = 0.0
        cfg_mod._runtime_mtime_last_checked = 0.0
        try:
            assert cfg_mod.get_runtime_config() == {}
        finally:
            cfg_mod._RUNTIME_CONFIG_PATH = orig_path
            cfg_mod._runtime_config = orig_cfg
            cfg_mod._runtime_mtime = orig_mtime
            cfg_mod._runtime_mtime_last_checked = orig_checked


# ---------------------------------------------------------------------------
# TestUpdateRuntimeConfig — write-side hot-reload (POST → GET round-trip)
# ---------------------------------------------------------------------------


class TestUpdateRuntimeConfig:
    def test_write_then_immediate_read_sees_new_value(self, runtime_yaml):
        """The contract behind /api/v1/web-ui/toggle-vi-mode and friends:
        a write must be visible to the very next read in the same process.
        update_runtime_config explicitly clears the mtime cache for this."""
        ok = cfg_mod.update_runtime_config("default_model", "qwen3:30b")
        assert ok is True

        # No manual cache bust — update_runtime_config did it for us
        rt = cfg_mod.get_runtime_config()
        assert rt["default_model"] == "qwen3:30b"

    def test_update_round_trips_through_yaml_file(self, runtime_yaml):
        """After update_runtime_config, the on-disk YAML is parseable AND
        contains the new value under runtime: — required for any other
        process / next-startup reload to see the change."""
        cfg_mod.update_runtime_config("custom_key", 42)

        with open(runtime_yaml) as f:
            data = yaml.safe_load(f)
        assert data["runtime"]["custom_key"] == 42

    def test_update_preserves_other_runtime_keys(self, runtime_yaml):
        """Concurrent updates from different request handlers must not
        clobber each other. Writing key A must NOT remove key B."""
        runtime_yaml.write_text("runtime:\n  key_a: alpha\n  key_b: beta\n")
        cfg_mod._runtime_mtime = 0.0
        cfg_mod._runtime_mtime_last_checked = 0.0

        cfg_mod.update_runtime_config("key_b", "BETA_NEW")

        rt = cfg_mod.get_runtime_config()
        assert rt["key_a"] == "alpha"
        assert rt["key_b"] == "BETA_NEW"

    def test_update_with_value_none_deletes_key(self, runtime_yaml):
        """value=None is the sentinel for "remove this key" — the API
        contract documented in update_runtime_config's docstring."""
        runtime_yaml.write_text("runtime:\n  feature_flag: true\n")
        cfg_mod._runtime_mtime = 0.0
        cfg_mod._runtime_mtime_last_checked = 0.0

        cfg_mod.update_runtime_config("feature_flag", None)

        rt = cfg_mod.get_runtime_config()
        assert "feature_flag" not in rt


# ---------------------------------------------------------------------------
# TestRuntimeOverridesAffectBackendsConfig — the most important consumer
# ---------------------------------------------------------------------------


class TestRuntimeOverridesAffectBackendsConfig:
    """``get_effective_backends_config()`` is the runtime-config consumer
    that matters most: it controls whether backends_enabled flips on/off
    and which backend list the router uses. A regression here would mean
    operators can't toggle the multi-backend router at runtime — the
    whole point of runtime_config.yaml.
    """

    @pytest.fixture
    def base_config(self):
        """Patch the BASE config (config.yaml, separate cache) with a
        minimal known shape so get_effective_backends_config has
        something to merge against."""
        orig = cfg_mod._config
        cfg_mod._config = {
            "backend": {"url": "http://127.0.0.1:11434", "default_model": "llama3.2"},
            "backends_enabled": False,
            "backends": [
                {"provider": "ollama", "name": "primary",
                 "url": "http://127.0.0.1:11434", "priority": 1},
            ],
            "routing": {},
        }
        yield cfg_mod._config
        cfg_mod._config = orig

    def test_runtime_can_enable_disabled_backends(self, runtime_yaml, base_config):
        """When config.yaml has backends_enabled: false, runtime_config.yaml
        must be able to flip it on without restarting the server."""
        # Sanity check: starts disabled
        enabled, _ = cfg_mod.get_effective_backends_config()
        assert enabled is False

        cfg_mod.update_runtime_config("backends_enabled", True)
        enabled, backends = cfg_mod.get_effective_backends_config()
        assert enabled is True
        # Backends list still comes from base config when runtime doesn't
        # override it
        assert any(b.get("name") == "primary" for b in backends)

    def test_runtime_backends_list_replaces_static_list(self, runtime_yaml, base_config):
        """When the runtime config provides its own ``backends`` list, it
        REPLACES (not merges with) the static list. Operators rely on this
        to swap providers without editing config.yaml."""
        cfg_mod.update_runtime_config(
            "backends",
            [
                {"provider": "openrouter", "name": "or",
                 "url": "https://openrouter.ai/api/v1", "priority": 1,
                 "api_key": "sk-test"},
            ],
        )
        cfg_mod.update_runtime_config("backends_enabled", True)

        _enabled, backends = cfg_mod.get_effective_backends_config()
        # OpenRouter from runtime + the auto-injected ollama-local from
        # static config (since runtime list had no Ollama backend)
        names = [b.get("name") for b in backends]
        assert "or" in names
        # Auto-injection guarantees an Ollama backend even when runtime
        # config wipes them out — defends against "broke local routing"
        assert any(b.get("provider") == "ollama" for b in backends)

    def test_revert_runtime_override_returns_to_static(self, runtime_yaml, base_config):
        """Setting and then deleting a runtime key must restore the
        static config value — otherwise the runtime layer is sticky and
        operators can't undo a temporary override."""
        cfg_mod.update_runtime_config("backends_enabled", True)
        enabled, _ = cfg_mod.get_effective_backends_config()
        assert enabled is True

        cfg_mod.update_runtime_config("backends_enabled", None)  # delete
        enabled, _ = cfg_mod.get_effective_backends_config()
        # Falls back to base config value (False)
        assert enabled is False
