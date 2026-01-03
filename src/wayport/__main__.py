"""CLI entry point for Wayport."""

from __future__ import annotations

import argparse
import asyncio
import sys


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="wayport",
        description="Internet sharing application using SOCKS5 proxy with WebSocket relay",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Server (exit node) command
    server_parser = subparsers.add_parser(
        "server",
        help="Run as exit node (shares your internet)",
        aliases=["s"],
    )
    server_parser.add_argument(
        "--relay-url",
        default="ws://localhost:8080",
        help="Relay server URL (default: ws://localhost:8080)",
    )
    server_parser.add_argument(
        "--device-name",
        help="Device name to display to clients",
    )
    server_parser.add_argument(
        "--code",
        help="Preferred connection code (relay will use if available)",
    )
    server_parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Log level (default: INFO)",
    )

    # Client command
    client_parser = subparsers.add_parser(
        "client",
        help="Connect to an exit node",
        aliases=["c"],
    )
    client_parser.add_argument(
        "code",
        nargs="?",
        help="Connection code (prompted if not provided)",
    )
    client_parser.add_argument(
        "--relay-url",
        default="ws://localhost:8080",
        help="Relay server URL (default: ws://localhost:8080)",
    )
    client_parser.add_argument(
        "--proxy-port",
        type=int,
        default=1080,
        help="Local SOCKS5 proxy port (default: 1080)",
    )
    client_parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Log level (default: INFO)",
    )

    # Relay server command
    relay_parser = subparsers.add_parser(
        "relay",
        help="Run the relay server",
        aliases=["r"],
    )
    relay_parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0)",
    )
    relay_parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port to bind to (default: 8080)",
    )
    relay_parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Log level (default: INFO)",
    )

    args = parser.parse_args()

    if args.command in ("server", "s"):
        run_server(args)
    elif args.command in ("client", "c"):
        run_client(args)
    elif args.command in ("relay", "r"):
        run_relay(args)
    else:
        parser.print_help()
        sys.exit(1)


def run_server(args: argparse.Namespace) -> None:
    """Run the exit node server."""
    from wayport.common.config import ExitNodeSettings
    from wayport.exitnode.server import run_exit_node

    settings = ExitNodeSettings(
        relay_url=args.relay_url,
        log_level=args.log_level,
    )
    if args.device_name:
        settings = ExitNodeSettings(
            relay_url=args.relay_url,
            device_name=args.device_name,
            log_level=args.log_level,
        )

    preferred_code = args.code.upper() if args.code else None
    asyncio.run(run_exit_node(settings, preferred_code=preferred_code))


def run_client(args: argparse.Namespace) -> None:
    """Run the client."""
    from wayport.common.config import ClientSettings
    from wayport.client.client import run_client as run_client_impl

    settings = ClientSettings(
        relay_url=args.relay_url,
        proxy_port=args.proxy_port,
        log_level=args.log_level,
    )

    code = args.code
    if not code:
        code = input("Enter connection code: ").strip().upper()

    if not code:
        print("Error: Connection code is required")
        sys.exit(1)

    asyncio.run(run_client_impl(code, settings))


def run_relay(args: argparse.Namespace) -> None:
    """Run the relay server."""
    from wayport.common.config import RelaySettings
    from wayport.relay.server import run_relay_server

    settings = RelaySettings(
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )

    asyncio.run(run_relay_server(settings))


if __name__ == "__main__":
    main()
