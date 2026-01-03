"""Exit node server that orchestrates the tunnel and SOCKS handler."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from wayport.common.config import ExitNodeSettings
from wayport.common.logging import get_logger, setup_logging
from wayport.common.protocol import Frame
from wayport.exitnode.socks import SocksHandler
from wayport.exitnode.tunnel import ExitNodeTunnel

if TYPE_CHECKING:
    from collections.abc import Callable

logger = get_logger(__name__)


class ExitNodeServer:
    """Exit node server that shares internet via relay."""

    def __init__(
        self,
        settings: ExitNodeSettings | None = None,
        on_code_received: Callable[[str, str], None] | None = None,
        on_client_connected: Callable[[str], None] | None = None,
        on_client_disconnected: Callable[[str], None] | None = None,
        on_connection_status: Callable[[str], None] | None = None,
    ) -> None:
        """Initialize the exit node server.

        Args:
            settings: Server settings
            on_code_received: Callback when registration code is received
            on_client_connected: Callback when a client connects
            on_client_disconnected: Callback when a client disconnects
            on_connection_status: Callback for connection status updates
        """
        self.settings = settings or ExitNodeSettings()

        # External callbacks
        self._on_code_received = on_code_received
        self._on_client_connected = on_client_connected
        self._on_client_disconnected = on_client_disconnected
        self._on_connection_status = on_connection_status

        # Internal state
        self._current_code: str | None = None
        self._socks_handler: SocksHandler | None = None
        self._tunnel: ExitNodeTunnel | None = None
        self._send_queue: asyncio.Queue[Frame] = asyncio.Queue()
        self._recv_queue: asyncio.Queue[Frame] = asyncio.Queue()
        self._send_task: asyncio.Task[None] | None = None
        self._recv_task: asyncio.Task[None] | None = None

    @property
    def current_code(self) -> str | None:
        """Get the current connection code."""
        return self._current_code

    @property
    def is_connected(self) -> bool:
        """Check if connected to relay."""
        return self._tunnel is not None and self._tunnel.is_connected

    @property
    def has_client(self) -> bool:
        """Check if a client is currently connected."""
        return self._tunnel is not None and self._tunnel.current_tunnel_id is not None

    async def start(self) -> None:
        """Start the exit node server."""
        setup_logging(level=self.settings.log_level)

        logger.info("Starting exit node server", device_name=self.settings.device_name)

        # Create SOCKS handler
        self._socks_handler = SocksHandler(on_send_frame=self._queue_frame)

        # Create tunnel
        self._tunnel = ExitNodeTunnel(
            settings=self.settings,
            on_code_received=self._handle_code_received,
            on_client_connected=self._handle_client_connected,
            on_client_disconnected=self._handle_client_disconnected,
            on_connection_status=self._handle_connection_status,
            on_data_received=self._handle_data_received,
        )

        # Start send and receive tasks
        self._send_task = asyncio.create_task(self._send_loop())
        self._recv_task = asyncio.create_task(self._recv_loop())

        # Start tunnel (this blocks until stopped)
        try:
            await self._tunnel.start()
        finally:
            if self._send_task:
                self._send_task.cancel()
            if self._recv_task:
                self._recv_task.cancel()
            if self._socks_handler:
                await self._socks_handler.close_all()

    async def stop(self) -> None:
        """Stop the exit node server."""
        if self._tunnel:
            await self._tunnel.stop()

    def _queue_frame(self, frame: Frame) -> None:
        """Queue a frame to be sent through the tunnel.

        Args:
            frame: The frame to send
        """
        try:
            self._send_queue.put_nowait(frame)
        except asyncio.QueueFull:
            logger.warning("Send queue full, dropping frame")

    async def _send_loop(self) -> None:
        """Send queued frames through the tunnel."""
        while True:
            try:
                frame = await self._send_queue.get()
                if self._tunnel and self._tunnel.is_connected:
                    await self._tunnel.send_data(frame)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error sending frame", error=str(e))

    async def _recv_loop(self) -> None:
        """Process received frames sequentially."""
        while True:
            try:
                frame = await self._recv_queue.get()
                if self._socks_handler:
                    await self._socks_handler.handle_frame(frame)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error processing frame", error=str(e))

    def _handle_code_received(self, code: str, expires_at: str) -> None:
        """Handle receiving the registration code.

        Args:
            code: The connection code
            expires_at: Expiration timestamp
        """
        self._current_code = code
        if self._on_code_received:
            self._on_code_received(code, expires_at)

    def _handle_client_connected(self, tunnel_id: str) -> None:
        """Handle a client connecting.

        Args:
            tunnel_id: The tunnel ID
        """
        logger.info("Client connected", tunnel_id=tunnel_id)
        if self._on_client_connected:
            self._on_client_connected(tunnel_id)

    def _handle_client_disconnected(self, reason: str) -> None:
        """Handle a client disconnecting.

        Args:
            reason: Disconnection reason
        """
        logger.info("Client disconnected", reason=reason)

        # Close all streams
        if self._socks_handler:
            asyncio.create_task(self._socks_handler.close_all())

        if self._on_client_disconnected:
            self._on_client_disconnected(reason)

    def _handle_connection_status(self, status: str) -> None:
        """Handle connection status change.

        Args:
            status: Status string
        """
        if status == "disconnected":
            self._current_code = None
        if self._on_connection_status:
            self._on_connection_status(status)

    def _handle_data_received(self, frame: Frame) -> None:
        """Handle binary data from the client.

        Args:
            frame: The received frame
        """
        try:
            self._recv_queue.put_nowait(frame)
        except asyncio.QueueFull:
            logger.warning("Receive queue full, dropping frame")


async def run_exit_node(settings: ExitNodeSettings | None = None) -> None:
    """Run the exit node server.

    Args:
        settings: Server settings
    """
    server = ExitNodeServer(settings)
    await server.start()
