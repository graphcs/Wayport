"""Client that orchestrates the tunnel and local proxy."""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from wayport.client.local_proxy import LocalProxy
from wayport.client.tunnel import ClientTunnel
from wayport.common.config import ClientSettings
from wayport.common.crypto import decrypt, derive_key, encrypt
from wayport.common.logging import get_logger
from wayport.common.protocol import Frame
from wayport.common.ui import ui

if TYPE_CHECKING:
    from collections.abc import Coroutine

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

        # Proxy scoping: set up once the tunnel is live.
        self.mode = "none"
        self.verify = True
        self.system_proxy: Any = None
        self._browser: Any = None
        self._key_task: asyncio.Task[None] | None = None
        self._background: set[asyncio.Task[None]] = set()

        # Encryption
        self._encryption_key: bytes | None = None
        if self.settings.secret:
            self._encryption_key = derive_key(self.settings.secret)

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
        ui.banner("Wayport", f"Connecting to {code} ...")

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
            with contextlib.suppress(asyncio.CancelledError):
                await self._send_task

        if self._recv_task:
            self._recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._recv_task

        if self._status_task:
            self._status_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._status_task

        if self._key_task:
            self._key_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._key_task

        # Restore OS network settings before anything else can fail.
        if self.system_proxy is not None:
            self.system_proxy.disable()

        if self._proxy:
            await self._proxy.stop()

        ui.end_status()
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

                # Encrypt if enabled
                if self._encryption_key:
                    frame = Frame(
                        frame.frame_type,
                        frame.stream_id,
                        encrypt(frame.payload, self._encryption_key),
                    )

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

    def _track(self, coro: Coroutine[Any, Any, None]) -> None:
        """Run a background coroutine without losing its exceptions."""
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        task.add_done_callback(self._log_task_error)

    @staticmethod
    def _log_task_error(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        if (exc := task.exception()) is not None:
            logger.debug("Background task failed", error=str(exc))

    async def _after_connect(self) -> None:
        """Verify the tunnel, then apply the requested proxy scope."""
        if self.verify:
            from wayport.common.verify import exit_ip_via_proxy

            ip = await exit_ip_via_proxy(self.settings.proxy_host, self.settings.proxy_port)
            if ip:
                ui.field("Exiting via", ip, emphasis=True)
            else:
                ui.warn("Could not confirm the exit address (the tunnel may still be fine).")

        self._apply_mode()
        ui.blank()
        ui.hint([self._key_hint()])
        self._start_key_listener()

    def _key_hint(self) -> str:
        if self.system_proxy is None:
            return "Ctrl+C to disconnect."
        return "[s] toggle system proxy   [b] new browser window   Ctrl+C to disconnect"

    def _apply_mode(self) -> None:
        """Set up browser and/or system-wide proxying."""
        from wayport.common.defaults import relay_host
        from wayport.common.proxymodes import launch_browser, manual_browser_instructions
        from wayport.common.sysproxy import ProxySpec, SystemProxyGuard

        bypass = (relay_host(self.settings.relay_url),)
        spec = ProxySpec(
            host=self.settings.proxy_host, port=self.settings.proxy_port, bypass=bypass
        )
        self.system_proxy = SystemProxyGuard(spec)

        if self.mode == "browser":
            self._browser = launch_browser(
                self.settings.proxy_port, self.settings.proxy_host, bypass=bypass
            )
            if self._browser:
                ui.success("Opened a browser window that uses the tunnel.")
                ui.hint(["Everything else on this machine is unaffected."])
            else:
                ui.hint(manual_browser_instructions(self.settings.proxy_port))
        elif self.mode == "system" and self.system_proxy.enable():
            ui.success("System proxy configured; all traffic uses the tunnel.")
        elif self.mode == "system":
            ui.warn(f"Could not configure the system proxy: {self.system_proxy.reason}")

    def _start_key_listener(self) -> None:
        """Watch stdin for the single-key toggles, where that makes sense."""
        if not sys.stdin.isatty() or sys.platform == "win32":
            return
        try:
            self._key_task = asyncio.create_task(self._key_loop())
        except Exception:
            logger.debug("Key listener unavailable")

    async def _key_loop(self) -> None:
        """Read single keypresses in raw mode, restoring the terminal after."""
        import termios
        import tty

        loop = asyncio.get_running_loop()
        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
        queue: asyncio.Queue[str] = asyncio.Queue()

        def on_readable() -> None:
            with contextlib.suppress(OSError):
                queue.put_nowait(os.read(fd, 1).decode("utf-8", "ignore"))

        try:
            tty.setcbreak(fd)
            loop.add_reader(fd, on_readable)
            while True:
                key = (await queue.get()).lower()
                if key == "s":
                    self._toggle_system_proxy()
                elif key == "b":
                    self._open_browser_window()
        except asyncio.CancelledError:
            raise
        finally:
            with contextlib.suppress(Exception):
                loop.remove_reader(fd)
            with contextlib.suppress(Exception):
                termios.tcsetattr(fd, termios.TCSADRAIN, saved)

    def _toggle_system_proxy(self) -> None:
        if self.system_proxy is None:
            return
        if self.system_proxy.toggle():
            ui.success("System proxy ON - everything on this machine uses the tunnel.")
        elif self.system_proxy.reason and not self.system_proxy.active:
            ui.warn(f"Could not configure the system proxy: {self.system_proxy.reason}")
        else:
            ui.info("System proxy OFF - previous settings restored.")

    def _open_browser_window(self) -> None:
        from wayport.common.defaults import relay_host
        from wayport.common.proxymodes import launch_browser

        proc = launch_browser(
            self.settings.proxy_port,
            self.settings.proxy_host,
            bypass=(relay_host(self.settings.relay_url),),
        )
        if proc:
            ui.success("Opened another browser window.")
        else:
            ui.warn("No Chrome or Edge found.")

    async def _status_loop(self) -> None:
        """Refresh the single-line status display."""
        while True:
            try:
                await asyncio.sleep(2)
                ui.status(self._status_line())
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    def _status_line(self) -> str:
        """Compose the status line, including the live proxy-mode toggles."""
        health = self._health
        state = "connected" if health.tunnel_connected else "connecting"
        sent = health.bytes_sent / 1024
        recv = health.bytes_received / 1024
        parts = [
            state,
            f"up {sent:.1f}KB",
            f"down {recv:.1f}KB",
        ]
        if self.system_proxy is not None:
            parts.append(f"[s] system: {'on' if self.system_proxy.active else 'off'}")
            parts.append("[b] browser")
        return "  " + "  |  ".join(parts)

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

        ui.blank()
        ui.field("Connected to", peer_device_name, emphasis=True)
        ui.field("Proxy", f"{self.settings.proxy_host}:{self.settings.proxy_port}")
        self._track(self._after_connect())

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

        ui.warn(f"Disconnected: {reason}")
        logger.info("Disconnected from exit node", reason=reason)

        if self._on_disconnected:
            self._on_disconnected(reason)

    def _handle_connection_status(self, status: str) -> None:
        """Handle connection status change.

        Args:
            status: Status string
        """
        if status == "connecting":
            # Clear stale queues when starting a new connection attempt
            # Old pending frames are for streams that may no longer exist
            self._clear_stale_queues()
            ui.status("Connecting to relay...")
        elif status == "connected_to_relay":
            self._health.relay_connected = True
            self._health.reconnect_count += 1
            ui.status("Connected to relay, waiting for the other machine...")
        elif status == "tunnel_established":
            self._health.tunnel_connected = True
        elif status == "disconnected":
            self._health.relay_connected = False
            self._health.tunnel_connected = False
            ui.status("Connection lost, reconnecting...")

        if self._on_connection_status:
            self._on_connection_status(status)

    def _clear_stale_queues(self) -> None:
        """Clear stale data from queues on reconnect.

        This is called when starting a new connection attempt to ensure
        we don't send stale data from previous connections.
        """
        # Clear pending queue (frames from disconnection period)
        stale_pending = len(self._pending_queue)
        self._pending_queue.clear()

        # Drain the send queue
        stale_send = 0
        while not self._send_queue.empty():
            try:
                self._send_queue.get_nowait()
                stale_send += 1
            except asyncio.QueueEmpty:
                break

        # Drain the receive queue
        stale_recv = 0
        while not self._recv_queue.empty():
            try:
                self._recv_queue.get_nowait()
                stale_recv += 1
            except asyncio.QueueEmpty:
                break

        if stale_pending > 0 or stale_send > 0 or stale_recv > 0:
            logger.info(
                "Cleared stale queues on reconnect",
                pending=stale_pending,
                send=stale_send,
                recv=stale_recv,
            )

    def _handle_error(self, error_code: str, error_message: str) -> None:
        """Handle an error.

        Args:
            error_code: Error code
            error_message: Error message
        """
        ui.error(f"{error_message}")
        logger.error("Error", code=error_code, message=error_message)
        if self._on_error:
            self._on_error(error_code, error_message)

    def _handle_data_received(self, frame: Frame) -> None:
        """Handle data received from the exit node.

        Args:
            frame: The received frame
        """
        try:
            # Decrypt if enabled
            if self._encryption_key:
                try:
                    frame = Frame(
                        frame.frame_type,
                        frame.stream_id,
                        decrypt(frame.payload, self._encryption_key),
                    )
                except Exception as e:
                    logger.error("Decryption failed", error=str(e))
                    return

            self._recv_queue.put_nowait(frame)
        except asyncio.QueueFull:
            logger.warning("Receive queue full, dropping frame")


async def run_client(
    code: str,
    settings: ClientSettings | None = None,
    mode: str = "none",
    verify: bool = True,
) -> None:
    """Run the client.

    Args:
        code: Connection code
        settings: Client settings
        mode: What to route through the tunnel -- "browser", "system" or "none"
        verify: Check and report the exit IP once connected
    """
    client = WayportClient(settings)
    client.mode = mode
    client.verify = verify
    await client.connect(code)
