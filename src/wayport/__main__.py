"""CLI entry point for Wayport."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import getpass
import sys
from typing import TYPE_CHECKING, Any

from wayport.common.defaults import DEFAULT_RELAY_URL, resolve_relay_url
from wayport.common.errors import WayportError
from wayport.common.ui import ui

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from pydantic_settings import BaseSettings

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_FATAL = 2
EXIT_INTERRUPTED = 130


def _install_truststore() -> None:
    """Use the operating system's trust store for TLS.

    Needed for ``wss://`` behind a TLS-intercepting proxy (e.g. Zscaler), whose
    root CA is installed in the OS keychain but not in certifi's bundle.
    """
    with contextlib.suppress(Exception):
        import truststore

        truststore.inject_into_ssl()


def settings_kwargs(args: argparse.Namespace, model: type[BaseSettings]) -> dict[str, Any]:
    """Collect only the CLI arguments the user actually provided.

    Options that map onto a settings field use ``argparse.SUPPRESS``, so an
    omitted option is simply absent. Forwarding only what is present preserves
    CLI > environment > config-file > default precedence.
    """
    return {k: v for k, v in vars(args).items() if k in model.model_fields and v is not None}


def apply_config_defaults(kwargs: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """Fill unset settings from ``config.toml``.

    Applied only where neither a flag nor an environment variable supplied a
    value, so the file sits below both but above the built-in default.
    """
    from wayport.common.state import load_config

    config = load_config()
    for key in keys:
        if key not in kwargs and config.get(key):
            kwargs[key] = config[key]
    return kwargs


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--relay-url",
        default=argparse.SUPPRESS,
        help=f"Relay server URL (default: {DEFAULT_RELAY_URL})",
    )
    parser.add_argument(
        "--relay-token",
        default=argparse.SUPPRESS,
        help="Relay bearer token (default: from config or environment)",
    )
    parser.add_argument(
        "--secret",
        dest="prompt_secret",
        action="store_true",
        help="Prompt for an encryption secret instead of using the configured one",
    )
    parser.add_argument("-v", "--verbose", action="count", default=0, help="Show diagnostics")


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="wayport",
        description="Share one machine's internet connection with another.",
    )
    sub = parser.add_subparsers(dest="command")

    share = sub.add_parser("share", help="Share this machine's internet connection")
    _add_common(share)
    share.add_argument("--device-name", default=argparse.SUPPRESS, help="Name shown to the client")
    share.add_argument("--code", help="Use a specific connection code")
    share.add_argument("--new-code", action="store_true", help="Rotate this machine's code")

    connect = sub.add_parser("connect", help="Connect to a machine that is sharing")
    _add_common(connect)
    connect.add_argument("code", nargs="?", help="Connection code (prompted if omitted)")
    connect.add_argument(
        "--proxy-port",
        default=argparse.SUPPRESS,
        help="Local SOCKS5 port, or 'auto' to pick a free one (default: 1080)",
    )
    connect.add_argument(
        "--mode",
        choices=["browser", "system", "none"],
        default="browser",
        help="What to route through the tunnel (default: browser)",
    )
    connect.add_argument(
        "--no-verify", action="store_true", help="Skip the post-connect exit-IP check"
    )

    shell = sub.add_parser("shell", help="Open a shell whose traffic uses the tunnel")
    shell.add_argument(
        "--proxy-port", type=int, default=1080, help="Port of a running client (default: 1080)"
    )

    setup = sub.add_parser("setup", help="Save relay settings for this machine")
    setup.add_argument("--relay-url", help="Relay server URL")
    setup.add_argument("--relay-token", help="Relay bearer token")
    setup.add_argument("--secret", help="Shared encryption secret")
    setup.add_argument("--show", action="store_true", help="Print current configuration")

    sub.add_parser("restore", help="Restore system proxy settings after a crash")

    doctor = sub.add_parser("doctor", help="Check that everything is set up correctly")
    doctor.add_argument("-v", "--verbose", action="count", default=0)

    relay = sub.add_parser("relay", help="Run a relay server (for self-hosting)")
    relay.add_argument("--host", default=argparse.SUPPRESS, help="Bind address (default: 0.0.0.0)")
    relay.add_argument(
        "--port", type=int, default=argparse.SUPPRESS, help="Port (default: $PORT, else 8080)"
    )
    relay.add_argument("--token", default=argparse.SUPPRESS, help="Require this bearer token")
    relay.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=argparse.SUPPRESS,
    )

    return parser


def _log_level(args: argparse.Namespace) -> str:
    """Diagnostics are quiet by default; -v and -vv open them up."""
    verbose = getattr(args, "verbose", 0)
    if verbose >= 2:
        return "DEBUG"
    if verbose == 1:
        return "INFO"
    return "WARNING"


def _run(coro: Coroutine[Any, Any, None]) -> int:
    """Run a coroutine, turning interruption and known errors into exit codes."""
    try:
        asyncio.run(coro)
    except KeyboardInterrupt:
        ui.end_status()
        ui.info("Stopped.")
        return EXIT_INTERRUPTED
    except WayportError as exc:
        ui.end_status()
        ui.error(exc.message, exc.hint)
        return EXIT_FATAL
    ui.end_status()
    return EXIT_OK


def main() -> None:
    """Main CLI entry point."""
    _install_truststore()
    parser = build_parser()
    args = parser.parse_args()

    handlers = {
        "share": run_share,
        "connect": run_connect,
        "shell": run_shell,
        "setup": run_setup,
        "restore": run_restore,
        "doctor": run_doctor,
        "relay": run_relay,
    }
    handler = handlers.get(args.command or "")
    if handler is None:
        _print_overview()
        sys.exit(EXIT_OK)
    try:
        sys.exit(handler(args))
    except WayportError as exc:
        ui.error(exc.message, exc.hint)
        sys.exit(EXIT_FATAL)
    except KeyboardInterrupt:
        ui.info("Stopped.")
        sys.exit(EXIT_INTERRUPTED)


def _print_overview() -> None:
    """A short orientation, rather than a wall of argparse help."""
    ui.banner("Wayport", "Share one machine's internet connection with another.")
    ui.blank()
    ui.field("On the host", "wayport share")
    ui.field("On the other", "wayport connect <code>")
    ui.blank()
    ui.hint(
        [
            "First time?     wayport setup",
            "Something odd?  wayport doctor",
            "All commands:   wayport --help",
        ]
    )
    ui.blank()


def _resolve_secret(args: argparse.Namespace, kwargs: dict[str, Any]) -> None:
    """Prompt for a secret only when asked; otherwise use the configured one."""
    if getattr(args, "prompt_secret", False):
        secret = getpass.getpass("Encryption secret: ")
        if not secret:
            raise WayportError("secret cannot be empty")
        kwargs["secret"] = secret


def _recover_stale_proxy() -> None:
    """Undo system proxy settings a previous run could not clean up.

    Runs before every connect/share so a machine left pointing at a dead
    tunnel by `kill -9` or a power cut fixes itself on the next launch.
    """
    from wayport.common.sysproxy import recover_stale

    if recover_stale():
        ui.info("Restored system proxy settings left by a previous run.")


def run_share(args: argparse.Namespace) -> int:
    """Share this machine's internet connection."""
    from wayport.common.codes import derive_word_code
    from wayport.common.config import ExitNodeSettings
    from wayport.common.logging import setup_logging
    from wayport.common.state import machine_key
    from wayport.exitnode.server import run_exit_node

    _recover_stale_proxy()

    setup_logging(level=_log_level(args))  # type: ignore[arg-type]

    kwargs = settings_kwargs(args, ExitNodeSettings)
    kwargs = apply_config_defaults(kwargs, ("relay_url", "relay_token", "secret"))
    if (relay_url := resolve_relay_url(kwargs.get("relay_url"))) is not None:
        kwargs["relay_url"] = relay_url
    _resolve_secret(args, kwargs)

    settings = ExitNodeSettings(**kwargs)
    code = args.code or derive_word_code(machine_key(rotate=args.new_code))
    return _run(run_exit_node(settings, preferred_code=code))


def run_connect(args: argparse.Namespace) -> int:
    """Connect to a machine that is sharing its connection."""
    from wayport.client.client import run_client
    from wayport.common.config import ClientSettings
    from wayport.common.logging import setup_logging
    from wayport.common.net import resolve_proxy_port

    _recover_stale_proxy()

    setup_logging(level=_log_level(args))  # type: ignore[arg-type]

    kwargs = settings_kwargs(args, ClientSettings)
    kwargs = apply_config_defaults(kwargs, ("relay_url", "relay_token", "secret"))
    if (relay_url := resolve_relay_url(kwargs.get("relay_url"))) is not None:
        kwargs["relay_url"] = relay_url
    _resolve_secret(args, kwargs)
    kwargs["proxy_port"] = resolve_proxy_port(kwargs.get("proxy_port"))

    settings = ClientSettings(**kwargs)

    code = args.code or input("Connection code: ").strip()
    if not code:
        raise WayportError(
            "a connection code is required",
            "Run `wayport share` on the other machine to get one.",
        )

    return _run(run_client(code, settings, mode=args.mode, verify=not args.no_verify))


def run_shell(args: argparse.Namespace) -> int:
    """Open a shell whose traffic goes through a running tunnel."""
    from wayport.common.proxymodes import run_proxied_shell

    return run_proxied_shell(args.proxy_port)


def run_setup(args: argparse.Namespace) -> int:
    """Save relay settings for this machine."""
    from wayport.common.codes import derive_word_code
    from wayport.common.state import config_path, load_config, machine_key, save_config

    if args.show:
        config = load_config()
        ui.banner("Wayport configuration", str(config_path()))
        ui.blank()
        if not config:
            ui.warn("Nothing configured yet. Run `wayport setup` to get started.")
            return EXIT_OK
        for key, value in sorted(config.items()):
            shown = "*" * 8 if key in ("relay_token", "secret") else str(value)
            ui.field(key, shown)
        ui.blank()
        ui.field("This machine", derive_word_code(machine_key()), emphasis=True)
        ui.blank()
        return EXIT_OK

    values = {k: str(v) for k, v in load_config().items()}

    if args.relay_url:
        values["relay_url"] = args.relay_url
    if args.relay_token:
        values["relay_token"] = args.relay_token
    if args.secret:
        values["secret"] = args.secret

    if not any((args.relay_url, args.relay_token, args.secret)):
        ui.banner("Wayport setup", "Press Enter to keep the current value.")
        ui.blank()
        relay = input(f"Relay URL [{values.get('relay_url', DEFAULT_RELAY_URL)}]: ").strip()
        if relay:
            values["relay_url"] = relay
        token = getpass.getpass("Relay token (hidden, Enter to keep): ").strip()
        if token:
            values["relay_token"] = token
        secret = getpass.getpass("Shared secret (hidden, Enter to keep): ").strip()
        if secret:
            values["secret"] = secret

    path = save_config(values)
    ui.blank()
    ui.success(f"Saved to {path}")
    ui.field("This machine", derive_word_code(machine_key()), emphasis=True)
    ui.hint(["Run `wayport share` here, then `wayport connect <code>` on the other machine."])
    ui.blank()
    return EXIT_OK


def run_restore(args: argparse.Namespace) -> int:  # noqa: ARG001
    """Restore system proxy settings left behind by a crashed run."""
    from wayport.common.sysproxy import recover_stale

    if recover_stale(force=True):
        ui.success("System proxy settings restored.")
    else:
        ui.info("Nothing to restore; system proxy settings are unchanged.")
    return EXIT_OK


def run_doctor(args: argparse.Namespace) -> int:
    """Check that everything is set up correctly."""
    from wayport.common.doctor import run_checks
    from wayport.common.logging import setup_logging

    setup_logging(level=_log_level(args))  # type: ignore[arg-type]
    return _run(run_checks())


def run_relay(args: argparse.Namespace) -> int:
    """Run a relay server."""
    from wayport.common.config import RelaySettings
    from wayport.common.logging import setup_logging
    from wayport.relay.server import run_relay_server

    settings = RelaySettings(**settings_kwargs(args, RelaySettings))
    # Structured logs when running as a service; readable ones in a terminal.
    setup_logging(level=settings.log_level, json_output=not sys.stderr.isatty())
    return _run(run_relay_server(settings))


if __name__ == "__main__":
    main()
