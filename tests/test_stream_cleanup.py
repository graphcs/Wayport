"""Sockets must be released when the peer goes away.

A stream whose peer closed but whose descriptor is never closed sits in
CLOSE_WAIT for the life of the process. Over a long session that leaks file
descriptors until the tunnel stops working.
"""

from __future__ import annotations

import asyncio

from wayport.common.protocol import (
    Frame,
    FrameType,
    Socks5AddressType,
    StreamOpenRequest,
)
from wayport.exitnode.socks import SocksHandler


async def test_stream_is_forgotten_when_the_destination_closes() -> None:
    sent: list[Frame] = []
    handler = SocksHandler(on_send_frame=sent.append)

    async def hang_up(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()  # close immediately, as a real server eventually will

    server = await asyncio.start_server(hang_up, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    try:
        request = StreamOpenRequest(Socks5AddressType.IPV4, "127.0.0.1", port)
        await handler.handle_frame(Frame(FrameType.OPEN, 1, request.encode()))

        # Opens are asynchronous now, so wait for the CLOSE rather than racing
        # the moment the stream appears in the table.
        async with asyncio.timeout(5):
            while not any(f.frame_type == FrameType.CLOSE and f.stream_id == 1 for f in sent):
                await asyncio.sleep(0.02)
        assert 1 not in handler._streams

        # And the tunnel was told, so the client can release its side too.
        assert any(f.frame_type == FrameType.CLOSE and f.stream_id == 1 for f in sent)
    finally:
        server.close()
        await server.wait_closed()
        await handler.close_all()


async def test_repeated_streams_do_not_accumulate() -> None:
    """The leak showed up as steady growth, so assert on the steady state."""
    sent: list[Frame] = []
    handler = SocksHandler(on_send_frame=sent.append)

    async def hang_up(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()

    server = await asyncio.start_server(hang_up, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    request = StreamOpenRequest(Socks5AddressType.IPV4, "127.0.0.1", port)

    try:
        for stream_id in range(1, 26):
            await handler.handle_frame(Frame(FrameType.OPEN, stream_id, request.encode()))

        async with asyncio.timeout(10):
            while len([f for f in sent if f.frame_type == FrameType.CLOSE]) < 25:
                await asyncio.sleep(0.05)

        assert handler._streams == {}
        # Nothing left half-opened either.
        assert handler._opening == {}
        assert handler._aborted == set()
    finally:
        server.close()
        await server.wait_closed()
        await handler.close_all()
