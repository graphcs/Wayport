"""Exit node server that orchestrates the tunnel and SOCKS handler."""

from __future__ import annotations

import asyncio
import sys
import time
from typing import TYPE_CHECKING

from wayport.common.config import ExitNodeSettings
from wayport.common.logging import get_logger, setup_logging
from wayport.common.protocol import Frame
from wayport.exitnode.socks import SocksHandler
from wayport.exitnode.tunnel import ExitNodeTunnel

if TYPE_CHECKING:
    from collections.abc import Callable

logger = get_logger(__name__)


class ConnectionHealth:
    """Tracks connection health metrics."""

    def __init__(self) -> None:
        self.connected = False
        self.client_connected = False
        self.last_ping_time: float | None = None
        self.last_data_time: float | None = None
        self.reconnect_count = 0
        self.bytes_sent = 0
        self.bytes_received = 0
        self.current_code: str | None = None

    def record_ping(self) -> None:
        self.last_ping_time = time.time()

    def record_data_sent(self, size: int) -> None:
        self.bytes_sent += size
        self.last_data_time = time.time()

    def record_data_received(self, size: int) -> None:
        self.bytes_received += size
        self.last_data_time = time.time()

    def get_status_line(self) -> str:
        """Get a single-line status string."""
        relay = "CONNECTED" if self.connected else "DISCONNECTED"
        client = "CLIENT" if self.client_connected else "WAITING"
        code = self.current_code or "------"

        # Calculate data rates
        sent_kb = self.bytes_sent / 1024
        recv_kb = self.bytes_received / 1024

        # Health indicator
        if not self.connected:
            health = "[!]"
        elif self.client_connected:
            health = "[OK]"
        else:
            health = "[~]"

        return f"{health} Code: {code} | Relay: {relay} | {client} | Sent: {sent_kb:.1f}KB | Recv: {recv_kb:.1f}KB"


class ExitNodeServer:
    """Exit node server that shares internet via relay."""

    def __init__(
        self,
        settings: ExitNodeSettings | None = None,
        preferred_code: str | None = None,
        on_code_received: Callable[[str, str], None] | None = None,
        on_client_connected: Callable[[str], None] | None = None,
        on_client_disconnected: Callable[[str], None] | None = None,
        on_connection_status: Callable[[str], None] | None = None,
    ) -> None:
        """Initialize the exit node server.

        Args:
            settings: Server settings
            preferred_code: Preferred connection code to request
            on_code_received: Callback when registration code is received
            on_client_connected: Callback when a client connects
            on_client_disconnected: Callback when a client disconnects
            on_connection_status: Callback for connection status updates
        """
        self.settings = settings or ExitNodeSettings()
        self.preferred_code = preferred_code

        # External callbacks
        self._on_code_received = on_code_received
        self._on_client_connected = on_client_connected
        self._on_client_disconnected = on_client_disconnected
        self._on_connection_status = on_connection_status

        # Internal state
        self._current_code: str | None = None
        self._socks_handler: SocksHandler | None = None
        self._tunnel: ExitNodeTunnel | None = None
        self._send_queue: asyncio.Queue[Frame] = asyncio.Queue(maxsize=10000)
        self._recv_queue: asyncio.Queue[Frame] = asyncio.Queue(maxsize=10000)
        self._pending_queue: list[Frame] = []  # Queue for graceful degradation
        self._send_task: asyncio.Task[None] | None = None
        self._recv_task: asyncio.Task[None] | None = None
        self._status_task: asyncio.Task[None] | None = None
        self._health = ConnectionHealth()

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

        print(f"\n=== Wayport Exit Node ===")
        print(f"Device: {self.settings.device_name}")
        print(f"Relay: {self.settings.relay_url}")
        if self.preferred_code:
            print(f"Preferred code: {self.preferred_code}")
        print("=" * 30)
        print("Connecting to relay...")

        # Create SOCKS handler
        self._socks_handler = SocksHandler(on_send_frame=self._queue_frame)

        # Create tunnel
        self._tunnel = ExitNodeTunnel(
            settings=self.settings,
            preferred_code=self.preferred_code,
            on_code_received=self._handle_code_received,
            on_client_connected=self._handle_client_connected,
            on_client_disconnected=self._handle_client_disconnected,
            on_connection_status=self._handle_connection_status,
            on_data_received=self._handle_data_received,
        )

        # Start send and receive tasks
        self._send_task = asyncio.create_task(self._send_loop())
        self._recv_task = asyncio.create_task(self._recv_loop())
        self._status_task = asyncio.create_task(self._status_loop())

        # Start tunnel (this blocks until stopped)
        try:
            await self._tunnel.start()
        finally:
            if self._send_task:
                self._send_task.cancel()
            if self._recv_task:
                self._recv_task.cancel()
            if self._status_task:
                self._status_task.cancel()
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
                if self._socks_handler:
                    await self._socks_handler.handle_frame(frame)
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
                # Use carriage return to update in place
                sys.stdout.write(f"\r{status}    ")
                sys.stdout.flush()
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    def _handle_code_received(self, code: str, expires_at: str) -> None:
        """Handle receiving the registration code.

        Args:
            code: The connection code
            expires_at: Expiration timestamp
        """
        self._current_code = code
        self._health.current_code = code
        print(f"\n\n*** CONNECTION CODE: {code} ***")
        print(f"Share this code with the client to connect")
        print("=" * 30 + "\n")
        if self._on_code_received:
            self._on_code_received(code, expires_at)

    def _handle_client_connected(self, tunnel_id: str) -> None:
        """Handle a client connecting.

        Args:
            tunnel_id: The tunnel ID
        """
        self._health.client_connected = True
        print(f"\n[+] Client connected (tunnel: {tunnel_id[:8]}...)")
        logger.info("Client connected", tunnel_id=tunnel_id)
        if self._on_client_connected:
            self._on_client_connected(tunnel_id)

    def _handle_client_disconnected(self, reason: str) -> None:
        """Handle a client disconnecting.

        Args:
            reason: Disconnection reason
        """
        self._health.client_connected = False
        print(f"\n[-] Client disconnected: {reason}")
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
        if status == "connected":
            self._health.connected = True
            self._health.reconnect_count += 1
            print(f"\n[+] Connected to relay")
        elif status == "disconnected":
            self._health.connected = False
            self._current_code = None
            self._health.current_code = None
            print(f"\n[!] Disconnected from relay, reconnecting...")
        elif status == "connecting":
            print(f"\n[~] Connecting to relay...")

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


async def run_exit_node(
    settings: ExitNodeSettings | None = None,
    preferred_code: str | None = None,
) -> None:
    """Run the exit node server.

    Args:
        settings: Server settings
        preferred_code: Preferred connection code
    """
    server = ExitNodeServer(settings, preferred_code=preferred_code)
    await server.start()
