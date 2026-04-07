"""Tests for model alias resolver."""
import pytest
from beigebox.aliases import AliasResolver


def _r(aliases: dict) -> AliasResolver:
    return AliasResolver({"aliases": aliases})


def test_resolves_alias():
    r = _r({"fast": "qwen3:4b"})
    assert r.resolve("fast") == "qwen3:4b"


def test_passthrough_unknown():
    r = _r({"fast": "qwen3:4b"})
    assert r.resolve("mistral:7b") == "mistral:7b"


def test_empty_string_passthrough():
    r = _r({"fast": "qwen3:4b"})
    assert r.resolve("") == ""


def test_no_aliases_configured():
    r = AliasResolver({})
    assert r.resolve("fast") == "fast"


def test_list_aliases():
    r = _r({"fast": "qwen3:4b", "smart": "qwen3:30b"})
    aliases = r.list_aliases()
    assert aliases["fast"] == "qwen3:4b"
    assert aliases["smart"] == "qwen3:30b"


def test_multiple_aliases():
    r = _r({"fast": "qwen3:4b", "smart": "qwen3:30b", "cheap": "llama3.2:1b"})
    assert r.resolve("smart") == "qwen3:30b"
    assert r.resolve("cheap") == "llama3.2:1b"
