"""Relay server WebSocket endpoints."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from aiohttp import WSMsgType, web

from wayport.common.config import RelaySettings
from wayport.common.logging import get_logger, setup_logging
from wayport.common.protocol import (
    Message,
    MessageType,
    PongMessage,
    RegisteredMessage,
)
from wayport.relay.broker import ConnectionBroker
from wayport.relay.session import SessionManager

logger = get_logger(__name__)


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

    async def start(self) -> None:
        """Start the relay server."""
        setup_logging(level=self.settings.log_level)

        app = web.Application()
        app.router.add_get("/health", self._handle_health)
        app.router.add_get("/server/register", self._handle_server_register)
        app.router.add_get("/client/connect", self._handle_client_connect)

        # Start cleanup task
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self.settings.host, self.settings.port)
        await site.start()

        logger.info(
            "Relay server started",
            host=self.settings.host,
            port=self.settings.port,
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

    async def _handle_health(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        return web.json_response({"status": "ok"})

    async def _handle_server_register(self, request: web.Request) -> web.Response:
        """Handle exit node registration WebSocket connection."""
        ws = web.WebSocketResponse()
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

    async def _handle_client_connect(self, request: web.Request) -> web.Response:
        """Handle client connection WebSocket."""
        ws = web.WebSocketResponse()
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
        session: "ClientSession",
        data: str,
    ) -> None:
        """Handle a message from a client.

        Args:
            session: Client session
            data: JSON message data
        """
        from wayport.relay.session import ClientSession

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
