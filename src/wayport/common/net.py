"""Small networking helpers."""

from __future__ import annotations

import socket

DEFAULT_PROXY_PORT = 1080


def port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    """Return True if a TCP port can be bound right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
        return True


def find_free_port(preferred: int = DEFAULT_PROXY_PORT, host: str = "127.0.0.1") -> int:
    """Return ``preferred`` if free, else the next free port above it."""
    for candidate in range(preferred, preferred + 100):
        if port_is_free(candidate, host):
            return candidate
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        chosen: int = sock.getsockname()[1]
        return chosen


def resolve_proxy_port(value: str | int | None) -> int | None:
    """Interpret ``--proxy-port``, which accepts a number or ``auto``.

    Returns None when unset so the settings layer keeps control.
    """
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() == "auto":
        return find_free_port()
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid proxy port {value!r}; use a number or 'auto'") from exc
