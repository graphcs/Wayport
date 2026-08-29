"""Per-machine configuration and state.

Two files, both under a single directory:

``config.toml``
    User settings -- relay URL, relay token, shared secret. Written once by
    ``wayport setup`` so neither machine needs command-line flags.

``state.json``
    Machine-generated state -- the random key that a stable connection code is
    derived from, and a record of any system proxy settings we changed so a
    crashed run can be cleaned up on the next launch.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import sys
import tomllib
from pathlib import Path
from typing import Any

CONFIG_FILENAME = "config.toml"
STATE_FILENAME = "state.json"
PROXY_STATE_FILENAME = "proxy-state.json"


def config_dir() -> Path:
    """Return the per-user configuration directory.

    Honours ``WAYPORT_CONFIG_DIR`` so tests never touch the real one.
    """
    override = os.environ.get("WAYPORT_CONFIG_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Wayport"
    return Path.home() / ".config" / "wayport"


def _ensure_dir() -> Path:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def config_path() -> Path:
    return config_dir() / CONFIG_FILENAME


def state_path() -> Path:
    return config_dir() / STATE_FILENAME


def proxy_state_path() -> Path:
    return config_dir() / PROXY_STATE_FILENAME


def load_config() -> dict[str, Any]:
    """Read config.toml, returning an empty mapping if absent or unreadable."""
    path = config_path()
    try:
        with path.open("rb") as handle:
            data: dict[str, Any] = tomllib.load(handle)
            return data
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def save_config(values: dict[str, str]) -> Path:
    """Write config.toml with owner-only permissions.

    The file holds the relay token and shared secret, so it must not be
    world-readable.
    """
    path = _ensure_dir() / CONFIG_FILENAME
    lines = ["# Wayport configuration.", ""]
    lines += [f"{key} = {_quote(value)}" for key, value in sorted(values.items()) if value]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _restrict(path)
    return path


def _restrict(path: Path) -> None:
    """Best-effort chmod 600; a no-op where the platform lacks POSIX modes."""
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def load_state() -> dict[str, Any]:
    try:
        data: dict[str, Any] = json.loads(state_path().read_text(encoding="utf-8"))
        return data
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict[str, Any]) -> None:
    path = _ensure_dir() / STATE_FILENAME
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    _restrict(path)


def machine_key(rotate: bool = False) -> str:
    """Return this machine's code-derivation key, creating one if needed.

    Args:
        rotate: Discard any existing key and mint a new one, which changes the
            machine's connection code.
    """
    state = load_state()
    key = state.get("machine_key")
    if rotate or not isinstance(key, str) or not key:
        key = secrets.token_urlsafe(32)
        state["machine_key"] = key
        save_state(state)
    return key
