"""WebSocket tunnel client for the Wayport client."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Callable

import aiohttp
from aiohttp import WSMsgType

from wayport.common.logging import get_logger
from wayport.common.protocol import (
    ConnectMessage,
    Frame,
    Message,
    MessageType,
    PingMessage,
)

if TYPE_CHECKING:
    from wayport.common.config import ClientSettings

logger = get_logger(__name__)


class ClientTunnel:
    """Manages the WebSocket connection to the relay server for the client."""

    def __init__(
        self,
        settings: "ClientSettings",
        on_connected: Callable[[str, str], None] | None = None,
        on_disconnected: Callable[[str], None] | None = None,
        on_connection_status: Callable[[str], None] | None = None,
        on_data_received: Callable[[Frame], None] | None = None,
        on_error: Callable[[str, str], None] | None = None,
    ) -> None:
        """Initialize the tunnel.

        Args:
            settings: Client settings
            on_connected: Callback when connected to exit node (tunnel_id, peer_device_name)
            on_disconnected: Callback when disconnected from exit node (reason)
            on_connection_status: Callback for connection status updates
            on_data_received: Callback when binary data is received from exit node
            on_error: Callback for errors (error_code, error_message)
        """
        self.settings = settings
        self.on_connected = on_connected
        self.on_disconnected = on_disconnected
        self.on_connection_status = on_connection_status
        self.on_data_received = on_data_received
        self.on_error = on_error

        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._running = False
        self._tunnel_id: str | None = None
        self._peer_device_name: str | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._reconnect_delay = settings.reconnect_delay_seconds
        self._code: str | None = None

    @property
    def is_connected(self) -> bool:
        """Check if connected to relay and exit node."""
        return self._tunnel_id is not None

    @property
    def tunnel_id(self) -> str | None:
        """Get the current tunnel ID."""
        return self._tunnel_id

    @property
    def peer_device_name(self) -> str | None:
        """Get the connected peer's device name."""
        return self._peer_device_name

    async def connect(self, code: str) -> bool:
        """Connect to an exit node using a connection code.

        Args:
            code: The connection code

        Returns:
            True if connection initiated, False otherwise
        """
        self._code = code.upper()
        self._running = True
        self._session = aiohttp.ClientSession()

        try:
            await self._connect_and_handle()
            return True
        except Exception as e:
            logger.error("Connection failed", error=str(e))
            return False

    async def start_with_reconnect(self, code: str) -> None:
        """Connect to an exit node with auto-reconnect.

        Args:
            code: The connection code
        """
        self._code = code.upper()
        self._running = True
        self._session = aiohttp.ClientSession()

        while self._running:
            try:
                await self._connect_and_handle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Connection error", error=str(e))

            if self._running:
                self._notify_status("disconnected")
                logger.info("Reconnecting", delay=self._reconnect_delay)
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * self.settings.reconnect_backoff_multiplier,
                    self.settings.reconnect_max_delay_seconds,
                )

        if self._session:
            await self._session.close()

    async def stop(self) -> None:
        """Stop the tunnel connection."""
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._ws:
            await self._ws.close()
        if self._session:
            await self._session.close()
            self._session = None

    async def send_data(self, frame: Frame) -> None:
        """Send binary data to the exit node via relay.

        Args:
            frame: The frame to send
        """
        if self._ws and not self._ws.closed:
            await self._ws.send_bytes(frame.encode())

    async def _connect_and_handle(self) -> None:
        """Connect to relay and handle messages."""
        if not self._session or not self._code:
            return

        url = f"{self.settings.relay_url}/client/connect"
        logger.info("Connecting to relay", url=url)
        self._notify_status("connecting")

        async with self._session.ws_connect(url) as ws:
            self._ws = ws
            self._reconnect_delay = self.settings.reconnect_delay_seconds
            self._notify_status("connected_to_relay")

            # Send connect message with code
            connect_msg = ConnectMessage(code=self._code)
            await ws.send_str(connect_msg.to_json())

            # Start heartbeat
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            try:
                await self._handle_messages()
            finally:
                if self._heartbeat_task:
                    self._heartbeat_task.cancel()
                    try:
                        await self._heartbeat_task
                    except asyncio.CancelledError:
                        pass
                self._ws = None
                self._tunnel_id = None
                self._peer_device_name = None

    async def _handle_messages(self) -> None:
        """Handle incoming messages from the relay."""
        if not self._ws:
            return

        async for msg in self._ws:
            if msg.type == WSMsgType.TEXT:
                await self._handle_json_message(msg.data)
            elif msg.type == WSMsgType.BINARY:
                await self._handle_binary_data(msg.data)
            elif msg.type == WSMsgType.ERROR:
                logger.error("WebSocket error", error=str(self._ws.exception()))
                break
            elif msg.type == WSMsgType.CLOSE:
                break

    async def _handle_json_message(self, data: str) -> None:
        """Handle a JSON control message.

        Args:
            data: JSON message data
        """
        try:
            msg = Message.from_json(data)
            msg_type = msg.get("type")

            if msg_type == MessageType.CONNECTED:
                self._tunnel_id = msg.get("tunnel_id", "")
                self._peer_device_name = msg.get("peer_device_name", "")
                logger.info(
                    "Connected to exit node",
                    tunnel_id=self._tunnel_id,
                    peer=self._peer_device_name,
                )
                self._notify_status("tunnel_established")
                if self.on_connected:
                    self.on_connected(self._tunnel_id, self._peer_device_name)

            elif msg_type == MessageType.PEER_DISCONNECTED:
                reason = msg.get("reason", "")
                self._tunnel_id = None
                logger.info("Disconnected from exit node", reason=reason)
                if self.on_disconnected:
                    self.on_disconnected(reason)
                # Stop if peer disconnected
                self._running = False

            elif msg_type == MessageType.ERROR:
                error_code = msg.get("error_code", "")
                error_message = msg.get("error_message", "")
                logger.error("Relay error", code=error_code, message=error_message)
                if self.on_error:
                    self.on_error(error_code, error_message)
                # Stop on error
                self._running = False

            elif msg_type == MessageType.PONG:
                pass  # Heartbeat response

        except Exception as e:
            logger.error("Error handling message", error=str(e))

    async def _handle_binary_data(self, data: bytes) -> None:
        """Handle binary data from the relay (from exit node).

        Args:
            data: Binary frame data
        """
        try:
            frame = Frame.decode(data)
            if self.on_data_received:
                self.on_data_received(frame)
        except Exception as e:
            logger.error("Error handling binary data", error=str(e))

    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeat pings."""
        while True:
            try:
                await asyncio.sleep(self.settings.heartbeat_interval_seconds)
                if self._ws and not self._ws.closed:
                    await self._ws.send_str(PingMessage().to_json())
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Heartbeat error", error=str(e))
                break

    def _notify_status(self, status: str) -> None:
        """Notify connection status change.

        Args:
            status: Status string
        """
        if self.on_connection_status:
            self.on_connection_status(status)
