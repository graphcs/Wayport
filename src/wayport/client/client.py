"""Client that orchestrates the tunnel and local proxy."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Callable

from wayport.common.config import ClientSettings
from wayport.common.logging import get_logger, setup_logging
from wayport.common.protocol import Frame
from wayport.client.local_proxy import LocalProxy
from wayport.client.tunnel import ClientTunnel

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


class WayportClient:
    """Client that connects through an exit node."""

    def __init__(
        self,
        settings: ClientSettings | None = None,
        on_connected: Callable[[str, str], None] | None = None,
        on_disconnected: Callable[[str], None] | None = None,
        on_connection_status: Callable[[str], None] | None = None,
        on_error: Callable[[str, str], None] | None = None,
    ) -> None:
        """Initialize the client.

        Args:
            settings: Client settings
            on_connected: Callback when connected (tunnel_id, peer_device_name)
            on_disconnected: Callback when disconnected (reason)
            on_connection_status: Callback for status updates
            on_error: Callback for errors (error_code, error_message)
        """
        self.settings = settings or ClientSettings()

        # External callbacks
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected
        self._on_connection_status = on_connection_status
        self._on_error = on_error

        # Internal state
        self._tunnel: ClientTunnel | None = None
        self._proxy: LocalProxy | None = None
        self._send_queue: asyncio.Queue[Frame] = asyncio.Queue()
        self._recv_queue: asyncio.Queue[Frame] = asyncio.Queue()
        self._send_task: asyncio.Task[None] | None = None
        self._recv_task: asyncio.Task[None] | None = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        """Check if connected to an exit node."""
        return self._connected

    @property
    def proxy_address(self) -> tuple[str, int]:
        """Get the local proxy address."""
        return (self.settings.proxy_host, self.settings.proxy_port)

    async def connect(self, code: str) -> bool:
        """Connect to an exit node using a connection code.

        Args:
            code: The connection code

        Returns:
            True if connection succeeded, False otherwise
        """
        setup_logging(level=self.settings.log_level)

        logger.info("Connecting to exit node", code=code)

        # Create local proxy
        self._proxy = LocalProxy(
            host=self.settings.proxy_host,
            port=self.settings.proxy_port,
            on_send_frame=self._queue_frame,
        )

        # Create tunnel
        self._tunnel = ClientTunnel(
            settings=self.settings,
            on_connected=self._handle_connected,
            on_disconnected=self._handle_disconnected,
            on_connection_status=self._handle_connection_status,
            on_data_received=self._handle_data_received,
            on_error=self._handle_error,
        )

        # Start send and receive tasks
        self._send_task = asyncio.create_task(self._send_loop())
        self._recv_task = asyncio.create_task(self._recv_loop())

        # Start tunnel (with reconnect)
        try:
            await self._tunnel.start_with_reconnect(code)
        except asyncio.CancelledError:
            pass
        finally:
            await self._cleanup()

        return self._connected

    async def disconnect(self) -> None:
        """Disconnect from the exit node."""
        if self._tunnel:
            await self._tunnel.stop()

    async def _cleanup(self) -> None:
        """Clean up resources."""
        if self._send_task:
            self._send_task.cancel()
            try:
                await self._send_task
            except asyncio.CancelledError:
                pass

        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass

        if self._proxy:
            await self._proxy.stop()

        self._connected = False

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
                if self._proxy:
                    await self._proxy.handle_frame(frame)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error processing frame", error=str(e))

    def _handle_connected(self, tunnel_id: str, peer_device_name: str) -> None:
        """Handle successful connection to exit node.

        Args:
            tunnel_id: The tunnel ID
            peer_device_name: Name of the exit node device
        """
        asyncio.create_task(self._on_tunnel_connected(tunnel_id, peer_device_name))

    async def _on_tunnel_connected(self, tunnel_id: str, peer_device_name: str) -> None:
        """Async handler for tunnel connection."""
        self._connected = True

        # Start local proxy
        if self._proxy:
            await self._proxy.start()

        logger.info(
            "Connected to exit node",
            tunnel_id=tunnel_id,
            peer=peer_device_name,
            proxy=f"{self.settings.proxy_host}:{self.settings.proxy_port}",
        )

        if self._on_connected:
            self._on_connected(tunnel_id, peer_device_name)

    def _handle_disconnected(self, reason: str) -> None:
        """Handle disconnection from exit node.

        Args:
            reason: Disconnection reason
        """
        asyncio.create_task(self._on_tunnel_disconnected(reason))

    async def _on_tunnel_disconnected(self, reason: str) -> None:
        """Async handler for tunnel disconnection."""
        self._connected = False

        # Stop local proxy
        if self._proxy:
            await self._proxy.stop()

        logger.info("Disconnected from exit node", reason=reason)

        if self._on_disconnected:
            self._on_disconnected(reason)

    def _handle_connection_status(self, status: str) -> None:
        """Handle connection status change.

        Args:
            status: Status string
        """
        if self._on_connection_status:
            self._on_connection_status(status)

    def _handle_error(self, error_code: str, error_message: str) -> None:
        """Handle an error.

        Args:
            error_code: Error code
            error_message: Error message
        """
        logger.error("Error", code=error_code, message=error_message)
        if self._on_error:
            self._on_error(error_code, error_message)

    def _handle_data_received(self, frame: Frame) -> None:
        """Handle data received from the exit node.

        Args:
            frame: The received frame
        """
        try:
            self._recv_queue.put_nowait(frame)
        except asyncio.QueueFull:
            logger.warning("Receive queue full, dropping frame")


async def run_client(code: str, settings: ClientSettings | None = None) -> None:
    """Run the client.

    Args:
        code: Connection code
        settings: Client settings
    """
    client = WayportClient(settings)
    await client.connect(code)
