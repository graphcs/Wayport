"""Prove the tunnel actually carries traffic.

"Connected" only means two processes agree a WebSocket is open. The useful
question is whether an ordinary request now leaves from the other machine, so
we make one through our own SOCKS proxy and report the address it came from.
"""

from __future__ import annotations

import asyncio
import contextlib

from wayport.common.logging import get_logger

logger = get_logger(__name__)

# Plain-HTTP, tiny response, no TLS handshake to slow the check down.
CHECK_HOST = "ifconfig.me"
CHECK_PATH = "/ip"


async def exit_ip_via_proxy(
    proxy_host: str,
    proxy_port: int,
    timeout: float = 15.0,
) -> str | None:
    """Fetch our apparent public IP through the local SOCKS5 proxy.

    Speaks SOCKS5 directly rather than adding a dependency, and returns None on
    any failure -- this is a nicety, never a reason to fail a connection.
    """
    try:
        return await asyncio.wait_for(
            _fetch(proxy_host, proxy_port),
            timeout=timeout,
        )
    except (TimeoutError, OSError, ValueError) as exc:
        logger.debug("Exit IP check failed", error=str(exc))
        return None


async def _fetch(proxy_host: str, proxy_port: int) -> str | None:
    reader, writer = await asyncio.open_connection(proxy_host, proxy_port)
    try:
        # SOCKS5 greeting, no authentication.
        writer.write(bytes([0x05, 0x01, 0x00]))
        await writer.drain()
        if await reader.readexactly(2) != bytes([0x05, 0x00]):
            return None

        # CONNECT to the check host by name, so the far end resolves it.
        host = CHECK_HOST.encode()
        writer.write(bytes([0x05, 0x01, 0x00, 0x03, len(host)]) + host + (80).to_bytes(2, "big"))
        await writer.drain()

        reply = await reader.readexactly(4)
        if reply[1] != 0x00:
            return None
        # Consume the bound address so the stream is positioned correctly.
        if reply[3] == 0x01:
            await reader.readexactly(4)
        elif reply[3] == 0x03:
            await reader.readexactly((await reader.readexactly(1))[0])
        elif reply[3] == 0x04:
            await reader.readexactly(16)
        await reader.readexactly(2)

        writer.write(
            f"GET {CHECK_PATH} HTTP/1.1\r\nHost: {CHECK_HOST}\r\n"
            "User-Agent: wayport\r\nConnection: close\r\n\r\n".encode()
        )
        await writer.drain()

        body = await reader.read(4096)
        _, _, payload = body.partition(b"\r\n\r\n")
        text = payload.decode("utf-8", "replace").strip()
        return text or None
    finally:
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
