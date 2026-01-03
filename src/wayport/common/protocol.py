"""Protocol definitions for Wayport WebSocket communication.

This module defines:
- JSON message types for control plane (registration, connection)
- Binary frame format for data plane (tunnel traffic)
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any
from uuid import uuid4

import json


# =============================================================================
# Frame Types for Binary Protocol
# =============================================================================


class FrameType(IntEnum):
    """Binary frame types for tunnel data transfer."""

    DATA = 0x01  # Payload data
    OPEN = 0x02  # Open new stream (SOCKS5 connection request)
    CLOSE = 0x03  # Close stream
    ACK = 0x04  # Flow control acknowledgment


# Frame header format: type (1 byte) + stream_id (4 bytes) + length (2 bytes)
FRAME_HEADER_FORMAT = "!BIH"
FRAME_HEADER_SIZE = struct.calcsize(FRAME_HEADER_FORMAT)  # 7 bytes
MAX_FRAME_PAYLOAD = 65535  # Max payload size (2 bytes for length)


@dataclass
class Frame:
    """Binary frame for tunnel data transfer.

    Format:
    | 1 byte  | 4 bytes   | 2 bytes | N bytes |
    | Type    | Stream ID | Length  | Payload |
    """

    frame_type: FrameType
    stream_id: int
    payload: bytes = b""

    def encode(self) -> bytes:
        """Encode frame to bytes."""
        if len(self.payload) > MAX_FRAME_PAYLOAD:
            raise ValueError(f"Payload too large: {len(self.payload)} > {MAX_FRAME_PAYLOAD}")
        header = struct.pack(
            FRAME_HEADER_FORMAT, self.frame_type, self.stream_id, len(self.payload)
        )
        return header + self.payload

    @classmethod
    def decode(cls, data: bytes) -> Frame:
        """Decode bytes to frame."""
        if len(data) < FRAME_HEADER_SIZE:
            raise ValueError(f"Data too short: {len(data)} < {FRAME_HEADER_SIZE}")
        frame_type, stream_id, length = struct.unpack(
            FRAME_HEADER_FORMAT, data[:FRAME_HEADER_SIZE]
        )
        payload = data[FRAME_HEADER_SIZE : FRAME_HEADER_SIZE + length]
        if len(payload) != length:
            raise ValueError(f"Incomplete payload: {len(payload)} != {length}")
        return cls(frame_type=FrameType(frame_type), stream_id=stream_id, payload=payload)


# =============================================================================
# SOCKS5 Constants
# =============================================================================


class Socks5AuthMethod(IntEnum):
    """SOCKS5 authentication methods."""

    NO_AUTH = 0x00
    GSSAPI = 0x01
    USERNAME_PASSWORD = 0x02
    NO_ACCEPTABLE = 0xFF


class Socks5Command(IntEnum):
    """SOCKS5 commands."""

    CONNECT = 0x01
    BIND = 0x02
    UDP_ASSOCIATE = 0x03


class Socks5AddressType(IntEnum):
    """SOCKS5 address types."""

    IPV4 = 0x01
    DOMAIN = 0x03
    IPV6 = 0x04


class Socks5Reply(IntEnum):
    """SOCKS5 reply codes."""

    SUCCEEDED = 0x00
    GENERAL_FAILURE = 0x01
    CONNECTION_NOT_ALLOWED = 0x02
    NETWORK_UNREACHABLE = 0x03
    HOST_UNREACHABLE = 0x04
    CONNECTION_REFUSED = 0x05
    TTL_EXPIRED = 0x06
    COMMAND_NOT_SUPPORTED = 0x07
    ADDRESS_TYPE_NOT_SUPPORTED = 0x08


# =============================================================================
# JSON Message Types (Control Plane)
# =============================================================================


class MessageType:
    """WebSocket JSON message types."""

    # Server registration
    REGISTER = "register"
    REGISTERED = "registered"

    # Client connection
    CONNECT = "connect"
    CONNECTED = "connected"

    # Peer notifications (relay to exit node)
    PEER_CONNECTING = "peer_connecting"
    PEER_ACCEPTED = "peer_accepted"
    PEER_DISCONNECTED = "peer_disconnected"

    # Heartbeat
    PING = "ping"
    PONG = "pong"

    # Errors
    ERROR = "error"


@dataclass
class Message:
    """Base class for WebSocket JSON messages."""

    type: str
    id: str = field(default_factory=lambda: str(uuid4()))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {"type": self.type, "id": self.id}

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, data: str | bytes) -> dict[str, Any]:
        """Parse JSON string to dictionary."""
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        return json.loads(data)


# -----------------------------------------------------------------------------
# Registration Messages
# -----------------------------------------------------------------------------


@dataclass
class RegisterMessage(Message):
    """Exit node registration request."""

    type: str = field(default=MessageType.REGISTER, init=False)
    device_name: str = ""
    preferred_code: str | None = None  # Preferred code to use if available

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["device_name"] = self.device_name
        if self.preferred_code:
            d["preferred_code"] = self.preferred_code
        return d


@dataclass
class RegisteredMessage(Message):
    """Registration response with connection code."""

    type: str = field(default=MessageType.REGISTERED, init=False)
    code: str = ""
    expires_at: str = ""  # ISO 8601 timestamp

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["code"] = self.code
        d["expires_at"] = self.expires_at
        return d


# -----------------------------------------------------------------------------
# Connection Messages
# -----------------------------------------------------------------------------


@dataclass
class ConnectMessage(Message):
    """Client connection request with code."""

    type: str = field(default=MessageType.CONNECT, init=False)
    code: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["code"] = self.code
        return d


@dataclass
class ConnectedMessage(Message):
    """Connection established response."""

    type: str = field(default=MessageType.CONNECTED, init=False)
    tunnel_id: str = ""
    peer_device_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["tunnel_id"] = self.tunnel_id
        d["peer_device_name"] = self.peer_device_name
        return d


# -----------------------------------------------------------------------------
# Peer Notification Messages
# -----------------------------------------------------------------------------


@dataclass
class PeerConnectingMessage(Message):
    """Notify exit node that a client is connecting."""

    type: str = field(default=MessageType.PEER_CONNECTING, init=False)
    client_id: str = ""
    tunnel_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["client_id"] = self.client_id
        d["tunnel_id"] = self.tunnel_id
        return d


@dataclass
class PeerAcceptedMessage(Message):
    """Exit node accepts client connection."""

    type: str = field(default=MessageType.PEER_ACCEPTED, init=False)
    tunnel_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["tunnel_id"] = self.tunnel_id
        return d


@dataclass
class PeerDisconnectedMessage(Message):
    """Notify that peer has disconnected."""

    type: str = field(default=MessageType.PEER_DISCONNECTED, init=False)
    tunnel_id: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["tunnel_id"] = self.tunnel_id
        d["reason"] = self.reason
        return d


# -----------------------------------------------------------------------------
# Heartbeat Messages
# -----------------------------------------------------------------------------


@dataclass
class PingMessage(Message):
    """Heartbeat ping."""

    type: str = field(default=MessageType.PING, init=False)


@dataclass
class PongMessage(Message):
    """Heartbeat pong."""

    type: str = field(default=MessageType.PONG, init=False)


# -----------------------------------------------------------------------------
# Error Messages
# -----------------------------------------------------------------------------


@dataclass
class ErrorMessage(Message):
    """Error response."""

    type: str = field(default=MessageType.ERROR, init=False)
    error_code: str = ""
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["error_code"] = self.error_code
        d["error_message"] = self.error_message
        return d


class ErrorCode:
    """Error codes for error messages."""

    INVALID_CODE = "invalid_code"
    CODE_EXPIRED = "code_expired"
    SERVER_NOT_FOUND = "server_not_found"
    SERVER_BUSY = "server_busy"
    TUNNEL_ERROR = "tunnel_error"
    INTERNAL_ERROR = "internal_error"


# =============================================================================
# Stream Open Request (sent in OPEN frame payload)
# =============================================================================


@dataclass
class StreamOpenRequest:
    """Request to open a new stream (sent in OPEN frame payload).

    Contains the SOCKS5 destination address.
    """

    address_type: Socks5AddressType
    dest_addr: str  # IP address or domain name
    dest_port: int

    def encode(self) -> bytes:
        """Encode to bytes for OPEN frame payload."""
        if self.address_type == Socks5AddressType.IPV4:
            import socket

            addr_bytes = socket.inet_aton(self.dest_addr)
        elif self.address_type == Socks5AddressType.IPV6:
            import socket

            addr_bytes = socket.inet_pton(socket.AF_INET6, self.dest_addr)
        elif self.address_type == Socks5AddressType.DOMAIN:
            domain_bytes = self.dest_addr.encode("utf-8")
            addr_bytes = bytes([len(domain_bytes)]) + domain_bytes
        else:
            raise ValueError(f"Unknown address type: {self.address_type}")

        return bytes([self.address_type]) + addr_bytes + struct.pack("!H", self.dest_port)

    @classmethod
    def decode(cls, data: bytes) -> StreamOpenRequest:
        """Decode from bytes."""
        if len(data) < 2:
            raise ValueError("Data too short")

        addr_type = Socks5AddressType(data[0])

        if addr_type == Socks5AddressType.IPV4:
            import socket

            if len(data) < 7:
                raise ValueError("Data too short for IPv4")
            dest_addr = socket.inet_ntoa(data[1:5])
            dest_port = struct.unpack("!H", data[5:7])[0]
        elif addr_type == Socks5AddressType.IPV6:
            import socket

            if len(data) < 19:
                raise ValueError("Data too short for IPv6")
            dest_addr = socket.inet_ntop(socket.AF_INET6, data[1:17])
            dest_port = struct.unpack("!H", data[17:19])[0]
        elif addr_type == Socks5AddressType.DOMAIN:
            domain_len = data[1]
            if len(data) < 2 + domain_len + 2:
                raise ValueError("Data too short for domain")
            dest_addr = data[2 : 2 + domain_len].decode("utf-8")
            dest_port = struct.unpack("!H", data[2 + domain_len : 4 + domain_len])[0]
        else:
            raise ValueError(f"Unknown address type: {addr_type}")

        return cls(address_type=addr_type, dest_addr=dest_addr, dest_port=dest_port)
