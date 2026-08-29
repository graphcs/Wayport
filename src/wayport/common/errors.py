"""Errors that carry a message a person can act on."""

from __future__ import annotations


class WayportError(Exception):
    """An error with a user-facing message and an optional hint."""

    def __init__(self, message: str, hint: str | None = None, code: str = "error") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.code = code


class FatalTunnelError(WayportError):
    """A condition that retrying will never fix."""


class SecretMismatchError(FatalTunnelError):
    """The two ends are configured with different shared secrets."""

    def __init__(self) -> None:
        super().__init__(
            "the shared secret does not match the other machine",
            "Both machines must use the same secret.\n"
            "Run `wayport setup` on each, or pass the same --secret to both.",
            code="secret_mismatch",
        )


class PortUnavailableError(WayportError):
    """The local SOCKS port is already taken."""

    def __init__(self, port: int) -> None:
        super().__init__(
            f"port {port} is already in use",
            "Another `wayport connect` may already be running.\n"
            f"Try: wayport connect <code> --proxy-port {port + 1}\n"
            "Or:  wayport connect <code> --proxy-port auto",
            code="port_unavailable",
        )
