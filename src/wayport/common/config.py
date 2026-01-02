"""Configuration settings for Wayport components."""

from __future__ import annotations

import platform
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def get_default_device_name() -> str:
    """Get a default device name from the system."""
    return platform.node() or "Unknown Device"


class RelaySettings(BaseSettings):
    """Settings for the relay server."""

    model_config = SettingsConfigDict(env_prefix="WAYPORT_RELAY_")

    host: str = "0.0.0.0"
    port: int = 8080

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
    relay_url: str = "ws://localhost:8080"

    # Device identification
    device_name: str = Field(default_factory=get_default_device_name)

    # Reconnection settings
    reconnect_delay_seconds: float = 1.0
    reconnect_max_delay_seconds: float = 60.0
    reconnect_backoff_multiplier: float = 2.0

    # Heartbeat settings
    heartbeat_interval_seconds: int = 30

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


class ClientSettings(BaseSettings):
    """Settings for the client."""

    model_config = SettingsConfigDict(env_prefix="WAYPORT_CLIENT_")

    # Relay connection
    relay_url: str = "ws://localhost:8080"

    # Local SOCKS proxy
    proxy_host: str = "127.0.0.1"
    proxy_port: int = 1080

    # Reconnection settings
    reconnect_delay_seconds: float = 1.0
    reconnect_max_delay_seconds: float = 60.0
    reconnect_backoff_multiplier: float = 2.0

    # Heartbeat settings
    heartbeat_interval_seconds: int = 30

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
