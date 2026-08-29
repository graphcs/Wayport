"""System-wide proxy configuration, with guaranteed restore.

Changing OS network settings is the one thing here that can leave a machine
broken, so restore is defended in layers:

1. ``try/finally`` around the session (normal exit and exceptions)
2. ``atexit`` (``sys.exit``, unhandled exceptions)
3. signal handlers (Ctrl+C, SIGTERM)
4. a state file written *before* the change is applied
5. recovery on next launch, which is the only thing that survives ``kill -9``

Layer 4 exists solely to make layer 5 possible.

Per-application proxying is not offered because no mainstream desktop OS
supports it without a signed system extension. See ``proxymodes`` for the
browser- and shell-scoped alternatives.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

from wayport.common.logging import get_logger
from wayport.common.state import proxy_state_path

logger = get_logger(__name__)

COMMAND_TIMEOUT = 5.0


@dataclass(frozen=True)
class ProxySpec:
    """The proxy to point the system at."""

    host: str
    port: int
    bypass: tuple[str, ...] = ()


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        cmd, capture_output=True, text=True, timeout=COMMAND_TIMEOUT, check=False
    )


class MacOSBackend:
    """macOS, via ``networksetup``. Works for an admin user without sudo."""

    name = "macos"

    def available(self) -> tuple[bool, str]:
        if sys.platform != "darwin":
            return False, "not macOS"
        if not shutil.which("networksetup"):
            return False, "networksetup not found"
        return True, ""

    def _services(self) -> list[str]:
        result = _run(["networksetup", "-listallnetworkservices"])
        if result.returncode != 0:
            return []
        services = []
        for line in result.stdout.splitlines()[1:]:  # first line is a header
            name = line.strip()
            # A leading asterisk marks a disabled service.
            if name and not name.startswith("*"):
                services.append(name)
        return services

    def snapshot(self) -> dict[str, Any]:
        saved: dict[str, Any] = {}
        for service in self._services():
            result = _run(["networksetup", "-getsocksfirewallproxy", service])
            if result.returncode != 0:
                continue
            fields: dict[str, str] = {}
            for line in result.stdout.splitlines():
                key, _, value = line.partition(":")
                if value:
                    fields[key.strip()] = value.strip()
            saved[service] = fields
        return saved

    def apply(self, spec: ProxySpec) -> list[str]:
        changed = []
        for service in self._services():
            set_result = _run(
                ["networksetup", "-setsocksfirewallproxy", service, spec.host, str(spec.port)]
            )
            if set_result.returncode != 0:
                logger.debug("Could not set proxy", service=service, err=set_result.stderr)
                continue
            _run(["networksetup", "-setsocksfirewallproxystate", service, "on"])
            if spec.bypass:
                _run(["networksetup", "-setproxybypassdomains", service, *spec.bypass])
            changed.append(service)
        return changed

    def restore(self, snapshot: dict[str, Any], changed: list[str]) -> None:
        for service in changed:
            fields = snapshot.get(service, {})
            server = str(fields.get("Server", "") or "")
            port = str(fields.get("Port", "") or "0")
            # Put the address back even when the proxy was previously off, so
            # the settings match what was there before rather than merely being
            # disabled with our values left in the fields.
            if server:
                _run(["networksetup", "-setsocksfirewallproxy", service, server, port])
            was_enabled = str(fields.get("Enabled", "No")).lower() == "yes"
            state = "on" if was_enabled else "off"
            _run(["networksetup", "-setsocksfirewallproxystate", service, state])


class WindowsBackend:
    """Windows, via WinINET settings under HKCU. No administrator rights needed.

    Caveats worth stating plainly: WinINET's SOCKS support does not proxy DNS,
    and Firefox ignores these settings entirely.
    """

    name = "windows"
    _KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"

    def available(self) -> tuple[bool, str]:
        if sys.platform != "win32":
            return False, "not Windows"
        return True, ""

    def snapshot(self) -> dict[str, Any]:
        assert sys.platform == "win32"  # noqa: S101 - narrows for type checking
        import winreg

        saved: dict[str, Any] = {}
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._KEY) as key:
            for name in ("ProxyEnable", "ProxyServer", "ProxyOverride"):
                try:
                    saved[name] = winreg.QueryValueEx(key, name)[0]
                except FileNotFoundError:
                    saved[name] = None  # absent is distinct from empty
        return saved

    def apply(self, spec: ProxySpec) -> list[str]:
        assert sys.platform == "win32"  # noqa: S101 - narrows for type checking
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(
                key, "ProxyServer", 0, winreg.REG_SZ, f"socks={spec.host}:{spec.port}"
            )
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
            if spec.bypass:
                winreg.SetValueEx(
                    key, "ProxyOverride", 0, winreg.REG_SZ, ";".join(("<local>", *spec.bypass))
                )
        self._refresh()
        return ["HKCU"]

    def restore(self, snapshot: dict[str, Any], changed: list[str]) -> None:
        assert sys.platform == "win32"  # noqa: S101 - narrows for type checking
        import winreg

        if not changed:
            return
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._KEY, 0, winreg.KEY_SET_VALUE) as key:
            for name, value in snapshot.items():
                if value is None:
                    with contextlib.suppress(FileNotFoundError, OSError):
                        winreg.DeleteValue(key, name)
                elif name == "ProxyEnable":
                    winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, int(value))
                else:
                    winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(value))
        self._refresh()

    @staticmethod
    def _refresh() -> None:
        """Tell WinINET its settings changed, so running apps pick them up."""
        try:
            import ctypes

            wininet = ctypes.windll.Wininet
            wininet.InternetSetOptionW(0, 39, 0, 0)  # SETTINGS_CHANGED
            wininet.InternetSetOptionW(0, 37, 0, 0)  # REFRESH
        except Exception:
            logger.debug("Could not refresh WinINET settings")


class GnomeBackend:
    """Linux desktops running GNOME, via ``gsettings``.

    KDE is deliberately not supported rather than half-supported; the shell
    mode covers the common case there.
    """

    name = "gnome"
    _SCHEMA = "org.gnome.system.proxy"

    def available(self) -> tuple[bool, str]:
        if sys.platform.startswith("win") or sys.platform == "darwin":
            return False, "not Linux"
        if not shutil.which("gsettings"):
            return False, "gsettings not found"
        if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            return False, "no desktop session"
        return True, ""

    def snapshot(self) -> dict[str, Any]:
        return {
            "mode": _run(["gsettings", "get", self._SCHEMA, "mode"]).stdout.strip(),
            "host": _run(["gsettings", "get", f"{self._SCHEMA}.socks", "host"]).stdout.strip(),
            "port": _run(["gsettings", "get", f"{self._SCHEMA}.socks", "port"]).stdout.strip(),
        }

    def apply(self, spec: ProxySpec) -> list[str]:
        _run(["gsettings", "set", f"{self._SCHEMA}.socks", "host", spec.host])
        _run(["gsettings", "set", f"{self._SCHEMA}.socks", "port", str(spec.port)])
        _run(["gsettings", "set", self._SCHEMA, "mode", "manual"])
        return ["gnome"]

    def restore(self, snapshot: dict[str, Any], changed: list[str]) -> None:
        if not changed:
            return
        _run(
            ["gsettings", "set", f"{self._SCHEMA}.socks", "host", str(snapshot["host"]).strip("'")]
        )
        _run(["gsettings", "set", f"{self._SCHEMA}.socks", "port", str(snapshot["port"])])
        _run(["gsettings", "set", self._SCHEMA, "mode", str(snapshot["mode"]).strip("'")])


class UnsupportedBackend:
    name = "unsupported"

    def available(self) -> tuple[bool, str]:
        return False, f"no supported proxy backend for {sys.platform}"

    def snapshot(self) -> dict[str, Any]:
        return {}

    def apply(self, spec: ProxySpec) -> list[str]:  # noqa: ARG002 - protocol signature
        return []

    def restore(self, snapshot: dict[str, Any], changed: list[str]) -> None:  # noqa: ARG002
        return None


Backend = MacOSBackend | WindowsBackend | GnomeBackend | UnsupportedBackend


def get_backend() -> Backend:
    for backend in (MacOSBackend(), WindowsBackend(), GnomeBackend()):
        ok, _ = backend.available()
        if ok:
            return backend
    return UnsupportedBackend()


def recover_stale(force: bool = False, backend: Backend | None = None) -> bool:
    """Restore settings left behind by a run that did not clean up.

    Args:
        force: Restore even if the recorded process still appears to be alive.

    Returns:
        True if something was restored.
    """
    path = proxy_state_path()
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False

    if not force and _process_alive(saved.get("pid", -1)):
        return False

    backend = backend if backend is not None else get_backend()
    if backend.name != saved.get("backend"):
        path.unlink(missing_ok=True)
        return False

    try:
        backend.restore(saved.get("snapshot", {}), saved.get("changed", []))
    except Exception as exc:
        logger.warning("Could not restore system proxy", error=str(exc))
        return False
    path.unlink(missing_ok=True)
    return True


def _process_alive(pid: int) -> bool:
    if pid <= 0 or pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


class SystemProxyGuard:
    """Apply a system proxy and guarantee it is put back."""

    def __init__(self, spec: ProxySpec, backend: Backend | None = None) -> None:
        self.spec = spec
        # Injectable so tests never touch the host's real network settings.
        self.backend = backend if backend is not None else get_backend()
        self.active = False
        self.reason: str | None = None
        self._snapshot: dict[str, Any] = {}
        self._changed: list[str] = []
        self._registered = False

    def enable(self) -> bool:
        """Apply the proxy. Returns True if the system was actually changed."""
        if self.active:
            return True
        ok, reason = self.backend.available()
        if not ok:
            self.reason = reason
            return False

        # Recover first: snapshotting while a stale state file exists would
        # record *our* proxy as the previous value, making restore a no-op.
        recover_stale()

        try:
            self._snapshot = self.backend.snapshot()
            self._write_state()
            self._changed = self.backend.apply(self.spec)
        except Exception as exc:
            self.reason = str(exc)
            proxy_state_path().unlink(missing_ok=True)
            return False

        if not self._changed:
            self.reason = "no network services could be configured"
            proxy_state_path().unlink(missing_ok=True)
            return False

        self._write_state()
        self.active = True
        if not self._registered:
            atexit.register(self.disable)
            self._registered = True
        return True

    def disable(self) -> None:
        """Put the previous settings back. Safe to call more than once."""
        if not self.active:
            return
        self.active = False
        with contextlib.suppress(Exception):
            atexit.unregister(self.disable)
            self._registered = False
        try:
            self.backend.restore(self._snapshot, self._changed)
        except Exception as exc:
            logger.warning("Could not restore system proxy", error=str(exc))
        finally:
            proxy_state_path().unlink(missing_ok=True)

    def toggle(self) -> bool:
        """Flip system proxying and return the new state."""
        if self.active:
            self.disable()
            return False
        return self.enable()

    def _write_state(self) -> None:
        payload = {
            "backend": self.backend.name,
            "pid": os.getpid(),
            "snapshot": self._snapshot,
            "changed": self._changed,
        }
        path = proxy_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def __enter__(self) -> SystemProxyGuard:
        return self

    def __exit__(self, *exc: object) -> None:
        self.disable()
