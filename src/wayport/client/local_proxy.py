"""Local SOCKS5 proxy server for the client.

Listens on localhost and forwards requests through the tunnel to the exit node.
"""

from __future__ import annotations

import asyncio
import struct
from typing import TYPE_CHECKING, Callable

from wayport.common.logging import get_logger
from wayport.common.protocol import (
    Frame,
    FrameType,
    Socks5AddressType,
    Socks5AuthMethod,
    Socks5Command,
    Socks5Reply,
    StreamOpenRequest,
)

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


class LocalProxyConnection:
    """Handles a single client connection to the local SOCKS5 proxy."""

    def __init__(
        self,
        stream_id: int,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        on_open_stream: Callable[[int, StreamOpenRequest], None],
        on_data: Callable[[Frame], None],
        on_close: Callable[[int], None],
    ) -> None:
        """Initialize the connection.

        Args:
            stream_id: Unique stream identifier
            reader: Async reader for the connection
            writer: Async writer for the connection
            on_open_stream: Callback to request opening a stream to destination
            on_data: Callback when data needs to be sent to exit node
            on_close: Callback when connection closes
        """
        self.stream_id = stream_id
        self.reader = reader
        self.writer = writer
        self.on_open_stream = on_open_stream
        self.on_data = on_data
        self.on_close = on_close

        self._handshake_complete = False
        self._connected = False
        self._read_task: asyncio.Task[None] | None = None
        self._dest_addr: str = ""
        self._dest_port: int = 0

    async def start(self) -> bool:
        """Start handling the connection.

        Returns:
            True if handshake succeeded, False otherwise
        """
        try:
            # SOCKS5 handshake
            if not await self._do_handshake():
                return False

            # Request opening the stream
            address_type = self._get_address_type(self._dest_addr)
            request = StreamOpenRequest(
                address_type=address_type,
                dest_addr=self._dest_addr,
                dest_port=self._dest_port,
            )
            self.on_open_stream(self.stream_id, request)

            return True

        except Exception as e:
            logger.debug("Connection error", stream_id=self.stream_id, error=str(e))
            return False

    async def handle_open_response(self, reply: Socks5Reply) -> None:
        """Handle the response to an open stream request.

        Args:
            reply: SOCKS5 reply code
        """
        if reply == Socks5Reply.SUCCEEDED:
            self._connected = True
            # Send success response to client
            await self._send_connect_response(reply)
            # Start reading from client
            self._read_task = asyncio.create_task(self._read_loop())
        else:
            # Send failure response and close
            await self._send_connect_response(reply)
            await self.close()

    async def handle_data(self, data: bytes) -> None:
        """Handle data received from the exit node.

        Args:
            data: Data to send to the local client
        """
        if self._connected:
            try:
                self.writer.write(data)
                await self.writer.drain()
            except Exception as e:
                logger.debug("Write error", stream_id=self.stream_id, error=str(e))
                await self.close()

    async def close(self) -> None:
        """Close the connection."""
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
        self.on_close(self.stream_id)

    async def _do_handshake(self) -> bool:
        """Perform SOCKS5 handshake.

        Returns:
            True if handshake succeeded, False otherwise
        """
        # Read greeting
        data = await self.reader.read(2)
        if len(data) < 2:
            return False

        version, nmethods = data[0], data[1]
        if version != 0x05:
            return False

        # Read methods
        methods = await self.reader.read(nmethods)
        if len(methods) < nmethods:
            return False

        # We only support no auth
        if Socks5AuthMethod.NO_AUTH not in methods:
            self.writer.write(bytes([0x05, Socks5AuthMethod.NO_ACCEPTABLE]))
            await self.writer.drain()
            return False

        # Send auth method selection
        self.writer.write(bytes([0x05, Socks5AuthMethod.NO_AUTH]))
        await self.writer.drain()

        # Read connect request
        data = await self.reader.read(4)
        if len(data) < 4:
            return False

        version, cmd, _, atyp = data[0], data[1], data[2], data[3]

        if version != 0x05:
            return False

        if cmd != Socks5Command.CONNECT:
            await self._send_connect_response(Socks5Reply.COMMAND_NOT_SUPPORTED)
            return False

        # Read destination address
        if atyp == Socks5AddressType.IPV4:
            addr_data = await self.reader.read(4)
            if len(addr_data) < 4:
                return False
            import socket
            self._dest_addr = socket.inet_ntoa(addr_data)
        elif atyp == Socks5AddressType.DOMAIN:
            length_data = await self.reader.read(1)
            if len(length_data) < 1:
                return False
            length = length_data[0]
            addr_data = await self.reader.read(length)
            if len(addr_data) < length:
                return False
            self._dest_addr = addr_data.decode("utf-8")
        elif atyp == Socks5AddressType.IPV6:
            addr_data = await self.reader.read(16)
            if len(addr_data) < 16:
                return False
            import socket
            self._dest_addr = socket.inet_ntop(socket.AF_INET6, addr_data)
        else:
            await self._send_connect_response(Socks5Reply.ADDRESS_TYPE_NOT_SUPPORTED)
            return False

        # Read port
        port_data = await self.reader.read(2)
        if len(port_data) < 2:
            return False
        self._dest_port = struct.unpack("!H", port_data)[0]

        self._handshake_complete = True
        logger.debug(
            "SOCKS5 request",
            stream_id=self.stream_id,
            dest=f"{self._dest_addr}:{self._dest_port}",
        )
        return True

    async def _send_connect_response(self, reply: Socks5Reply) -> None:
        """Send a SOCKS5 connect response.

        Args:
            reply: The reply code
        """
        # Response: VER REP RSV ATYP BND.ADDR BND.PORT
        # We use 0.0.0.0:0 as bound address since we're proxying
        response = bytes([
            0x05,  # Version
            reply,  # Reply
            0x00,  # Reserved
            Socks5AddressType.IPV4,  # Address type
            0, 0, 0, 0,  # Bound address (0.0.0.0)
            0, 0,  # Bound port (0)
        ])
        self.writer.write(response)
        await self.writer.drain()

    async def _read_loop(self) -> None:
        """Read data from the local client and send to exit node."""
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

    def _get_address_type(self, addr: str) -> Socks5AddressType:
        """Determine the address type.

        Args:
            addr: The address string

        Returns:
            The SOCKS5 address type
        """
        import socket

        try:
            socket.inet_aton(addr)
            return Socks5AddressType.IPV4
        except OSError:
            pass

        try:
            socket.inet_pton(socket.AF_INET6, addr)
            return Socks5AddressType.IPV6
        except OSError:
            pass

        return Socks5AddressType.DOMAIN


class LocalProxy:
    """Local SOCKS5 proxy server."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 1080,
        on_send_frame: Callable[[Frame], None] | None = None,
    ) -> None:
        """Initialize the proxy server.

        Args:
            host: Host to listen on
            port: Port to listen on
            on_send_frame: Callback to send frames through the tunnel
        """
        self.host = host
        self.port = port
        self.on_send_frame = on_send_frame

        self._server: asyncio.Server | None = None
        self._connections: dict[int, LocalProxyConnection] = {}
        self._closed_streams: set[int] = set()  # Track recently closed streams
        self._next_stream_id = 1
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Start the proxy server."""
        self._server = await asyncio.start_server(
            self._handle_connection,
            self.host,
            self.port,
        )
        logger.info("Local SOCKS5 proxy started", host=self.host, port=self.port)

    async def stop(self) -> None:
        """Stop the proxy server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()

        # Close all connections
        async with self._lock:
            for conn in list(self._connections.values()):
                await conn.close()
            self._connections.clear()
            self._closed_streams.clear()

    async def handle_frame(self, frame: Frame) -> None:
        """Handle a frame received from the exit node.

        Args:
            frame: The received frame
        """
        conn = self._connections.get(frame.stream_id)
        if not conn:
            # Only log if not a recently closed stream (avoids spurious warnings)
            if frame.stream_id not in self._closed_streams:
                logger.debug("Frame for unknown stream", stream_id=frame.stream_id)
            return

        if frame.frame_type == FrameType.OPEN:
            # Open response
            if frame.payload:
                reply = Socks5Reply(frame.payload[0])
                await conn.handle_open_response(reply)
            else:
                await conn.handle_open_response(Socks5Reply.GENERAL_FAILURE)

        elif frame.frame_type == FrameType.DATA:
            await conn.handle_data(frame.payload)

        elif frame.frame_type == FrameType.CLOSE:
            await conn.close()

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a new connection to the proxy.

        Args:
            reader: Stream reader
            writer: Stream writer
        """
        async with self._lock:
            stream_id = self._next_stream_id
            self._next_stream_id += 1

        conn = LocalProxyConnection(
            stream_id=stream_id,
            reader=reader,
            writer=writer,
            on_open_stream=self._on_open_stream,
            on_data=self._on_data,
            on_close=self._on_close,
        )

        async with self._lock:
            self._connections[stream_id] = conn

        if not await conn.start():
            async with self._lock:
                self._connections.pop(stream_id, None)
            await conn.close()

    def _on_open_stream(self, stream_id: int, request: StreamOpenRequest) -> None:
        """Handle request to open a stream.

        Args:
            stream_id: The stream ID
            request: The open request
        """
        frame = Frame(
            frame_type=FrameType.OPEN,
            stream_id=stream_id,
            payload=request.encode(),
        )
        if self.on_send_frame:
            self.on_send_frame(frame)

    def _on_data(self, frame: Frame) -> None:
        """Handle data from a connection.

        Args:
            frame: The frame to send
        """
        if self.on_send_frame:
            self.on_send_frame(frame)

    def _on_close(self, stream_id: int) -> None:
        """Handle connection close.

        Args:
            stream_id: The closed stream ID
        """
        self._connections.pop(stream_id, None)
        # Track as recently closed to avoid spurious warnings for in-flight frames
        self._closed_streams.add(stream_id)
        # Cleanup old entries (keep last 100)
        if len(self._closed_streams) > 100:
            oldest = min(self._closed_streams)
            self._closed_streams.discard(oldest)
