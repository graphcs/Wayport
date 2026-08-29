"""Pre-flight checks: `wayport doctor`."""

from __future__ import annotations

import sys
from dataclasses import dataclass

from wayport.common.ui import ui


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    hint: str | None = None


async def run_checks() -> None:
    """Run every check and print a report."""
    checks = [
        _python_version(),
        _configuration(),
        _this_machine_code(),
        await _relay_reachable(),
        _proxy_port(),
        _proxy_backend(),
        _browser(),
        _stale_proxy_state(),
    ]

    ui.banner("Wayport doctor")
    ui.blank()
    for check in checks:
        (ui.success if check.ok else ui.warn)(f"{check.name.ljust(20)} {check.detail}")
        if check.hint and not check.ok:
            ui.hint([f"    {check.hint}"])
    ui.blank()

    failures = [c for c in checks if not c.ok]
    if failures:
        ui.info(f"{len(failures)} of {len(checks)} checks need attention.")
    else:
        ui.success("Everything looks good.")
    ui.blank()


def _python_version() -> Check:
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    ok = sys.version_info >= (3, 11)
    return Check("python", ok, version, "Wayport needs Python 3.11 or newer.")


def _configuration() -> Check:
    from wayport.common.state import config_path, load_config

    config = load_config()
    if not config:
        return Check("config", False, "not configured", "Run `wayport setup`.")
    missing = [k for k in ("relay_url", "relay_token") if not config.get(k)]
    if missing:
        return Check(
            "config",
            False,
            f"missing {', '.join(missing)}",
            "Run `wayport setup` to fill these in.",
        )
    return Check("config", True, str(config_path()))


def _this_machine_code() -> Check:
    from wayport.common.codes import derive_word_code
    from wayport.common.state import machine_key

    return Check("this machine", True, derive_word_code(machine_key()))


async def _relay_reachable() -> Check:
    import time

    import aiohttp

    from wayport.common.config import ClientSettings
    from wayport.common.state import load_config

    config = load_config()
    settings = ClientSettings(
        **{k: v for k, v in config.items() if k in ClientSettings.model_fields}
    )
    url = settings.relay_url
    health = url.replace("wss://", "https://").replace("ws://", "http://") + "/health"

    started = time.monotonic()
    try:
        async with (
            aiohttp.ClientSession(trust_env=False) as session,
            session.get(health, timeout=aiohttp.ClientTimeout(total=10)) as response,
        ):
            elapsed = int((time.monotonic() - started) * 1000)
            if response.status == 200:
                return Check("relay", True, f"{url} ({elapsed}ms)")
            return Check("relay", False, f"HTTP {response.status} from {health}")
    except Exception as exc:
        return Check(
            "relay",
            False,
            f"unreachable: {type(exc).__name__}",
            "Check the relay URL, your network, and any corporate proxy.",
        )


def _proxy_port() -> Check:
    from wayport.common.net import DEFAULT_PROXY_PORT, port_is_free

    if port_is_free(DEFAULT_PROXY_PORT):
        return Check("local port", True, f"{DEFAULT_PROXY_PORT} available")
    return Check(
        "local port",
        False,
        f"{DEFAULT_PROXY_PORT} in use",
        "Another client may be running; use --proxy-port auto.",
    )


def _proxy_backend() -> Check:
    from wayport.common.sysproxy import get_backend

    backend = get_backend()
    ok, reason = backend.available()
    if ok:
        return Check("system proxy", True, f"{backend.name} backend")
    return Check(
        "system proxy",
        False,
        reason,
        "Browser and shell modes still work; only system-wide mode is affected.",
    )


def _browser() -> Check:
    from wayport.common.proxymodes import find_chrome

    path = find_chrome()
    if path:
        return Check("browser", True, path)
    return Check(
        "browser",
        False,
        "no Chrome or Edge found",
        "Install Chrome, or use --mode system.",
    )


def _stale_proxy_state() -> Check:
    from wayport.common.state import proxy_state_path

    if proxy_state_path().exists():
        return Check(
            "proxy state",
            False,
            "settings from a previous run were left behind",
            "Run `wayport restore` to put them back.",
        )
    return Check("proxy state", True, "clean")
