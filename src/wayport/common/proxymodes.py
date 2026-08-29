"""Scoped proxying: one browser window, or one shell.

Neither macOS, Windows nor Linux offers per-application proxying without a
signed system extension, so "just Teams through the tunnel" is achieved by
scoping instead:

* **browser** -- launch Chrome with ``--proxy-server`` and its own profile
  directory, so exactly that window is tunnelled and your normal browser is
  untouched.
* **shell** -- a subshell with the proxy environment variables set, which
  covers curl, git, gcloud, aws and anything else that honours them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from wayport.common.logging import get_logger
from wayport.common.state import config_dir

logger = get_logger(__name__)

_MAC_CHROME_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)
_WINDOWS_CHROME_PATHS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
)
_LINUX_CHROME_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")


def find_chrome() -> str | None:
    """Locate a Chromium-family browser, or None if there isn't one."""
    if sys.platform == "darwin":
        candidates: tuple[str, ...] = _MAC_CHROME_PATHS
    elif sys.platform == "win32":
        candidates = _WINDOWS_CHROME_PATHS
    else:
        found = [shutil.which(name) for name in _LINUX_CHROME_NAMES]
        candidates = tuple(path for path in found if path)
    for path in candidates:
        if Path(path).exists():
            return path
    return None


def browser_profile_dir() -> Path:
    """A profile directory of our own, so the real browser stays untouched."""
    return config_dir() / "chrome-profile"


def chrome_command(
    path: str, port: int, host: str, url: str | None, bypass: tuple[str, ...]
) -> list[str]:
    """Build the Chrome invocation for a tunnelled window."""
    profile = browser_profile_dir()
    profile.mkdir(parents=True, exist_ok=True)
    # socks5:// (rather than socks://) makes Chrome resolve DNS through the
    # proxy, so browsing does not leak names to the local resolver.
    bypass_list = ",".join(("<-loopback>", *bypass)) if bypass else "<-loopback>"
    command = [
        path,
        f"--proxy-server=socks5://{host}:{port}",
        f"--proxy-bypass-list={bypass_list}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
    ]
    if url:
        command.append(url)
    return command


def launch_browser(
    port: int,
    host: str = "127.0.0.1",
    url: str | None = None,
    bypass: tuple[str, ...] = (),
) -> subprocess.Popen[bytes] | None:
    """Open a browser window whose traffic goes through the tunnel.

    Returns the process, or None if no browser could be found.
    """
    path = find_chrome()
    if not path:
        return None
    command = chrome_command(path, port, host, url, bypass)
    logger.debug("Launching browser", path=path)
    return subprocess.Popen(  # noqa: S603
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def manual_browser_instructions(port: int, host: str = "127.0.0.1") -> list[str]:
    """What to run by hand when we cannot find a browser."""
    return [
        "No Chrome or Edge found. To open a proxied window yourself:",
        f'  <browser> --proxy-server="socks5://{host}:{port}" \\',
        f"      --user-data-dir={browser_profile_dir()}",
    ]


def proxy_environment(
    port: int, host: str = "127.0.0.1", bypass: tuple[str, ...] = ()
) -> dict[str, str]:
    """Environment variables that route a process through the tunnel.

    ``socks5h`` (not ``socks5``) resolves hostnames at the far end, which both
    avoids DNS leaks and lets names that only resolve there work.
    """
    endpoint = f"socks5h://{host}:{port}"
    no_proxy = ",".join(("localhost", "127.0.0.1", "::1", *bypass))
    env = {
        "ALL_PROXY": endpoint,
        "all_proxy": endpoint,
        "HTTP_PROXY": endpoint,
        "http_proxy": endpoint,
        "HTTPS_PROXY": endpoint,
        "https_proxy": endpoint,
        "NO_PROXY": no_proxy,
        "no_proxy": no_proxy,
        "WAYPORT_PROXY": endpoint,
    }
    return env


def run_proxied_shell(port: int, host: str = "127.0.0.1") -> int:
    """Start an interactive shell whose traffic uses the tunnel."""
    from wayport.common.net import port_is_free
    from wayport.common.ui import ui

    if port_is_free(port, host):
        ui.error(
            f"nothing is listening on {host}:{port}",
            "Start the tunnel first with `wayport connect <code>`.",
        )
        return 2

    env = dict(os.environ)
    env.update(proxy_environment(port, host))
    # A visible marker so it is obvious which terminal is tunnelled.
    env["PROMPT_COMMAND"] = env.get("PROMPT_COMMAND", "")
    env["PS1"] = "(wayport) " + env.get("PS1", r"\h:\W \u\$ ")

    shell = env.get("COMSPEC") if sys.platform == "win32" else env.get("SHELL", "/bin/sh")
    ui.banner("Wayport shell", f"Traffic from this shell goes via {host}:{port}")
    ui.blank()
    ui.hint(["Type `exit` to leave.", "Only this shell is affected."])
    ui.blank()
    return subprocess.call([shell or "/bin/sh"], env=env)  # noqa: S603
