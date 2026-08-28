"""CLI entry point for Wayport."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import getpass
import sys
from typing import TYPE_CHECKING, Any

from wayport.common.defaults import DEFAULT_RELAY_URL, resolve_relay_url

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from pydantic_settings import BaseSettings


def _install_truststore() -> None:
    """Use the operating system's trust store for TLS.

    Needed for ``wss://`` behind a TLS-intercepting proxy (e.g. Zscaler), whose
    root CA is installed in the OS keychain but not in certifi's bundle. Failure
    is non-fatal: we simply fall back to the default SSL context.
    """
    with contextlib.suppress(Exception):
        import truststore

        truststore.inject_into_ssl()


def settings_kwargs(args: argparse.Namespace, model: type[BaseSettings]) -> dict[str, Any]:
    """Collect only the CLI arguments the user actually provided.

    Arguments that map onto a settings field use ``default=argparse.SUPPRESS``,
    so an option the user omitted is simply absent from ``args``. Passing only
    what is present preserves the intended precedence of
    CLI > environment > settings default.

    Passing argparse defaults through unconditionally is what previously made
    every ``WAYPORT_*`` environment variable a no-op.

    Args:
        args: Parsed arguments.
        model: The settings class the values are destined for.

    Returns:
        Keyword arguments to construct ``model`` with.
    """
    return {k: v for k, v in vars(args).items() if k in model.model_fields and v is not None}


def _add_relay_url_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--relay-url",
        default=argparse.SUPPRESS,
        help=f"Relay server URL (default: $WAYPORT_RELAY_URL, else {DEFAULT_RELAY_URL})",
    )


def _add_log_level_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=argparse.SUPPRESS,
        help="Log level (default: INFO)",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
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
    _add_relay_url_arg(server_parser)
    server_parser.add_argument(
        "--relay-token",
        default=argparse.SUPPRESS,
        help="Relay bearer token (default: $WAYPORT_EXITNODE_RELAY_TOKEN)",
    )
    server_parser.add_argument(
        "--device-name",
        default=argparse.SUPPRESS,
        help="Device name to display to clients",
    )
    server_parser.add_argument(
        "--code",
        help="Preferred connection code (relay will use if available)",
    )
    server_parser.add_argument(
        "--secret",
        dest="prompt_secret",
        action="store_true",
        help="Enable encryption (will prompt for secret)",
    )
    _add_log_level_arg(server_parser)

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
    _add_relay_url_arg(client_parser)
    client_parser.add_argument(
        "--relay-token",
        default=argparse.SUPPRESS,
        help="Relay bearer token (default: $WAYPORT_CLIENT_RELAY_TOKEN)",
    )
    client_parser.add_argument(
        "--proxy-port",
        type=int,
        default=argparse.SUPPRESS,
        help="Local SOCKS5 proxy port (default: 1080)",
    )
    client_parser.add_argument(
        "--secret",
        dest="prompt_secret",
        action="store_true",
        help="Enable encryption (will prompt for secret)",
    )
    _add_log_level_arg(client_parser)

    # Relay server command
    relay_parser = subparsers.add_parser(
        "relay",
        help="Run the relay server",
        aliases=["r"],
    )
    relay_parser.add_argument(
        "--host",
        default=argparse.SUPPRESS,
        help="Host to bind to (default: 0.0.0.0)",
    )
    relay_parser.add_argument(
        "--port",
        type=int,
        default=argparse.SUPPRESS,
        help="Port to bind to (default: $PORT, else 8080)",
    )
    relay_parser.add_argument(
        "--token",
        default=argparse.SUPPRESS,
        help="Require this bearer token from peers (default: $WAYPORT_RELAY_TOKEN)",
    )
    _add_log_level_arg(relay_parser)

    return parser


def _run(coro: Coroutine[Any, Any, None]) -> int:
    """Run a coroutine, translating interruption into a clean exit code."""
    try:
        asyncio.run(coro)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130
    return 0


def main() -> None:
    """Main CLI entry point."""
    _install_truststore()

    parser = build_parser()
    args = parser.parse_args()

    if args.command in ("server", "s"):
        sys.exit(run_server(args))
    elif args.command in ("client", "c"):
        sys.exit(run_client(args))
    elif args.command in ("relay", "r"):
        sys.exit(run_relay(args))
    else:
        parser.print_help()
        sys.exit(1)


def _prompt_secret() -> str:
    secret = getpass.getpass("Enter encryption secret: ")
    if not secret:
        print("Error: Secret cannot be empty")
        sys.exit(1)
    return secret


def run_server(args: argparse.Namespace) -> int:
    """Run the exit node server."""
    from wayport.common.config import ExitNodeSettings
    from wayport.exitnode.server import run_exit_node

    kwargs = settings_kwargs(args, ExitNodeSettings)
    if (relay_url := resolve_relay_url(kwargs.get("relay_url"))) is not None:
        kwargs["relay_url"] = relay_url
    if args.prompt_secret:
        kwargs["secret"] = _prompt_secret()

    settings = ExitNodeSettings(**kwargs)
    preferred_code = args.code.upper() if args.code else None
    return _run(run_exit_node(settings, preferred_code=preferred_code))


def run_client(args: argparse.Namespace) -> int:
    """Run the client."""
    from wayport.client.client import run_client as run_client_impl
    from wayport.common.config import ClientSettings

    kwargs = settings_kwargs(args, ClientSettings)
    if (relay_url := resolve_relay_url(kwargs.get("relay_url"))) is not None:
        kwargs["relay_url"] = relay_url
    if args.prompt_secret:
        kwargs["secret"] = _prompt_secret()

    settings = ClientSettings(**kwargs)

    code = args.code
    if not code:
        code = input("Enter connection code: ").strip().upper()
    if not code:
        print("Error: Connection code is required")
        return 1

    return _run(run_client_impl(code, settings))


def run_relay(args: argparse.Namespace) -> int:
    """Run the relay server."""
    from wayport.common.config import RelaySettings
    from wayport.relay.server import run_relay_server

    settings = RelaySettings(**settings_kwargs(args, RelaySettings))
    return _run(run_relay_server(settings))


if __name__ == "__main__":
    main()
