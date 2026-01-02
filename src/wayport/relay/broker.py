"""Connection broker that matches clients to exit nodes and relays data."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import uuid4

from wayport.common.logging import get_logger
from wayport.common.protocol import (
    ConnectedMessage,
    ErrorCode,
    ErrorMessage,
    PeerConnectingMessage,
    PeerDisconnectedMessage,
)

if TYPE_CHECKING:
    from wayport.relay.session import ClientSession, ExitNodeSession, SessionManager

logger = get_logger(__name__)


class ConnectionBroker:
    """Brokers connections between clients and exit nodes."""

    def __init__(self, session_manager: SessionManager) -> None:
        """Initialize the connection broker.

        Args:
            session_manager: The session manager instance
        """
        self.session_manager = session_manager

    async def connect_client_to_exitnode(
        self,
        client_session: ClientSession,
        code: str,
    ) -> bool:
        """Attempt to connect a client to an exit node using a code.

        Args:
            client_session: The client session
            code: The connection code

        Returns:
            True if connection was established, False otherwise
        """
        # Look up exit node by code
        exitnode = self.session_manager.get_exitnode_by_code(code)

        if exitnode is None:
            await self._send_error(
                client_session,
                ErrorCode.INVALID_CODE,
                "Invalid or expired connection code",
            )
            return False

        if not exitnode.is_available():
            await self._send_error(
                client_session,
                ErrorCode.SERVER_BUSY,
                "Exit node is busy with another connection",
            )
            return False

        # Generate tunnel ID
        tunnel_id = str(uuid4())

        # Notify exit node that a client is connecting
        peer_connecting_msg = PeerConnectingMessage(
            client_id=client_session.session_id,
            tunnel_id=tunnel_id,
        )
        try:
            await exitnode.websocket.send_str(peer_connecting_msg.to_json())
        except Exception as e:
            logger.error("Failed to notify exit node", error=str(e))
            await self._send_error(
                client_session,
                ErrorCode.TUNNEL_ERROR,
                "Failed to reach exit node",
            )
            return False

        # Link the sessions
        if not self.session_manager.link_sessions(
            exitnode.session_id,
            client_session.session_id,
            tunnel_id,
        ):
            await self._send_error(
                client_session,
                ErrorCode.TUNNEL_ERROR,
                "Failed to establish tunnel",
            )
            return False

        # Notify client of successful connection
        connected_msg = ConnectedMessage(
            tunnel_id=tunnel_id,
            peer_device_name=exitnode.device_name,
        )
        await client_session.websocket.send_str(connected_msg.to_json())

        logger.info(
            "Tunnel established",
            tunnel_id=tunnel_id,
            client_id=client_session.session_id,
            exitnode_id=exitnode.session_id,
        )

        return True

    async def relay_binary_data(
        self,
        source_session_id: str,
        data: bytes,
        is_client: bool,
    ) -> None:
        """Relay binary data from one peer to the other.

        Args:
            source_session_id: Session ID of the sender
            data: Binary data to relay
            is_client: True if source is a client, False if exit node
        """
        if is_client:
            # Client -> Exit node
            client = self.session_manager.get_client(source_session_id)
            if client and client.connected_exitnode_id:
                exitnode = self.session_manager.get_exitnode(client.connected_exitnode_id)
                if exitnode:
                    try:
                        await exitnode.websocket.send_bytes(data)
                    except Exception as e:
                        logger.debug("Failed to relay to exitnode", error=str(e))
        else:
            # Exit node -> Client
            exitnode = self.session_manager.get_exitnode(source_session_id)
            if exitnode and exitnode.connected_client_id:
                client = self.session_manager.get_client(exitnode.connected_client_id)
                if client:
                    try:
                        await client.websocket.send_bytes(data)
                    except Exception as e:
                        logger.debug("Failed to relay to client", error=str(e))

    async def disconnect_tunnel(self, tunnel_id: str, reason: str = "") -> None:
        """Disconnect a tunnel and notify both parties.

        Args:
            tunnel_id: The tunnel ID to disconnect
            reason: Reason for disconnection
        """
        # Unlink sessions
        exitnode_id, client_id = self.session_manager.unlink_sessions(tunnel_id)

        # Notify both parties
        disconnect_msg = PeerDisconnectedMessage(tunnel_id=tunnel_id, reason=reason)
        msg_json = disconnect_msg.to_json()

        if exitnode_id:
            exitnode = self.session_manager.get_exitnode(exitnode_id)
            if exitnode:
                try:
                    await exitnode.websocket.send_str(msg_json)
                except Exception:
                    pass

        if client_id:
            client = self.session_manager.get_client(client_id)
            if client:
                try:
                    await client.websocket.send_str(msg_json)
                except Exception:
                    pass

        logger.info("Tunnel disconnected", tunnel_id=tunnel_id, reason=reason)

    async def handle_exitnode_disconnect(self, exitnode_id: str) -> None:
        """Handle an exit node disconnecting.

        Args:
            exitnode_id: The exit node session ID
        """
        exitnode = self.session_manager.get_exitnode(exitnode_id)
        if exitnode and exitnode.tunnel_id:
            await self.disconnect_tunnel(exitnode.tunnel_id, "Exit node disconnected")

    async def handle_client_disconnect(self, client_id: str) -> None:
        """Handle a client disconnecting.

        Args:
            client_id: The client session ID
        """
        client = self.session_manager.get_client(client_id)
        if client and client.tunnel_id:
            await self.disconnect_tunnel(client.tunnel_id, "Client disconnected")

    async def _send_error(
        self,
        session: ClientSession,
        error_code: str,
        error_message: str,
    ) -> None:
        """Send an error message to a client.

        Args:
            session: The client session
            error_code: Error code
            error_message: Human-readable error message
        """
        msg = ErrorMessage(error_code=error_code, error_message=error_message)
        try:
            await session.websocket.send_str(msg.to_json())
        except Exception:
            pass
