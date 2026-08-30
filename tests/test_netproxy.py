"""Choosing an outbound proxy for the relay connection."""

from __future__ import annotations

import pytest

from wayport.common.netproxy import relay_proxy

RELAY = "wss://relay.example.com"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY", "no_proxy", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)


def test_no_proxy_configured_means_direct() -> None:
    assert relay_proxy(RELAY) is None


@pytest.mark.parametrize("var", ["HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"])
def test_proxy_is_honoured(monkeypatch: pytest.MonkeyPatch, var: str) -> None:
    """Corporate networks set these, and browsers honour them.

    Regression: the relay connection used trust_env=False, so a machine where
    the browser could reach the relay could still fail to connect.
    """
    monkeypatch.setenv(var, "http://corp-proxy:8080")
    assert relay_proxy(RELAY) == "http://corp-proxy:8080"


def test_https_proxy_wins_over_all_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://specific:8080")
    monkeypatch.setenv("ALL_PROXY", "http://general:8080")
    assert relay_proxy(RELAY) == "http://specific:8080"


def test_no_proxy_exempts_the_relay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://corp-proxy:8080")
    monkeypatch.setenv("NO_PROXY", "relay.example.com")
    assert relay_proxy(RELAY) is None


def test_no_proxy_wildcard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://corp-proxy:8080")
    monkeypatch.setenv("NO_PROXY", "*")
    assert relay_proxy(RELAY) is None


def test_no_proxy_matches_a_parent_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://corp-proxy:8080")
    monkeypatch.setenv("NO_PROXY", ".example.com")
    assert relay_proxy(RELAY) is None


def test_socks_proxy_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """aiohttp cannot use a SOCKS proxy without an extra dependency."""
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:9050")
    assert relay_proxy(RELAY) is None


def test_a_proxy_pointing_at_our_own_tunnel_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """System-wide mode points the machine at us; the relay must not loop."""
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1080")
    assert relay_proxy(RELAY, local_proxy=("127.0.0.1", 1080)) is None


def test_a_different_local_proxy_is_still_used(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only our own port is excluded, not every loopback proxy."""
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:3128")
    assert relay_proxy(RELAY, local_proxy=("127.0.0.1", 1080)) == "http://127.0.0.1:3128"


def test_bare_host_proxy_is_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "corp-proxy:8080")
    assert relay_proxy(RELAY) == "corp-proxy:8080"
