"""Configuration settings for Wayport components."""

from __future__ import annotations

import platform
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from wayport.common.defaults import DEFAULT_RELAY_URL


def get_default_device_name() -> str:
    """Get a default device name from the system."""
    return platform.node() or "Unknown Device"


class RelaySettings(BaseSettings):
    """Settings for the relay server."""

    # populate_by_name so `RelaySettings(port=...)` still works alongside the
    # validation_alias on `port` below.
    model_config = SettingsConfigDict(env_prefix="WAYPORT_RELAY_", populate_by_name=True)

    host: str = "0.0.0.0"
    # Railway (and most PaaS) inject the port to bind as $PORT, so accept that
    # as well as the prefixed name.
    port: int = Field(
        default=8080,
        validation_alias=AliasChoices("WAYPORT_RELAY_PORT", "PORT"),
    )

    # Shared bearer token required of exit nodes and clients. When unset the
    # relay serves openly (convenient for local development) but logs a warning
    # on every connection -- a public relay must always set this.
    token: str | None = None

    # Code settings
    code_length: int = 6
    code_expiry_hours: int = 24

    # Heartbeat settings
    heartbeat_interval_seconds: int = 30
    heartbeat_timeout_seconds: int = 90

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


class ExitNodeSettings(BaseSettings):
    """Settings for the exit node server."""

    model_config = SettingsConfigDict(env_prefix="WAYPORT_EXITNODE_")

    # Relay connection
    relay_url: str = DEFAULT_RELAY_URL

    # Shared bearer token for the relay; must match the relay's own token.
    relay_token: str | None = None

    # Device identification
    device_name: str = Field(default_factory=get_default_device_name)

    # Reconnection settings
    reconnect_delay_seconds: float = 1.0
    reconnect_max_delay_seconds: float = 60.0
    reconnect_backoff_multiplier: float = 2.0

    # Heartbeat settings
    heartbeat_interval_seconds: int = 30

    # Encryption
    secret: str | None = None  # Shared secret for end-to-end encryption

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


class ClientSettings(BaseSettings):
    """Settings for the client."""

    model_config = SettingsConfigDict(env_prefix="WAYPORT_CLIENT_")

    # Relay connection
    relay_url: str = DEFAULT_RELAY_URL

    # Shared bearer token for the relay; must match the relay's own token.
    relay_token: str | None = None

    # Local SOCKS proxy
    proxy_host: str = "127.0.0.1"
    proxy_port: int = 1080

    # Reconnection settings
    reconnect_delay_seconds: float = 1.0
    reconnect_max_delay_seconds: float = 60.0
    reconnect_backoff_multiplier: float = 2.0

    # Heartbeat settings
    heartbeat_interval_seconds: int = 30

    # Encryption
    secret: str | None = None  # Shared secret for end-to-end encryption

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
