"""Choosing an outbound proxy for the relay connection.

Both ends dial the relay over HTTPS/WebSocket. On a corporate network that
often has to go through an HTTP proxy, which browsers pick up from the system
settings and Python does not unless told.

The complication is that Wayport itself runs a local SOCKS proxy, and in
system-wide mode points the machine's proxy settings at it. Sending the relay
connection through that would make the tunnel carry itself. So proxies are
honoured, except one that points back at us.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from wayport.common.defaults import relay_host
from wayport.common.logging import get_logger

logger = get_logger(__name__)

# Checked in order; the first one set wins.
PROXY_VARS = ("https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY")
NO_PROXY_VARS = ("no_proxy", "NO_PROXY")


def _no_proxy_matches(host: str) -> bool:
    """True if NO_PROXY says this host should be reached directly."""
    for var in NO_PROXY_VARS:
        raw = os.environ.get(var)
        if not raw:
            continue
        for entry in raw.split(","):
            rule = entry.strip().lstrip(".").lower()
            if not rule:
                continue
            if rule == "*" or host == rule or host.endswith("." + rule):
                return True
    return False


def relay_proxy(
    relay_url: str,
    local_proxy: tuple[str, int] | None = None,
) -> str | None:
    """Return the proxy URL to use when dialling the relay, or None for direct.

    Args:
        relay_url: The relay's ws:// or wss:// URL.
        local_proxy: This process's own (host, port) SOCKS proxy, if it is
            running one. A proxy setting pointing there is ignored, because
            routing the relay connection through our own tunnel would make it
            carry itself.
    """
    host = relay_host(relay_url).split(":")[0]
    if _no_proxy_matches(host):
        return None

    for var in PROXY_VARS:
        value = os.environ.get(var)
        if not value:
            continue

        parsed = urlparse(value if "://" in value else f"http://{value}")

        # aiohttp can only use an HTTP proxy for this. Routing via SOCKS would
        # need an extra dependency; say so rather than failing obscurely.
        if parsed.scheme not in ("http", "https"):
            logger.warning(
                "Ignoring unsupported proxy scheme for the relay connection",
                variable=var,
                scheme=parsed.scheme,
            )
            continue

        if local_proxy and _points_at_us(parsed.hostname, parsed.port, local_proxy):
            logger.debug("Ignoring proxy that points at our own tunnel", variable=var)
            continue

        logger.debug("Using proxy for the relay connection", variable=var, proxy=value)
        return value

    return None


def _points_at_us(host: str | None, port: int | None, local_proxy: tuple[str, int]) -> bool:
    """True if a proxy setting refers to this process's own SOCKS proxy."""
    if port != local_proxy[1]:
        return False
    return (host or "").lower() in ("localhost", "127.0.0.1", "::1", local_proxy[0].lower())
