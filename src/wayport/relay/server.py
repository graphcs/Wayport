"""Relay server WebSocket endpoints."""

from __future__ import annotations

import asyncio
import os
import secrets
import socket
import time
from collections import defaultdict, deque
from uuid import uuid4

from aiohttp import WSMsgType, web

from wayport.common.config import RelaySettings
from wayport.common.defaults import WS_HEARTBEAT_SECONDS
from wayport.common.logging import get_logger, setup_logging
from wayport.common.protocol import (
    Message,
    MessageType,
    PongMessage,
    RegisteredMessage,
)
from wayport.relay.broker import ConnectionBroker
from wayport.relay.session import ClientSession, SessionManager

logger = get_logger(__name__)

# Cap on how fast one IP may attempt connection codes.
CONNECT_RATE_MAX_ATTEMPTS = 20
CONNECT_RATE_WINDOW_SECONDS = 60.0


def get_local_ip() -> str:
    """Get the local IP address of this machine."""
    try:
        # Connect to an external address to determine local IP
        # This doesn't actually send any data
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip: str = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class RelayServer:
    """WebSocket relay server for connection brokering."""

    def __init__(self, settings: RelaySettings | None = None) -> None:
        """Initialize the relay server.

        Args:
            settings: Server settings (defaults to RelaySettings())
        """
        self.settings = settings or RelaySettings()
        self.session_manager = SessionManager(
            code_length=self.settings.code_length,
            code_expiry_hours=self.settings.code_expiry_hours,
        )
        self.broker = ConnectionBroker(self.session_manager)
        self._cleanup_task: asyncio.Task[None] | None = None
        # Per-IP timestamps of recent /client/connect attempts, for rate limiting.
        self._connect_attempts: dict[str, deque[float]] = defaultdict(deque)

    def build_app(self) -> web.Application:
        """Build the aiohttp application.

        Separate from :meth:`start` so tests can drive the routes without
        binding a port or entering the run-forever loop.
        """
        app = web.Application()
        app.router.add_get("/health", self._handle_health)
        app.router.add_get("/server/register", self._handle_server_register)
        app.router.add_get("/client/connect", self._handle_client_connect)
        return app

    def _authorized(self, request: web.Request) -> bool:
        """Check the bearer token, if this relay requires one."""
        expected = self.settings.token
        if not expected:
            return True
        header = request.headers.get("Authorization", "")
        scheme, _, presented = header.partition(" ")
        return scheme.lower() == "bearer" and secrets.compare_digest(presented, expected)

    def _rate_limited(self, request: web.Request) -> bool:
        """Return True if this client IP has exceeded the connect rate limit.

        Connection codes are short enough to be worth guessing, so cap how fast
        anyone can try them.
        """
        ip = request.remote or "unknown"
        now = time.monotonic()
        attempts = self._connect_attempts[ip]
        while attempts and now - attempts[0] > CONNECT_RATE_WINDOW_SECONDS:
            attempts.popleft()
        if len(attempts) >= CONNECT_RATE_MAX_ATTEMPTS:
            return True
        attempts.append(now)
        return False

    async def start(self) -> None:
        """Start the relay server."""
        setup_logging(level=self.settings.log_level)

        app = self.build_app()

        # Start cleanup task
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self.settings.host, self.settings.port)
        await site.start()

        # On a PaaS the container's LAN IP is meaningless; show the public
        # domain the platform assigned instead.
        public_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
        connect_url = f"wss://{public_domain}" if public_domain else None
        local_ip = get_local_ip()

        print("\n=== Wayport Relay Server ===")
        print(f"Listening on: {self.settings.host}:{self.settings.port}")
        print(f"Auth: {'bearer token required' if self.settings.token else 'OPEN (no token)'}")
        print("\nConnect using:")
        print(f"  {connect_url or f'ws://{local_ip}:{self.settings.port}'}")
        print("=" * 30 + "\n")

        if not self.settings.token:
            logger.warning(
                "Relay is running without a token; anyone who can reach it can use it. "
                "Set WAYPORT_RELAY_TOKEN before exposing it publicly."
            )

        logger.info(
            "Relay server started",
            host=self.settings.host,
            port=self.settings.port,
            public_domain=public_domain,
            authenticated=bool(self.settings.token),
        )

        # Keep running
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass
        finally:
            if self._cleanup_task:
                self._cleanup_task.cancel()
            await runner.cleanup()

    async def _handle_health(self, request: web.Request) -> web.Response:  # noqa: ARG002
        """Health check endpoint. `request` is required by the aiohttp handler signature."""
        return web.json_response({"status": "ok"})

    async def _handle_server_register(self, request: web.Request) -> web.StreamResponse:
        """Handle exit node registration WebSocket connection."""
        if not self._authorized(request):
            logger.warning("Rejected unauthorized exit node", remote=request.remote)
            return web.json_response({"error": "unauthorized"}, status=401)

        ws = web.WebSocketResponse(heartbeat=WS_HEARTBEAT_SECONDS)
        await ws.prepare(request)

        session_id = str(uuid4())
        session = None

        logger.info("Exit node connecting", session_id=session_id)

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await self._handle_exitnode_message(ws, session_id, msg.data)
                    # Get session after first message (registration)
                    if session is None:
                        session = self.session_manager.get_exitnode(session_id)
                elif msg.type == WSMsgType.BINARY:
                    # Relay binary data to connected client
                    await self.broker.relay_binary_data(session_id, msg.data, is_client=False)
                elif msg.type == WSMsgType.ERROR:
                    logger.error(
                        "WebSocket error",
                        session_id=session_id,
                        error=str(ws.exception()),
                    )
                    break

        except Exception as e:
            logger.error("Exit node handler error", session_id=session_id, error=str(e))
        finally:
            logger.info("Exit node disconnected", session_id=session_id)
            await self.broker.handle_exitnode_disconnect(session_id)
            self.session_manager.remove_exitnode_session(session_id)

        return ws

    async def _handle_client_connect(self, request: web.Request) -> web.StreamResponse:
        """Handle client connection WebSocket."""
        if not self._authorized(request):
            logger.warning("Rejected unauthorized client", remote=request.remote)
            return web.json_response({"error": "unauthorized"}, status=401)
        if self._rate_limited(request):
            logger.warning("Rate limited client", remote=request.remote)
            return web.json_response({"error": "rate_limited"}, status=429)

        ws = web.WebSocketResponse(heartbeat=WS_HEARTBEAT_SECONDS)
        await ws.prepare(request)

        session_id = str(uuid4())
        session = self.session_manager.create_client_session(session_id, ws)

        logger.info("Client connecting", session_id=session_id)

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await self._handle_client_message(session, msg.data)
                elif msg.type == WSMsgType.BINARY:
                    # Relay binary data to connected exit node
                    await self.broker.relay_binary_data(session_id, msg.data, is_client=True)
                elif msg.type == WSMsgType.ERROR:
                    logger.error(
                        "WebSocket error",
                        session_id=session_id,
                        error=str(ws.exception()),
                    )
                    break

        except Exception as e:
            logger.error("Client handler error", session_id=session_id, error=str(e))
        finally:
            logger.info("Client disconnected", session_id=session_id)
            await self.broker.handle_client_disconnect(session_id)
            self.session_manager.remove_client_session(session_id)

        return ws

    async def _handle_exitnode_message(
        self,
        ws: web.WebSocketResponse,
        session_id: str,
        data: str,
    ) -> None:
        """Handle a message from an exit node.

        Args:
            ws: WebSocket connection
            session_id: Session ID
            data: JSON message data
        """
        try:
            msg = Message.from_json(data)
            msg_type = msg.get("type")

            if msg_type == MessageType.REGISTER:
                device_name = msg.get("device_name", "Unknown")
                preferred_code = msg.get("preferred_code")
                session = self.session_manager.create_exitnode_session(
                    session_id=session_id,
                    device_name=device_name,
                    websocket=ws,
                    preferred_code=preferred_code,
                )

                # Send registration response
                response = RegisteredMessage(
                    code=session.code,
                    expires_at=session.expires_at.isoformat() if session.expires_at else "",
                )
                await ws.send_str(response.to_json())

                logger.info(
                    "Exit node registered",
                    session_id=session_id,
                    code=session.code,
                    device_name=device_name,
                    preferred_code=preferred_code,
                )

            elif msg_type == MessageType.PING:
                await ws.send_str(PongMessage().to_json())

            elif msg_type == MessageType.PEER_ACCEPTED:
                # Exit node accepted the connection - handled in broker
                pass

        except Exception as e:
            logger.error("Error handling exit node message", error=str(e))

    async def _handle_client_message(
        self,
        session: ClientSession,
        data: str,
    ) -> None:
        """Handle a message from a client.

        Args:
            session: Client session
            data: JSON message data
        """

        try:
            msg = Message.from_json(data)
            msg_type = msg.get("type")

            if msg_type == MessageType.CONNECT:
                code = msg.get("code", "")
                await self.broker.connect_client_to_exitnode(session, code)

            elif msg_type == MessageType.PING:
                await session.websocket.send_str(PongMessage().to_json())

        except Exception as e:
            logger.error("Error handling client message", error=str(e))

    async def _cleanup_loop(self) -> None:
        """Periodically clean up expired sessions."""
        while True:
            try:
                await asyncio.sleep(60)  # Run every minute
                count = self.session_manager.cleanup_expired()
                if count > 0:
                    logger.info("Cleaned up expired sessions", count=count)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Cleanup error", error=str(e))


async def run_relay_server(settings: RelaySettings | None = None) -> None:
    """Run the relay server.

    Args:
        settings: Server settings
    """
    server = RelayServer(settings)
    await server.start()
