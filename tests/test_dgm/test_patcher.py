"""
Tests for DGM ConfigPatcher — safe apply/revert of runtime config changes.

These tests use mocking to avoid touching the real filesystem.
They verify:
- Allowlist enforcement (forbidden keys are rejected)
- Type validation
- Range validation
- Successful apply stores the original value
- Revert restores the original value
- Nested key lookup works correctly
"""
import pytest
from unittest.mock import patch, MagicMock, call

from beigebox.dgm.patcher import ConfigPatcher, Patch, ALLOWED_KEYS


class TestAllowlist:
    def test_allowed_key_passes_validation(self):
        patcher = ConfigPatcher()
        patch_obj = Patch(key="models.default", value="qwen3:4b")
        assert patcher.validate(patch_obj) is None

    def test_forbidden_key_fails_validation(self):
        patcher = ConfigPatcher()
        patch_obj = Patch(key="auth.api_key", value="hacked")
        error = patcher.validate(patch_obj)
        assert error is not None
        assert "allowlist" in error.lower() or "not in" in error.lower()

    def test_storage_path_forbidden(self):
        patcher = ConfigPatcher()
        patch_obj = Patch(key="storage.path", value="/etc/passwd")
        assert patcher.validate(patch_obj) is not None

    def test_all_allowed_keys_have_descriptions(self):
        """Every key in ALLOWED_KEYS should have a non-empty description."""
        for key, (types, desc) in ALLOWED_KEYS.items():
            assert desc, f"Key '{key}' has no description"
            assert types, f"Key '{key}' has no allowed types"


class TestTypeValidation:
    def test_string_value_for_string_key(self):
        patcher = ConfigPatcher()
        p = Patch(key="models.default", value="qwen3:4b")
        assert patcher.validate(p) is None

    def test_int_for_string_key_fails(self):
        patcher = ConfigPatcher()
        p = Patch(key="models.default", value=42)
        error = patcher.validate(p)
        assert error is not None
        assert "type" in error.lower() or "str" in error.lower()

    def test_float_for_temperature_key(self):
        patcher = ConfigPatcher()
        p = Patch(key="decision_llm.temperature", value=0.3)
        assert patcher.validate(p) is None

    def test_int_also_accepted_for_temperature(self):
        """Temperature allows both float and int."""
        patcher = ConfigPatcher()
        p = Patch(key="decision_llm.temperature", value=1)
        assert patcher.validate(p) is None


class TestRangeValidation:
    def test_temperature_too_high(self):
        patcher = ConfigPatcher()
        p = Patch(key="decision_llm.temperature", value=2.5)
        error = patcher.validate(p)
        assert error is not None
        assert "temperature" in error.lower() or "2.0" in error

    def test_temperature_too_low(self):
        patcher = ConfigPatcher()
        p = Patch(key="decision_llm.temperature", value=-0.1)
        assert patcher.validate(p) is not None

    def test_temperature_boundary_values(self):
        patcher = ConfigPatcher()
        assert patcher.validate(Patch(key="decision_llm.temperature", value=0.0)) is None
        assert patcher.validate(Patch(key="decision_llm.temperature", value=2.0)) is None

    def test_max_iterations_zero_fails(self):
        patcher = ConfigPatcher()
        p = Patch(key="operator.max_iterations", value=0)
        assert patcher.validate(p) is not None

    def test_session_ttl_below_minimum(self):
        patcher = ConfigPatcher()
        p = Patch(key="routing.session_cache.ttl_seconds", value=30)
        assert patcher.validate(p) is not None

    def test_session_ttl_minimum_ok(self):
        patcher = ConfigPatcher()
        p = Patch(key="routing.session_cache.ttl_seconds", value=60)
        assert patcher.validate(p) is None


class TestApplyAndRevert:
    def test_apply_stores_original(self):
        patcher = ConfigPatcher()
        with (
            patch("beigebox.dgm.patcher.get_runtime_config", return_value={"models": {"default": "old_model"}}),
            patch("beigebox.dgm.patcher.update_runtime_config", return_value=True) as mock_update,
        ):
            result = patcher.apply(Patch(key="models.default", value="new_model"))

        assert result.ok
        assert result.original == "old_model"
        mock_update.assert_called_once_with("models.default", "new_model")

    def test_apply_returns_none_original_for_missing_key(self):
        """If the key doesn't exist yet, original should be None."""
        patcher = ConfigPatcher()
        with (
            patch("beigebox.dgm.patcher.get_runtime_config", return_value={}),
            patch("beigebox.dgm.patcher.update_runtime_config", return_value=True),
        ):
            result = patcher.apply(Patch(key="models.default", value="new_model"))

        assert result.ok
        assert result.original is None

    def test_apply_fails_on_invalid_key(self):
        patcher = ConfigPatcher()
        result = patcher.apply(Patch(key="auth.api_key", value="hacked"))
        assert not result.ok
        assert result.error

    def test_apply_fails_when_update_returns_false(self):
        patcher = ConfigPatcher()
        with (
            patch("beigebox.dgm.patcher.get_runtime_config", return_value={}),
            patch("beigebox.dgm.patcher.update_runtime_config", return_value=False),
        ):
            result = patcher.apply(Patch(key="models.default", value="new_model"))

        assert not result.ok

    def test_revert_calls_update_with_original(self):
        patcher = ConfigPatcher()
        with patch("beigebox.dgm.patcher.update_runtime_config", return_value=True) as mock_update:
            patcher.revert(Patch(key="models.default", value="new"), original="old_model")

        mock_update.assert_called_once_with("models.default", "old_model")

    def test_revert_with_none_removes_key(self):
        """Reverting to None should remove the key."""
        patcher = ConfigPatcher()
        with patch("beigebox.dgm.patcher.update_runtime_config", return_value=True) as mock_update:
            patcher.revert(Patch(key="models.default", value="new"), original=None)

        mock_update.assert_called_once_with("models.default", None)


class TestNestedKeyLookup:
    def test_shallow_key(self):
        patcher = ConfigPatcher()
        d = {"foo": "bar"}
        assert patcher._get_nested(d, "foo") == "bar"

    def test_deeply_nested_key(self):
        patcher = ConfigPatcher()
        d = {"a": {"b": {"c": 42}}}
        assert patcher._get_nested(d, "a.b.c") == 42

    def test_missing_key_returns_none(self):
        patcher = ConfigPatcher()
        d = {"a": {"b": 1}}
        assert patcher._get_nested(d, "a.c") is None

    def test_intermediate_non_dict_returns_none(self):
        patcher = ConfigPatcher()
        d = {"a": "not_a_dict"}
        assert patcher._get_nested(d, "a.b") is None
