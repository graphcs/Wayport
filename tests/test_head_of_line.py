"""The tunnel must not stall when one destination is slow.

Frames for every stream share a single processing loop. If that loop awaits
connection establishment, one unreachable destination blocks every other
stream for the whole connect timeout -- which is what made a browser session
appear to hang after a while.
"""

from __future__ import annotations

import asyncio

import pytest

from wayport.common.protocol import (
    Frame,
    FrameType,
    Socks5AddressType,
    Socks5Reply,
    StreamOpenRequest,
)
from wayport.exitnode.socks import SocksHandler

SLOW_STREAM = 1
FAST_STREAM = 2


def open_frame(stream_id: int, host: str, port: int) -> Frame:
    request = StreamOpenRequest(Socks5AddressType.DOMAIN, host, port)
    return Frame(FrameType.OPEN, stream_id, request.encode())


async def wait_for_reply(sent: list[Frame], stream_id: int, timeout: float = 5.0) -> Frame:
    async with asyncio.timeout(timeout):
        while True:
            for frame in sent:
                if frame.stream_id == stream_id and frame.frame_type == FrameType.OPEN:
                    return frame
            await asyncio.sleep(0.01)


async def test_a_slow_destination_does_not_block_other_streams() -> None:
    """This is the regression: a hanging connect used to stall the tunnel."""
    sent: list[Frame] = []
    handler = SocksHandler(on_send_frame=sent.append)

    # A real listener the fast stream can reach.
    server = await asyncio.start_server(lambda _r, _w: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    original = asyncio.open_connection

    async def slow_for_one_host(host=None, port=None, **kwargs):  # type: ignore[no-untyped-def]
        if host == "blackhole.invalid":
            await asyncio.sleep(30)  # never completes within the test
        return await original(host, port, **kwargs)

    asyncio.open_connection = slow_for_one_host  # type: ignore[assignment]
    try:
        # The slow one first, exactly as head-of-line blocking would need.
        await handler.handle_frame(open_frame(SLOW_STREAM, "blackhole.invalid", 80))
        await handler.handle_frame(open_frame(FAST_STREAM, "127.0.0.1", port))

        reply = await wait_for_reply(sent, FAST_STREAM)
        assert reply.payload[0] == Socks5Reply.SUCCEEDED
        # The slow stream is still pending, which is the point.
        assert not any(f.stream_id == SLOW_STREAM for f in sent)
    finally:
        asyncio.open_connection = original  # type: ignore[assignment]
        server.close()
        await server.wait_closed()
        await handler.close_all()


async def test_data_arriving_before_the_open_completes_is_not_lost() -> None:
    """Opens now run concurrently, so DATA can overtake its own OPEN."""
    sent: list[Frame] = []
    handler = SocksHandler(on_send_frame=sent.append)

    received: list[bytes] = []
    ready = asyncio.Event()

    async def echo(reader: asyncio.StreamReader, _writer: asyncio.StreamWriter) -> None:
        received.append(await reader.read(64))
        ready.set()

    server = await asyncio.start_server(echo, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    try:
        await handler.handle_frame(open_frame(FAST_STREAM, "127.0.0.1", port))
        # Sent immediately, likely before the connection is established.
        await handler.handle_frame(Frame(FrameType.DATA, FAST_STREAM, b"hello"))

        await asyncio.wait_for(ready.wait(), timeout=5)
        assert received == [b"hello"]
    finally:
        server.close()
        await server.wait_closed()
        await handler.close_all()


async def test_close_before_open_completes_is_handled() -> None:
    """A client that gives up mid-connect must not leave a stream behind."""
    sent: list[Frame] = []
    handler = SocksHandler(on_send_frame=sent.append)

    server = await asyncio.start_server(lambda _r, _w: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    try:
        await handler.handle_frame(open_frame(FAST_STREAM, "127.0.0.1", port))
        await handler.handle_frame(Frame(FrameType.CLOSE, FAST_STREAM, b""))
        await asyncio.sleep(0.5)
        assert FAST_STREAM not in handler._streams
    finally:
        server.close()
        await server.wait_closed()
        await handler.close_all()


@pytest.mark.parametrize("count", [1, 8])
async def test_many_opens_are_served_concurrently(count: int) -> None:
    sent: list[Frame] = []
    handler = SocksHandler(on_send_frame=sent.append)
    server = await asyncio.start_server(lambda _r, _w: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    try:
        for stream_id in range(10, 10 + count):
            await handler.handle_frame(open_frame(stream_id, "127.0.0.1", port))
        for stream_id in range(10, 10 + count):
            reply = await wait_for_reply(sent, stream_id)
            assert reply.payload[0] == Socks5Reply.SUCCEEDED
    finally:
        server.close()
        await server.wait_closed()
        await handler.close_all()
