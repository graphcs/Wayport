"""Client that orchestrates the tunnel and local proxy."""

from __future__ import annotations

import asyncio
import sys
import time
from typing import TYPE_CHECKING, Callable

from wayport.common.config import ClientSettings
from wayport.common.logging import get_logger, setup_logging
from wayport.common.protocol import Frame
from wayport.client.local_proxy import LocalProxy
from wayport.client.tunnel import ClientTunnel

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


class ConnectionHealth:
    """Tracks connection health metrics."""

    def __init__(self) -> None:
        self.relay_connected = False
        self.tunnel_connected = False
        self.last_data_time: float | None = None
        self.reconnect_count = 0
        self.bytes_sent = 0
        self.bytes_received = 0
        self.peer_device_name: str | None = None

    def record_data_sent(self, size: int) -> None:
        self.bytes_sent += size
        self.last_data_time = time.time()

    def record_data_received(self, size: int) -> None:
        self.bytes_received += size
        self.last_data_time = time.time()

    def get_status_line(self) -> str:
        """Get a single-line status string."""
        if self.tunnel_connected:
            status = "CONNECTED"
            health = "[OK]"
        elif self.relay_connected:
            status = "RELAY OK"
            health = "[~]"
        else:
            status = "DISCONNECTED"
            health = "[!]"

        peer = self.peer_device_name or "---"
        sent_kb = self.bytes_sent / 1024
        recv_kb = self.bytes_received / 1024

        return f"{health} {status} | Peer: {peer} | Sent: {sent_kb:.1f}KB | Recv: {recv_kb:.1f}KB | Reconnects: {self.reconnect_count}"


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
        self._send_queue: asyncio.Queue[Frame] = asyncio.Queue(maxsize=10000)
        self._recv_queue: asyncio.Queue[Frame] = asyncio.Queue(maxsize=10000)
        self._pending_queue: list[Frame] = []  # Queue for graceful degradation
        self._send_task: asyncio.Task[None] | None = None
        self._recv_task: asyncio.Task[None] | None = None
        self._status_task: asyncio.Task[None] | None = None
        self._connected = False
        self._health = ConnectionHealth()

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

        print(f"\n=== Wayport Client ===")
        print(f"Relay: {self.settings.relay_url}")
        print(f"Code: {code.upper()}")
        print(f"Local proxy: {self.settings.proxy_host}:{self.settings.proxy_port}")
        print("=" * 30)
        print("Connecting...")

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

        # Start send, receive, and status tasks
        self._send_task = asyncio.create_task(self._send_loop())
        self._recv_task = asyncio.create_task(self._recv_loop())
        self._status_task = asyncio.create_task(self._status_loop())

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

        if self._status_task:
            self._status_task.cancel()
            try:
                await self._status_task
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
        """Send queued frames through the tunnel with graceful degradation."""
        while True:
            try:
                frame = await self._send_queue.get()

                if self._tunnel and self._tunnel.is_connected:
                    # First, send any pending frames from disconnection period
                    while self._pending_queue:
                        pending_frame = self._pending_queue.pop(0)
                        await self._tunnel.send_data(pending_frame)
                        self._health.record_data_sent(len(pending_frame.payload))

                    # Send current frame
                    await self._tunnel.send_data(frame)
                    self._health.record_data_sent(len(frame.payload))
                else:
                    # Queue for later (graceful degradation)
                    if len(self._pending_queue) < 1000:
                        self._pending_queue.append(frame)
                    else:
                        logger.warning("Pending queue full, dropping frame")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error sending frame", error=str(e))

    async def _recv_loop(self) -> None:
        """Process received frames sequentially."""
        while True:
            try:
                frame = await self._recv_queue.get()
                self._health.record_data_received(len(frame.payload))
                if self._proxy:
                    await self._proxy.handle_frame(frame)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error processing frame", error=str(e))

    async def _status_loop(self) -> None:
        """Periodically print status to console."""
        while True:
            try:
                await asyncio.sleep(5)
                status = self._health.get_status_line()
                sys.stdout.write(f"\r{status}    ")
                sys.stdout.flush()
            except asyncio.CancelledError:
                break
            except Exception:
                pass

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
        self._health.tunnel_connected = True
        self._health.peer_device_name = peer_device_name

        # Start local proxy
        if self._proxy:
            try:
                await self._proxy.start()
            except OSError as e:
                if "Address already in use" in str(e):
                    logger.warning("Proxy already running, continuing...")
                else:
                    raise

        print(f"\n\n[+] Connected to: {peer_device_name}")
        print(f"[+] Tunnel ID: {tunnel_id[:8]}...")
        print(f"\n*** SOCKS5 proxy available at {self.settings.proxy_host}:{self.settings.proxy_port} ***")
        print("Configure your browser to use this proxy.\n")

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
        self._health.tunnel_connected = False
        self._health.peer_device_name = None

        print(f"\n[-] Disconnected from exit node: {reason}")
        logger.info("Disconnected from exit node", reason=reason)

        if self._on_disconnected:
            self._on_disconnected(reason)

    def _handle_connection_status(self, status: str) -> None:
        """Handle connection status change.

        Args:
            status: Status string
        """
        if status == "connecting":
            print(f"\n[~] Connecting to relay...")
        elif status == "connected_to_relay":
            self._health.relay_connected = True
            self._health.reconnect_count += 1
            print(f"\n[+] Connected to relay, waiting for tunnel...")
        elif status == "tunnel_established":
            self._health.tunnel_connected = True
        elif status == "disconnected":
            self._health.relay_connected = False
            self._health.tunnel_connected = False
            print(f"\n[!] Connection lost, reconnecting...")

        if self._on_connection_status:
            self._on_connection_status(status)

    def _handle_error(self, error_code: str, error_message: str) -> None:
        """Handle an error.

        Args:
            error_code: Error code
            error_message: Error message
        """
        print(f"\n[ERROR] {error_code}: {error_message}")
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
