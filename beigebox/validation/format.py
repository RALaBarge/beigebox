"""
Response format validation — optional, non-blocking.

Validates LLM responses against a declared format (json, json_array, xml, yaml)
or against an explicit JSON Schema. Validation failures are logged as warnings
and never block the response pipeline.

Configuration (runtime_config.yaml):

    response_validation:
      enabled: false
      schemas:
        # Named schemas used by proxy.py or external callers.
        # Values are JSON Schema objects (only validated for 'json'/'json_array' formats).
        openai:
          type: object
          properties:
            choices:
              type: array

Usage:

    from beigebox.validation.format import ResponseValidator

    validator = ResponseValidator(cfg)
    result = validator.validate(text, fmt="json")
    # result.valid  → bool
    # result.format → the fmt that was checked
    # result.error  → str if not valid, else ""

    # Non-streaming (full dict response):
    result = validator.validate_response(data, model="gpt-4o")
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from beigebox.logging import log_error_event

logger = logging.getLogger(__name__)

# Supported format tokens
FORMATS = frozenset({"json", "json_array", "xml", "yaml"})


@dataclass
class ValidationResult:
    valid: bool
    format: str = ""
    error: str = ""
    schema_errors: list[str] = field(default_factory=list)


class ResponseValidator:
    """
    Validate response text (or assembled streaming text) against a declared format.

    Parameters
    ----------
    cfg:
        Full config dict (config.yaml).  The ``response_validation`` block is
        read from ``runtime_config`` on every call so it hot-reloads without a
        restart.  ``cfg`` is kept for future schema defaults stored in the
        static config.
    """

    def __init__(self, cfg: dict | None = None) -> None:
        self._cfg = cfg or {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(
        self,
        text: str,
        fmt: str,
        schema: dict | None = None,
    ) -> ValidationResult:
        """
        Validate *text* against *fmt*.

        Parameters
        ----------
        text:
            The raw string to validate (may be the full assembled response or
            the ``content`` field extracted from a chat completion dict).
        fmt:
            One of ``"json"``, ``"json_array"``, ``"xml"``, ``"yaml"``.
            Unknown values are accepted without error (pass-through).
        schema:
            Optional JSON Schema dict.  Only applied when fmt is ``"json"`` or
            ``"json_array"``.  Requires ``jsonschema`` to be installed; if it
            is absent the structural check still runs but schema validation is
            skipped with a debug log.

        Returns
        -------
        ValidationResult
        """
        fmt = (fmt or "").strip().lower()
        if not fmt or fmt not in FORMATS:
            return ValidationResult(valid=True, format=fmt)

        try:
            if fmt == "json":
                return self._validate_json(text, schema)
            elif fmt == "json_array":
                return self._validate_json_array(text, schema)
            elif fmt == "xml":
                return self._validate_xml(text)
            elif fmt == "yaml":
                return self._validate_yaml(text)
        except Exception as exc:
            logger.debug("ResponseValidator.validate raised unexpectedly: %s", exc)
            return ValidationResult(valid=True, format=fmt)  # never block

        return ValidationResult(valid=True, format=fmt)

    def validate_response(
        self,
        data: dict,
        model: str = "",
        fmt: str = "",
        schema: dict | None = None,
    ) -> ValidationResult:
        """
        Validate the ``content`` field of a non-streaming chat completion *data* dict.

        Reads ``response_validation`` from runtime_config each call (hot-reload).
        Non-blocking: any exception is caught and logged; returns valid=True.
        """
        if not self._is_enabled():
            return ValidationResult(valid=True, format=fmt)

        try:
            choices = data.get("choices", [])
            if not choices:
                return ValidationResult(valid=True, format=fmt)
            content = choices[0].get("message", {}).get("content", "")
            if not content:
                return ValidationResult(valid=True, format=fmt)

            effective_fmt = fmt or self._detect_format(content)
            if not effective_fmt:
                return ValidationResult(valid=True, format="")

            effective_schema = schema or self._schema_for_model(model)
            result = self.validate(content, effective_fmt, schema=effective_schema)
            self._log_result(result, model=model, source="non-stream")
            return result
        except Exception as exc:
            logger.debug("validate_response error (suppressed): %s", exc)
            return ValidationResult(valid=True, format=fmt)

    def validate_stream_buffer(
        self,
        complete_text: str,
        model: str = "",
        fmt: str = "",
        schema: dict | None = None,
    ) -> ValidationResult:
        """
        Validate the fully assembled streaming response text.

        Same semantics as ``validate_response`` but operates on raw text.
        Non-blocking.
        """
        if not self._is_enabled():
            return ValidationResult(valid=True, format=fmt)

        try:
            if not complete_text:
                return ValidationResult(valid=True, format=fmt)

            effective_fmt = fmt or self._detect_format(complete_text)
            if not effective_fmt:
                return ValidationResult(valid=True, format="")

            effective_schema = schema or self._schema_for_model(model)
            result = self.validate(complete_text, effective_fmt, schema=effective_schema)
            self._log_result(result, model=model, source="stream")
            return result
        except Exception as exc:
            logger.debug("validate_stream_buffer error (suppressed): %s", exc)
            return ValidationResult(valid=True, format=fmt)

    # ------------------------------------------------------------------
    # Format validators
    # ------------------------------------------------------------------

    def _validate_json(self, text: str, schema: dict | None) -> ValidationResult:
        """Parse text as JSON object (or any JSON value if no schema)."""
        stripped = text.strip()
        # Extract first JSON block from prose (common LLM output pattern)
        stripped = _extract_json_block(stripped)
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            return ValidationResult(
                valid=False, format="json",
                error=f"Invalid JSON: {exc}",
            )

        if schema is None:
            return ValidationResult(valid=True, format="json")

        schema_errors = _validate_jsonschema(parsed, schema)
        if schema_errors:
            return ValidationResult(
                valid=False, format="json",
                error="JSON schema validation failed",
                schema_errors=schema_errors,
            )
        return ValidationResult(valid=True, format="json")

    def _validate_json_array(self, text: str, schema: dict | None) -> ValidationResult:
        """Parse text as a JSON array."""
        stripped = _extract_json_block(text.strip())
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            return ValidationResult(
                valid=False, format="json_array",
                error=f"Invalid JSON: {exc}",
            )
        if not isinstance(parsed, list):
            return ValidationResult(
                valid=False, format="json_array",
                error=f"Expected JSON array, got {type(parsed).__name__}",
            )
        if schema:
            schema_errors = _validate_jsonschema(parsed, schema)
            if schema_errors:
                return ValidationResult(
                    valid=False, format="json_array",
                    error="JSON array schema validation failed",
                    schema_errors=schema_errors,
                )
        return ValidationResult(valid=True, format="json_array")

    def _validate_xml(self, text: str) -> ValidationResult:
        """Basic XML well-formedness check using stdlib xml.etree."""
        import xml.etree.ElementTree as ET
        stripped = text.strip()
        # Wrap in a root if the response is a fragment (multiple top-level elements)
        if not stripped.startswith("<"):
            return ValidationResult(
                valid=False, format="xml",
                error="Response does not start with '<' — not XML",
            )
        try:
            # Try direct parse first
            ET.fromstring(stripped)
            return ValidationResult(valid=True, format="xml")
        except ET.ParseError:
            # Try wrapping in a synthetic root (handles multi-element fragments)
            try:
                ET.fromstring(f"<__root__>{stripped}</__root__>")
                return ValidationResult(valid=True, format="xml")
            except ET.ParseError as exc:
                return ValidationResult(
                    valid=False, format="xml",
                    error=f"Malformed XML: {exc}",
                )

    def _validate_yaml(self, text: str) -> ValidationResult:
        """Basic YAML parse check."""
        try:
            import yaml  # type: ignore[import]
        except ImportError:
            logger.debug("PyYAML not installed — skipping YAML validation")
            return ValidationResult(valid=True, format="yaml")

        try:
            yaml.safe_load(text)
            return ValidationResult(valid=True, format="yaml")
        except yaml.YAMLError as exc:
            return ValidationResult(
                valid=False, format="yaml",
                error=f"Invalid YAML: {exc}",
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_enabled(self) -> bool:
        from beigebox.config import get_runtime_config
        rt = get_runtime_config()
        return bool(rt.get("response_validation", {}).get("enabled", False))

    def _schema_for_model(self, model: str) -> dict | None:
        """Look up a named schema from runtime_config by model name."""
        from beigebox.config import get_runtime_config
        rt = get_runtime_config()
        schemas = rt.get("response_validation", {}).get("schemas", {})
        return schemas.get(model) if model else None

    def _detect_format(self, text: str) -> str:
        """Heuristically detect whether text looks like JSON/XML/YAML."""
        stripped = text.strip()
        if stripped.startswith(("{", "[")):
            return "json"
        if stripped.startswith("<") and not stripped.startswith("<<"):
            return "xml"
        return ""

    def _log_result(self, result: ValidationResult, model: str, source: str) -> None:
        if result.valid:
            logger.debug(
                "ResponseValidator [%s] model=%s fmt=%s: valid",
                source, model or "?", result.format,
            )
        else:
            error_msg = f"Validation [{source}] {result.format}: {result.error}"
            if result.schema_errors:
                error_msg += f" (schema_errors: {result.schema_errors})"
            logger.warning(
                "ResponseValidator [%s] model=%s fmt=%s: INVALID — %s%s",
                source, model or "?", result.format, result.error,
                f" (schema_errors: {result.schema_errors})" if result.schema_errors else "",
            )
            try:
                log_error_event("response_validator", error_msg, severity="warning")
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _extract_json_block(text: str) -> str:
    """
    If the text is not bare JSON, try to extract the first JSON block from
    fenced code (```json ... ```) or the first {...} / [...] span.
    Returns the original text if no extraction succeeds.
    """
    # Fenced code block: ```json ... ```
    m = re.search(r"```(?:json)?\s*\n?([\s\S]+?)\n?```", text, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # Bare JSON object or array starting at first { or [
    for start_char, end_char in (("{", "}"), ("[", "]")):
        idx = text.find(start_char)
        if idx != -1:
            # Find the matching closing bracket
            depth = 0
            for i, ch in enumerate(text[idx:], start=idx):
                if ch == start_char:
                    depth += 1
                elif ch == end_char:
                    depth -= 1
                    if depth == 0:
                        candidate = text[idx : i + 1]
                        try:
                            json.loads(candidate)
                            return candidate
                        except json.JSONDecodeError:
                            break

    return text


def _validate_jsonschema(instance: Any, schema: dict) -> list[str]:
    """
    Validate *instance* against *schema* using jsonschema.
    Returns a list of error messages (empty = valid).
    If jsonschema is not installed, returns [] (skip silently).
    """
    try:
        import jsonschema  # type: ignore[import]
    except ImportError:
        logger.debug("jsonschema not installed — skipping schema validation")
        return []

    errors = list(jsonschema.Draft7Validator(schema).iter_errors(instance))
    return [e.message for e in errors]
