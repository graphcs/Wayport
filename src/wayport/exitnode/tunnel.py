"""WebSocket tunnel client for the exit node."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Callable

import aiohttp
from aiohttp import WSMsgType

from wayport.common.logging import get_logger
from wayport.common.protocol import (
    Frame,
    FrameType,
    Message,
    MessageType,
    PingMessage,
    RegisterMessage,
)

if TYPE_CHECKING:
    from wayport.common.config import ExitNodeSettings

logger = get_logger(__name__)


class ExitNodeTunnel:
    """Manages the WebSocket connection to the relay server."""

    def __init__(
        self,
        settings: "ExitNodeSettings",
        preferred_code: str | None = None,
        on_code_received: Callable[[str, str], None] | None = None,
        on_client_connected: Callable[[str], None] | None = None,
        on_client_disconnected: Callable[[str], None] | None = None,
        on_connection_status: Callable[[str], None] | None = None,
        on_data_received: Callable[[Frame], None] | None = None,
    ) -> None:
        """Initialize the tunnel.

        Args:
            settings: Exit node settings
            preferred_code: Preferred connection code to request
            on_code_received: Callback when registration code is received (code, expires_at)
            on_client_connected: Callback when a client connects (tunnel_id)
            on_client_disconnected: Callback when a client disconnects (reason)
            on_connection_status: Callback for connection status updates
            on_data_received: Callback when binary data is received from client
        """
        self.settings = settings
        self.preferred_code = preferred_code
        self.on_code_received = on_code_received
        self.on_client_connected = on_client_connected
        self.on_client_disconnected = on_client_disconnected
        self.on_connection_status = on_connection_status
        self.on_data_received = on_data_received

        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._running = False
        self._current_tunnel_id: str | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._reconnect_delay = settings.reconnect_delay_seconds
        self._last_code: str | None = None  # Remember last code for reconnection

    @property
    def is_connected(self) -> bool:
        """Check if connected to relay."""
        return self._ws is not None and not self._ws.closed

    @property
    def current_tunnel_id(self) -> str | None:
        """Get the current tunnel ID if a client is connected."""
        return self._current_tunnel_id

    async def start(self) -> None:
        """Start the tunnel connection with auto-reconnect."""
        self._running = True
        self._session = aiohttp.ClientSession()

        while self._running:
            try:
                await self._connect()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Connection error", error=str(e))

            if self._running:
                self._notify_status("disconnected")
                logger.info(
                    "Reconnecting",
                    delay=self._reconnect_delay,
                )
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

    async def send_data(self, frame: Frame) -> None:
        """Send binary data to the connected client via relay.

        Args:
            frame: The frame to send
        """
        if self._ws and not self._ws.closed:
            await self._ws.send_bytes(frame.encode())

    async def _connect(self) -> None:
        """Connect to the relay server."""
        if not self._session:
            return

        url = f"{self.settings.relay_url}/server/register"
        logger.info("Connecting to relay", url=url)
        self._notify_status("connecting")

        async with self._session.ws_connect(url) as ws:
            self._ws = ws
            self._reconnect_delay = self.settings.reconnect_delay_seconds
            self._notify_status("connected")

            # Use preferred code or last code for reconnection
            code_to_request = self.preferred_code or self._last_code

            # Send registration message
            register_msg = RegisterMessage(
                device_name=self.settings.device_name,
                preferred_code=code_to_request,
            )
            await ws.send_str(register_msg.to_json())

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
                self._current_tunnel_id = None

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

            if msg_type == MessageType.REGISTERED:
                code = msg.get("code", "")
                expires_at = msg.get("expires_at", "")
                self._last_code = code  # Remember for reconnection
                logger.info("Registered with relay", code=code)
                if self.on_code_received:
                    self.on_code_received(code, expires_at)

            elif msg_type == MessageType.PEER_CONNECTING:
                tunnel_id = msg.get("tunnel_id", "")
                client_id = msg.get("client_id", "")
                self._current_tunnel_id = tunnel_id
                logger.info("Client connecting", tunnel_id=tunnel_id, client_id=client_id)
                if self.on_client_connected:
                    self.on_client_connected(tunnel_id)

            elif msg_type == MessageType.PEER_DISCONNECTED:
                reason = msg.get("reason", "")
                self._current_tunnel_id = None
                logger.info("Client disconnected", reason=reason)
                if self.on_client_disconnected:
                    self.on_client_disconnected(reason)

            elif msg_type == MessageType.PONG:
                pass  # Heartbeat response

            elif msg_type == MessageType.ERROR:
                error_code = msg.get("error_code", "")
                error_message = msg.get("error_message", "")
                logger.error("Relay error", code=error_code, message=error_message)

        except Exception as e:
            logger.error("Error handling message", error=str(e))

    async def _handle_binary_data(self, data: bytes) -> None:
        """Handle binary data from the relay (from connected client).

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
            status: Status string (connecting, connected, disconnected)
        """
        if self.on_connection_status:
            self.on_connection_status(status)
