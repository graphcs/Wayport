"""Tests for binary frame and SOCKS request encoding."""

from __future__ import annotations

import pytest

from wayport.common.protocol import (
    MAX_FRAME_PAYLOAD,
    Frame,
    FrameType,
    Message,
    Socks5AddressType,
    StreamOpenRequest,
)


@pytest.mark.parametrize("frame_type", list(FrameType))
def test_frame_roundtrip_every_type(frame_type: FrameType) -> None:
    frame = Frame(frame_type=frame_type, stream_id=42, payload=b"hello")
    decoded = Frame.decode(frame.encode())
    assert decoded.frame_type == frame_type
    assert decoded.stream_id == 42
    assert decoded.payload == b"hello"


@pytest.mark.parametrize("stream_id", [0, 1, 65535, 2**32 - 1])
def test_frame_roundtrip_stream_id_bounds(stream_id: int) -> None:
    decoded = Frame.decode(Frame(FrameType.DATA, stream_id, b"x").encode())
    assert decoded.stream_id == stream_id


def test_frame_empty_payload() -> None:
    decoded = Frame.decode(Frame(FrameType.DATA, 1, b"").encode())
    assert decoded.payload == b""


def test_frame_max_payload_is_allowed() -> None:
    payload = b"\x00" * MAX_FRAME_PAYLOAD
    decoded = Frame.decode(Frame(FrameType.DATA, 1, payload).encode())
    assert len(decoded.payload) == MAX_FRAME_PAYLOAD


def test_frame_rejects_oversized_payload() -> None:
    frame = Frame(FrameType.DATA, 1, b"\x00" * (MAX_FRAME_PAYLOAD + 1))
    with pytest.raises(ValueError, match="Payload too large"):
        frame.encode()


@pytest.mark.parametrize(
    ("addr", "atyp"),
    [
        ("93.184.216.34", Socks5AddressType.IPV4),
        ("2606:2800:220:1:248:1893:25c8:1946", Socks5AddressType.IPV6),
        ("example.com", Socks5AddressType.DOMAIN),
    ],
)
def test_stream_open_request_roundtrip(addr: str, atyp: Socks5AddressType) -> None:
    request = StreamOpenRequest(address_type=atyp, dest_addr=addr, dest_port=443)
    decoded = StreamOpenRequest.decode(request.encode())
    assert decoded.dest_addr == addr
    assert decoded.dest_port == 443
    assert decoded.address_type == atyp


@pytest.mark.parametrize("port", [0, 80, 65535])
def test_stream_open_request_port_bounds(port: int) -> None:
    request = StreamOpenRequest(Socks5AddressType.DOMAIN, "example.com", port)
    decoded = StreamOpenRequest.decode(request.encode())
    assert decoded.dest_port == port


def test_stream_open_request_long_domain() -> None:
    """A domain name uses a single length byte, so 255 is the maximum."""
    host = "a" * 255
    request = StreamOpenRequest(Socks5AddressType.DOMAIN, host, 443)
    decoded = StreamOpenRequest.decode(request.encode())
    assert decoded.dest_addr == host


def test_message_from_json_accepts_bytes_and_str() -> None:
    assert Message.from_json('{"type": "ping"}') == {"type": "ping"}
    assert Message.from_json(b'{"type": "ping"}') == {"type": "ping"}
