"""Tests for the local SOCKS5 proxy handshake.

The handshake reads fixed-size fields off a stream socket. The important
property is that it must tolerate *fragmentation*: TCP gives no guarantee that
a peer's greeting arrives in one segment, and traffic arriving through a tunnel
is fragmented routinely.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from wayport.client.local_proxy import LocalProxyConnection
from wayport.common.protocol import (
    Socks5AddressType,
    Socks5AuthMethod,
    Socks5Command,
    Socks5Reply,
)

if TYPE_CHECKING:
    from wayport.common.protocol import Frame, StreamOpenRequest


class FakeWriter:
    """Minimal asyncio.StreamWriter stand-in that records what was written."""

    def __init__(self) -> None:
        self.written = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


def build_connection(
    reader: asyncio.StreamReader,
) -> tuple[LocalProxyConnection, FakeWriter, list[Any]]:
    """Build a connection wired to a fake writer, capturing stream-open calls."""
    writer = FakeWriter()
    opened: list[Any] = []

    def on_open_stream(stream_id: int, request: StreamOpenRequest) -> None:
        opened.append((stream_id, request))

    def on_data(frame: Frame) -> None:  # noqa: ARG001 - callback signature
        return None

    def on_close(stream_id: int) -> None:  # noqa: ARG001 - callback signature
        return None

    conn = LocalProxyConnection(
        stream_id=1,
        reader=reader,
        writer=writer,  # type: ignore[arg-type]
        on_open_stream=on_open_stream,
        on_data=on_data,
        on_close=on_close,
    )
    return conn, writer, opened


def greeting() -> bytes:
    """A SOCKS5 greeting offering only 'no authentication'."""
    return bytes([0x05, 0x01, Socks5AuthMethod.NO_AUTH])


def connect_request(host: str = "example.com", port: int = 443) -> bytes:
    """A SOCKS5 CONNECT request for a domain-name destination."""
    encoded = host.encode()
    return (
        bytes([0x05, Socks5Command.CONNECT, 0x00, Socks5AddressType.DOMAIN, len(encoded)])
        + encoded
        + port.to_bytes(2, "big")
    )


async def feed_all_at_once(reader: asyncio.StreamReader, payload: bytes) -> None:
    reader.feed_data(payload)


async def feed_one_byte_at_a_time(reader: asyncio.StreamReader, payload: bytes) -> None:
    """Deliver the payload in single-byte chunks, yielding between each.

    This is what a fragmented TCP stream looks like to the reader.
    """
    for i in range(len(payload)):
        reader.feed_data(payload[i : i + 1])
        await asyncio.sleep(0)


@pytest.mark.parametrize("feeder", [feed_all_at_once, feed_one_byte_at_a_time])
async def test_handshake_succeeds_regardless_of_fragmentation(feeder: Any) -> None:
    """The handshake must work whether or not the bytes arrive together.

    Regression test: the handshake used reader.read(n), which returns *up to*
    n bytes. A greeting split across segments therefore failed, which shows up
    as intermittent, load-dependent connection failures through the tunnel.
    """
    reader = asyncio.StreamReader()
    conn, writer, _opened = build_connection(reader)

    payload = greeting() + connect_request()
    handshake = asyncio.create_task(conn._do_handshake())
    await feeder(reader, payload)

    ok = await asyncio.wait_for(handshake, timeout=5)

    assert ok is True
    # Server selected "no auth".
    assert writer.written[:2] == bytes([0x05, Socks5AuthMethod.NO_AUTH])
    # And the destination was parsed correctly.
    assert conn._dest_addr == "example.com"
    assert conn._dest_port == 443


async def test_rejects_non_socks5_version() -> None:
    reader = asyncio.StreamReader()
    conn, _writer, _opened = build_connection(reader)
    reader.feed_data(bytes([0x04, 0x01, 0x00]))
    assert await asyncio.wait_for(conn._do_handshake(), timeout=5) is False


async def test_rejects_when_no_acceptable_auth_method() -> None:
    """A client offering only username/password (0x02) must be refused."""
    reader = asyncio.StreamReader()
    conn, writer, _opened = build_connection(reader)
    reader.feed_data(bytes([0x05, 0x01, 0x02]))

    assert await asyncio.wait_for(conn._do_handshake(), timeout=5) is False
    assert writer.written[:2] == bytes([0x05, Socks5AuthMethod.NO_ACCEPTABLE])


async def test_rejects_unsupported_command() -> None:
    """Only CONNECT is supported; BIND must be refused explicitly."""
    reader = asyncio.StreamReader()
    conn, writer, _opened = build_connection(reader)
    reader.feed_data(greeting() + bytes([0x05, Socks5Command.BIND, 0x00, Socks5AddressType.IPV4]))

    assert await asyncio.wait_for(conn._do_handshake(), timeout=5) is False
    # The auth selection (2 bytes) precedes the reply, whose byte 1 is the code.
    assert writer.written[3] == Socks5Reply.COMMAND_NOT_SUPPORTED


async def test_parses_ipv4_destination() -> None:
    reader = asyncio.StreamReader()
    conn, _writer, _opened = build_connection(reader)
    request = (
        bytes([0x05, Socks5Command.CONNECT, 0x00, Socks5AddressType.IPV4])
        + bytes([93, 184, 216, 34])
        + (80).to_bytes(2, "big")
    )
    reader.feed_data(greeting() + request)

    assert await asyncio.wait_for(conn._do_handshake(), timeout=5) is True
    assert conn._dest_addr == "93.184.216.34"
    assert conn._dest_port == 80


async def test_truncated_stream_does_not_hang() -> None:
    """A client that disconnects mid-handshake must not wedge the connection."""
    reader = asyncio.StreamReader()
    conn, _writer, _opened = build_connection(reader)
    reader.feed_data(bytes([0x05]))  # promises a greeting, then vanishes
    reader.feed_eof()

    assert await asyncio.wait_for(conn._do_handshake(), timeout=5) is False
