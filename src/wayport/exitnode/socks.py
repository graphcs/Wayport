"""SOCKS5 protocol handler for the exit node.

Processes SOCKS5 requests received through the tunnel and makes
outbound connections to the internet.
"""

from __future__ import annotations

import asyncio
import socket
from typing import TYPE_CHECKING

from wayport.common.logging import get_logger
from wayport.common.protocol import (
    Frame,
    FrameType,
    Socks5AddressType,
    Socks5Reply,
    StreamOpenRequest,
)

if TYPE_CHECKING:
    from collections.abc import Callable

logger = get_logger(__name__)


class StreamConnection:
    """Represents an active stream connection to a destination."""

    def __init__(
        self,
        stream_id: int,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        on_data: Callable[[Frame], None],
        on_close: Callable[[int], None],
    ) -> None:
        """Initialize the stream connection.

        Args:
            stream_id: Unique stream identifier
            reader: Async reader for the connection
            writer: Async writer for the connection
            on_data: Callback when data is received from destination
            on_close: Callback when connection closes
        """
        self.stream_id = stream_id
        self.reader = reader
        self.writer = writer
        self.on_data = on_data
        self.on_close = on_close
        self._read_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start reading from the destination."""
        self._read_task = asyncio.create_task(self._read_loop())

    async def stop(self) -> None:
        """Stop the connection."""
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except Exception:
            pass

    async def write(self, data: bytes) -> None:
        """Write data to the destination.

        Args:
            data: Data to write
        """
        try:
            self.writer.write(data)
            await self.writer.drain()
        except Exception as e:
            logger.debug("Write error", stream_id=self.stream_id, error=str(e))
            await self.stop()
            self.on_close(self.stream_id)

    async def _read_loop(self) -> None:
        """Read data from the destination and send back through tunnel."""
        try:
            while True:
                data = await self.reader.read(65535)
                if not data:
                    break
                frame = Frame(
                    frame_type=FrameType.DATA,
                    stream_id=self.stream_id,
                    payload=data,
                )
                self.on_data(frame)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("Read error", stream_id=self.stream_id, error=str(e))
        finally:
            # Send close frame
            close_frame = Frame(
                frame_type=FrameType.CLOSE,
                stream_id=self.stream_id,
                payload=b"",
            )
            self.on_data(close_frame)
            self.on_close(self.stream_id)


class SocksHandler:
    """Handles SOCKS5 requests from the tunnel."""

    def __init__(
        self,
        on_send_frame: Callable[[Frame], None],
    ) -> None:
        """Initialize the SOCKS handler.

        Args:
            on_send_frame: Callback to send frames back through the tunnel
        """
        self.on_send_frame = on_send_frame
        self._streams: dict[int, StreamConnection] = {}
        self._lock = asyncio.Lock()

    async def handle_frame(self, frame: Frame) -> None:
        """Handle an incoming frame from the client.

        Args:
            frame: The received frame
        """
        if frame.frame_type == FrameType.OPEN:
            await self._handle_open(frame)
        elif frame.frame_type == FrameType.DATA:
            await self._handle_data(frame)
        elif frame.frame_type == FrameType.CLOSE:
            await self._handle_close(frame)

    async def close_all(self) -> None:
        """Close all active streams."""
        async with self._lock:
            for stream in list(self._streams.values()):
                await stream.stop()
            self._streams.clear()

    async def _handle_open(self, frame: Frame) -> None:
        """Handle a stream open request.

        Args:
            frame: The OPEN frame
        """
        stream_id = frame.stream_id

        try:
            # Parse the open request
            request = StreamOpenRequest.decode(frame.payload)
            logger.debug(
                "Opening stream",
                stream_id=stream_id,
                dest=f"{request.dest_addr}:{request.dest_port}",
            )

            # Connect to destination
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(request.dest_addr, request.dest_port),
                    timeout=30.0,
                )
            except asyncio.TimeoutError:
                logger.debug("Connection timeout", stream_id=stream_id)
                await self._send_open_response(stream_id, Socks5Reply.HOST_UNREACHABLE)
                return
            except OSError as e:
                logger.debug("Connection failed", stream_id=stream_id, error=str(e))
                reply = self._os_error_to_reply(e)
                await self._send_open_response(stream_id, reply)
                return

            # Create stream connection
            stream = StreamConnection(
                stream_id=stream_id,
                reader=reader,
                writer=writer,
                on_data=self._send_frame,
                on_close=self._on_stream_closed,
            )

            async with self._lock:
                self._streams[stream_id] = stream

            await stream.start()

            # Send success response
            await self._send_open_response(stream_id, Socks5Reply.SUCCEEDED)
            logger.debug("Stream opened", stream_id=stream_id)

        except Exception as e:
            logger.error("Error opening stream", stream_id=stream_id, error=str(e))
            await self._send_open_response(stream_id, Socks5Reply.GENERAL_FAILURE)

    async def _handle_data(self, frame: Frame) -> None:
        """Handle data for an existing stream.

        Args:
            frame: The DATA frame
        """
        stream = self._streams.get(frame.stream_id)
        if stream:
            await stream.write(frame.payload)
        else:
            logger.debug("Data for unknown stream", stream_id=frame.stream_id)

    async def _handle_close(self, frame: Frame) -> None:
        """Handle a stream close request.

        Args:
            frame: The CLOSE frame
        """
        stream_id = frame.stream_id
        async with self._lock:
            stream = self._streams.pop(stream_id, None)
        if stream:
            await stream.stop()
            logger.debug("Stream closed", stream_id=stream_id)

    async def _send_open_response(self, stream_id: int, reply: Socks5Reply) -> None:
        """Send an open response frame.

        Args:
            stream_id: The stream ID
            reply: SOCKS5 reply code
        """
        # Response payload is just the reply code
        frame = Frame(
            frame_type=FrameType.OPEN,
            stream_id=stream_id,
            payload=bytes([reply]),
        )
        self._send_frame(frame)

    def _send_frame(self, frame: Frame) -> None:
        """Send a frame through the tunnel.

        Args:
            frame: The frame to send
        """
        self.on_send_frame(frame)

    def _on_stream_closed(self, stream_id: int) -> None:
        """Handle a stream being closed.

        Args:
            stream_id: The closed stream ID
        """
        # Remove from tracking (may already be removed)
        self._streams.pop(stream_id, None)

    def _os_error_to_reply(self, error: OSError) -> Socks5Reply:
        """Convert an OSError to a SOCKS5 reply code.

        Args:
            error: The OS error

        Returns:
            Appropriate SOCKS5 reply code
        """
        import errno

        if error.errno == errno.ECONNREFUSED:
            return Socks5Reply.CONNECTION_REFUSED
        elif error.errno == errno.ENETUNREACH:
            return Socks5Reply.NETWORK_UNREACHABLE
        elif error.errno == errno.EHOSTUNREACH:
            return Socks5Reply.HOST_UNREACHABLE
        elif error.errno == errno.ETIMEDOUT:
            return Socks5Reply.HOST_UNREACHABLE
        else:
            return Socks5Reply.GENERAL_FAILURE
