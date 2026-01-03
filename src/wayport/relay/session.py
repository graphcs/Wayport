"""Session management and connection code generation for the relay server."""

from __future__ import annotations

import secrets
import string
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiohttp import web

# Alphabet for connection codes (excluding confusing characters: 0, O, I, 1, L)
CODE_ALPHABET = string.ascii_uppercase + string.digits
CODE_ALPHABET = CODE_ALPHABET.replace("0", "").replace("O", "")
CODE_ALPHABET = CODE_ALPHABET.replace("I", "").replace("1", "").replace("L", "")


def generate_connection_code(length: int = 6) -> str:
    """Generate a random connection code.

    Args:
        length: Number of characters in the code

    Returns:
        A random alphanumeric code (e.g., "ABC123")
    """
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))


@dataclass
class ExitNodeSession:
    """Represents a registered exit node session."""

    session_id: str
    device_name: str
    code: str
    websocket: web.WebSocketResponse
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    connected_client_id: str | None = None
    tunnel_id: str | None = None

    def is_expired(self) -> bool:
        """Check if the session has expired."""
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) > self.expires_at

    def is_available(self) -> bool:
        """Check if the exit node is available for new connections."""
        return not self.is_expired() and self.connected_client_id is None


@dataclass
class ClientSession:
    """Represents a connected client session."""

    session_id: str
    websocket: web.WebSocketResponse
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    connected_exitnode_id: str | None = None
    tunnel_id: str | None = None


class SessionManager:
    """Manages exit node and client sessions."""

    def __init__(self, code_length: int = 6, code_expiry_hours: int = 24) -> None:
        """Initialize the session manager.

        Args:
            code_length: Length of generated connection codes
            code_expiry_hours: Hours until a connection code expires
        """
        self.code_length = code_length
        self.code_expiry_hours = code_expiry_hours

        # Storage
        self._exitnodes: dict[str, ExitNodeSession] = {}  # session_id -> session
        self._clients: dict[str, ClientSession] = {}  # session_id -> session
        self._codes: dict[str, str] = {}  # code -> exitnode_session_id

    def create_exitnode_session(
        self,
        session_id: str,
        device_name: str,
        websocket: web.WebSocketResponse,
        preferred_code: str | None = None,
    ) -> ExitNodeSession:
        """Create a new exit node session with a unique code.

        Args:
            session_id: Unique identifier for the session
            device_name: Name of the device
            websocket: WebSocket connection
            preferred_code: Preferred code to use if available

        Returns:
            The created ExitNodeSession
        """
        # Try to use preferred code if available and not in use
        code = None
        if preferred_code:
            preferred_code = preferred_code.upper()
            if preferred_code not in self._codes:
                code = preferred_code

        # Generate a unique code if preferred not available
        if code is None:
            code = self._generate_unique_code()

        expires_at = datetime.now(timezone.utc) + timedelta(hours=self.code_expiry_hours)

        session = ExitNodeSession(
            session_id=session_id,
            device_name=device_name,
            code=code,
            websocket=websocket,
            expires_at=expires_at,
        )

        self._exitnodes[session_id] = session
        self._codes[code] = session_id

        return session

    def create_client_session(
        self,
        session_id: str,
        websocket: web.WebSocketResponse,
    ) -> ClientSession:
        """Create a new client session.

        Args:
            session_id: Unique identifier for the session
            websocket: WebSocket connection

        Returns:
            The created ClientSession
        """
        session = ClientSession(
            session_id=session_id,
            websocket=websocket,
        )
        self._clients[session_id] = session
        return session

    def get_exitnode_by_code(self, code: str) -> ExitNodeSession | None:
        """Look up an exit node by its connection code.

        Args:
            code: The connection code

        Returns:
            The ExitNodeSession if found and valid, None otherwise
        """
        code = code.upper()
        session_id = self._codes.get(code)
        if session_id is None:
            return None

        session = self._exitnodes.get(session_id)
        if session is None:
            # Clean up orphaned code
            del self._codes[code]
            return None

        if session.is_expired():
            # Clean up expired session
            self.remove_exitnode_session(session_id)
            return None

        return session

    def get_exitnode(self, session_id: str) -> ExitNodeSession | None:
        """Get an exit node session by ID."""
        return self._exitnodes.get(session_id)

    def get_client(self, session_id: str) -> ClientSession | None:
        """Get a client session by ID."""
        return self._clients.get(session_id)

    def remove_exitnode_session(self, session_id: str) -> None:
        """Remove an exit node session.

        Args:
            session_id: The session ID to remove
        """
        session = self._exitnodes.pop(session_id, None)
        if session:
            self._codes.pop(session.code, None)

    def remove_client_session(self, session_id: str) -> None:
        """Remove a client session.

        Args:
            session_id: The session ID to remove
        """
        self._clients.pop(session_id, None)

    def link_sessions(
        self,
        exitnode_id: str,
        client_id: str,
        tunnel_id: str,
    ) -> bool:
        """Link an exit node and client session for tunneling.

        Args:
            exitnode_id: Exit node session ID
            client_id: Client session ID
            tunnel_id: Unique tunnel identifier

        Returns:
            True if linking succeeded, False otherwise
        """
        exitnode = self._exitnodes.get(exitnode_id)
        client = self._clients.get(client_id)

        if exitnode is None or client is None:
            return False

        if not exitnode.is_available():
            return False

        exitnode.connected_client_id = client_id
        exitnode.tunnel_id = tunnel_id
        client.connected_exitnode_id = exitnode_id
        client.tunnel_id = tunnel_id

        return True

    def unlink_sessions(self, tunnel_id: str) -> tuple[str | None, str | None]:
        """Unlink sessions by tunnel ID.

        Args:
            tunnel_id: The tunnel ID

        Returns:
            Tuple of (exitnode_id, client_id) that were unlinked
        """
        exitnode_id = None
        client_id = None

        for session in self._exitnodes.values():
            if session.tunnel_id == tunnel_id:
                exitnode_id = session.session_id
                client_id = session.connected_client_id
                session.connected_client_id = None
                session.tunnel_id = None
                break

        if client_id:
            client = self._clients.get(client_id)
            if client:
                client.connected_exitnode_id = None
                client.tunnel_id = None

        return exitnode_id, client_id

    def _generate_unique_code(self) -> str:
        """Generate a unique connection code that doesn't conflict with existing ones."""
        for _ in range(100):  # Prevent infinite loop
            code = generate_connection_code(self.code_length)
            if code not in self._codes:
                return code
        raise RuntimeError("Could not generate unique code after 100 attempts")

    def cleanup_expired(self) -> int:
        """Remove expired sessions.

        Returns:
            Number of sessions removed
        """
        expired = [
            session_id
            for session_id, session in self._exitnodes.items()
            if session.is_expired()
        ]
        for session_id in expired:
            self.remove_exitnode_session(session_id)
        return len(expired)
