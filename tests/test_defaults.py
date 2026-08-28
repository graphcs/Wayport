"""Tests for relay URL resolution and normalization."""

from __future__ import annotations

import pytest

from wayport.common.defaults import (
    DEFAULT_RELAY_URL,
    RELAY_URL_ENV_VAR,
    normalize_relay_url,
    relay_host,
    resolve_relay_url,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("wss://relay.example.com", "wss://relay.example.com"),
        ("ws://localhost:8080", "ws://localhost:8080"),
        # What you actually get from the Railway dashboard.
        ("https://relay.example.com", "wss://relay.example.com"),
        ("http://localhost:8080", "ws://localhost:8080"),
        # A bare host should assume TLS.
        ("relay.example.com", "wss://relay.example.com"),
        # Trailing slashes and stray whitespace are common when pasting.
        ("wss://relay.example.com/", "wss://relay.example.com"),
        ("  wss://relay.example.com  ", "wss://relay.example.com"),
    ],
)
def test_normalize_relay_url(raw: str, expected: str) -> None:
    assert normalize_relay_url(raw) == expected


@pytest.mark.parametrize(
    ("bad", "match"),
    [
        ("", "cannot be empty"),
        ("   ", "cannot be empty"),
        ("ftp://relay.example.com", "Unsupported relay URL scheme"),
    ],
)
def test_normalize_relay_url_rejects_bad_input(bad: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        normalize_relay_url(bad)


def test_default_relay_url_is_secure() -> None:
    """The baked-in default must be TLS; the relay carries real traffic."""
    assert DEFAULT_RELAY_URL.startswith("wss://")


def test_resolve_returns_none_when_nothing_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returning a default here would clobber the per-component env vars.

    Regression test: an earlier version returned DEFAULT_RELAY_URL, which the
    caller then assigned unconditionally, silently overriding
    WAYPORT_CLIENT_RELAY_URL.
    """
    monkeypatch.delenv(RELAY_URL_ENV_VAR, raising=False)
    assert resolve_relay_url(None) is None


def test_resolve_prefers_cli_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(RELAY_URL_ENV_VAR, "ws://from-env:8080")
    assert resolve_relay_url("ws://from-cli:9999") == "ws://from-cli:9999"


def test_resolve_uses_env_when_no_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(RELAY_URL_ENV_VAR, "https://from-env.example.com")
    assert resolve_relay_url(None) == "wss://from-env.example.com"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("wss://relay.example.com", "relay.example.com"),
        ("ws://localhost:8080", "localhost:8080"),
        ("https://relay.example.com/path", "relay.example.com"),
    ],
)
def test_relay_host(url: str, expected: str) -> None:
    assert relay_host(url) == expected
