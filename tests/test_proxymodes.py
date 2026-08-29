"""Tests for scoped (browser and shell) proxying."""

from __future__ import annotations

from pathlib import Path

import pytest

from wayport.common.proxymodes import chrome_command, proxy_environment


def test_chrome_uses_socks5_so_dns_is_resolved_remotely(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """socks5:// (not socks://) makes Chrome resolve names through the proxy."""
    monkeypatch.setenv("WAYPORT_CONFIG_DIR", str(tmp_path))
    command = chrome_command("/bin/chrome", 1080, "127.0.0.1", None, ())
    assert "--proxy-server=socks5://127.0.0.1:1080" in command


def test_chrome_uses_an_isolated_profile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The user's real browser session must be left alone."""
    monkeypatch.setenv("WAYPORT_CONFIG_DIR", str(tmp_path))
    command = chrome_command("/bin/chrome", 1080, "127.0.0.1", None, ())
    profile = next(a for a in command if a.startswith("--user-data-dir="))
    assert str(tmp_path) in profile


def test_chrome_bypasses_the_relay(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The relay connection must never be routed through the tunnel it carries."""
    monkeypatch.setenv("WAYPORT_CONFIG_DIR", str(tmp_path))
    command = chrome_command("/bin/chrome", 1080, "127.0.0.1", None, ("relay.example.com",))
    bypass = next(a for a in command if a.startswith("--proxy-bypass-list="))
    assert "relay.example.com" in bypass


def test_shell_environment_uses_socks5h() -> None:
    """socks5h resolves hostnames at the far end, avoiding a DNS leak."""
    env = proxy_environment(1080)
    assert env["ALL_PROXY"] == "socks5h://127.0.0.1:1080"
    assert env["HTTPS_PROXY"] == env["ALL_PROXY"]


def test_shell_environment_sets_both_cases() -> None:
    """Tools disagree about capitalisation, so set both."""
    env = proxy_environment(1080)
    for name in ("all_proxy", "http_proxy", "https_proxy", "no_proxy"):
        assert name in env
        assert name.upper() in env


def test_shell_environment_excludes_loopback_and_relay() -> None:
    env = proxy_environment(1080, bypass=("relay.example.com",))
    assert "127.0.0.1" in env["NO_PROXY"]
    assert "relay.example.com" in env["NO_PROXY"]
