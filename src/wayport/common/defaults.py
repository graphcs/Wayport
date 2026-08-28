"""Built-in defaults and URL resolution for the hosted relay.

The relay URL is resolved from, in order of precedence:

1. an explicit CLI argument (``--relay-url``)
2. the ``WAYPORT_RELAY_URL`` environment variable
3. :data:`DEFAULT_RELAY_URL`, the relay we host

Keeping the default here (rather than in an argparse ``default=``) is what lets
the pydantic settings layer see environment variables at all; see
``wayport.__main__`` for why passing argparse defaults directly breaks that.
"""

from __future__ import annotations

import os

# The hosted relay. Both machines reach this by default, so neither side needs
# to pass --relay-url. Override with WAYPORT_RELAY_URL when testing locally.
DEFAULT_RELAY_URL = "wss://wayport-relay.up.railway.app"

RELAY_URL_ENV_VAR = "WAYPORT_RELAY_URL"
RELAY_TOKEN_ENV_VAR = "WAYPORT_RELAY_TOKEN"

# WebSocket-level keepalive, used by relay, client and exit node alike.
# Makes heartbeat_timeout_seconds real by closing half-open connections, and
# keeps PaaS edge proxies from reaping an idle tunnel.
WS_HEARTBEAT_SECONDS = 30.0


def normalize_relay_url(url: str) -> str:
    """Normalize a relay URL into a WebSocket URL.

    Accepts what a person is likely to paste -- a Railway dashboard URL
    (``https://...``), a bare hostname, or an already-correct ``wss://`` URL --
    and returns a URL aiohttp can open. Without this, pasting the ``https://``
    URL Railway shows you fails with an opaque aiohttp error.

    Args:
        url: A relay URL or hostname.

    Returns:
        The URL with a ``ws://`` or ``wss://`` scheme and no trailing slash.

    Raises:
        ValueError: If ``url`` is empty or has an unsupported scheme.
    """
    cleaned = url.strip().rstrip("/")
    if not cleaned:
        raise ValueError("Relay URL cannot be empty")

    if "://" not in cleaned:
        # A bare host. Assume TLS -- a hosted relay should always be wss://,
        # and a local one is spelled out explicitly.
        return f"wss://{cleaned}"

    scheme, _, rest = cleaned.partition("://")
    scheme = scheme.lower()
    if scheme in ("ws", "wss"):
        return f"{scheme}://{rest}"
    if scheme == "http":
        return f"ws://{rest}"
    if scheme == "https":
        return f"wss://{rest}"

    raise ValueError(
        f"Unsupported relay URL scheme {scheme!r}. Use wss://, ws://, https:// or http://."
    )


def resolve_relay_url(cli_value: str | None = None) -> str | None:
    """Resolve an explicitly-requested relay URL, if there is one.

    Returns ``None`` when neither ``--relay-url`` nor ``WAYPORT_RELAY_URL`` was
    given, so the caller can leave the field unset and let the settings layer
    apply its own per-component environment variable
    (``WAYPORT_CLIENT_RELAY_URL`` / ``WAYPORT_EXITNODE_RELAY_URL``) or
    :data:`DEFAULT_RELAY_URL`.

    Returning the default here instead would clobber those component-specific
    variables -- the very bug this indirection exists to avoid.

    Args:
        cli_value: The value of ``--relay-url``, or ``None`` if not passed.

    Returns:
        A normalized WebSocket URL, or ``None`` if nothing explicit was set.
    """
    explicit = cli_value or os.environ.get(RELAY_URL_ENV_VAR)
    return normalize_relay_url(explicit) if explicit else None


def relay_host(url: str) -> str:
    """Return the host[:port] portion of a relay URL.

    Used to build proxy bypass lists so a system-wide proxy can never loop the
    relay connection back through the tunnel it is carrying.
    """
    _, _, rest = normalize_relay_url(url).partition("://")
    return rest.split("/", 1)[0]
