"""Tests for port selection."""

from __future__ import annotations

import socket

import pytest

from wayport.common.net import find_free_port, port_is_free, resolve_proxy_port


def test_port_is_free_detects_a_bound_port() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        taken = sock.getsockname()[1]
        assert port_is_free(taken) is False
    assert port_is_free(taken) is True


def test_find_free_port_skips_a_taken_one() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        taken = sock.getsockname()[1]
        assert find_free_port(taken) != taken


def test_resolve_proxy_port_passthrough() -> None:
    assert resolve_proxy_port(1080) == 1080
    assert resolve_proxy_port("1080") == 1080


def test_resolve_proxy_port_none_stays_none() -> None:
    """None must not become a default, or it would clobber the config file."""
    assert resolve_proxy_port(None) is None


def test_resolve_proxy_port_auto_picks_something_free() -> None:
    port = resolve_proxy_port("auto")
    assert isinstance(port, int)
    assert port_is_free(port)


def test_resolve_proxy_port_rejects_nonsense() -> None:
    with pytest.raises(ValueError, match="invalid proxy port"):
        resolve_proxy_port("banana")
