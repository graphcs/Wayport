"""Tests for system proxy application and restore.

The apply/restore cycle is the one place a bug leaves the user's machine
unable to reach the internet, so it is tested against a recorded transcript of
`networksetup` calls rather than against the real system.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from wayport.common import sysproxy
from wayport.common.sysproxy import MacOSBackend, ProxySpec, SystemProxyGuard, recover_stale

SERVICES = "An asterisk (*) denotes that a network service is disabled.\nWi-Fi\nEthernet\n*Old\n"


class FakeNetworksetup:
    """Records calls and answers queries the way networksetup does."""

    def __init__(self, enabled: bool = False, server: str = "", port: str = "0") -> None:
        self.calls: list[list[str]] = []
        self.enabled = enabled
        self.server = server
        self.port = port

    def __call__(self, cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        if cmd[1] == "-listallnetworkservices":
            out = SERVICES
        elif cmd[1] == "-getsocksfirewallproxy":
            out = (
                f"Enabled: {'Yes' if self.enabled else 'No'}\n"
                f"Server: {self.server}\nPort: {self.port}\n"
            )
        else:
            out = ""
        return subprocess.CompletedProcess(cmd, 0, out, "")


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeNetworksetup:
    stub = FakeNetworksetup(enabled=False, server="10.0.0.1", port="9999")
    monkeypatch.setattr(subprocess, "run", stub)
    monkeypatch.setattr(sysproxy, "_run", lambda cmd: stub(cmd))
    monkeypatch.setenv("WAYPORT_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(MacOSBackend, "available", lambda _self: (True, ""))
    monkeypatch.setattr(sysproxy, "get_backend", MacOSBackend)
    return stub


def _args(calls: list[list[str]], flag: str) -> list[list[str]]:
    return [c for c in calls if len(c) > 1 and c[1] == flag]


def test_disabled_services_are_skipped(fake: FakeNetworksetup) -> None:
    """A service prefixed with * is off and must not be touched."""
    guard = SystemProxyGuard(ProxySpec("127.0.0.1", 1080))
    assert guard.enable()
    configured = {c[2] for c in _args(fake.calls, "-setsocksfirewallproxy")}
    assert configured == {"Wi-Fi", "Ethernet"}
    assert "Old" not in configured


def test_apply_then_restore_returns_previous_values(fake: FakeNetworksetup) -> None:
    """Restore must put the address back, not merely switch the proxy off."""
    guard = SystemProxyGuard(ProxySpec("127.0.0.1", 1080))
    assert guard.enable()
    fake.calls.clear()
    guard.disable()

    restored = _args(fake.calls, "-setsocksfirewallproxy")
    assert restored, "expected the previous server/port to be written back"
    for call in restored:
        assert call[3] == "10.0.0.1"
        assert call[4] == "9999"
    # It was previously disabled, so it must end disabled.
    for call in _args(fake.calls, "-setsocksfirewallproxystate"):
        assert call[3] == "off"


def test_previously_enabled_proxy_is_left_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stub = FakeNetworksetup(enabled=True, server="10.0.0.1", port="9999")
    monkeypatch.setattr(sysproxy, "_run", lambda cmd: stub(cmd))
    monkeypatch.setenv("WAYPORT_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(MacOSBackend, "available", lambda _self: (True, ""))

    guard = SystemProxyGuard(ProxySpec("127.0.0.1", 1080))
    assert guard.enable()
    stub.calls.clear()
    guard.disable()

    states = {c[3] for c in _args(stub.calls, "-setsocksfirewallproxystate")}
    assert states == {"on"}


def test_state_file_written_while_active_and_removed_after(
    fake: FakeNetworksetup,  # noqa: ARG001 - patches subprocess
    tmp_path: Path,
) -> None:
    """The state file is what makes recovery after kill -9 possible."""
    state = tmp_path / "proxy-state.json"
    guard = SystemProxyGuard(ProxySpec("127.0.0.1", 1080))
    guard.enable()
    assert state.exists()
    saved = json.loads(state.read_text())
    assert saved["backend"] == "macos"
    assert saved["changed"]

    guard.disable()
    assert not state.exists()


def test_toggle_flips_state(fake: FakeNetworksetup) -> None:  # noqa: ARG001 - patches subprocess
    guard = SystemProxyGuard(ProxySpec("127.0.0.1", 1080))
    assert guard.toggle() is True
    assert guard.active is True
    assert guard.toggle() is False
    assert guard.active is False


def test_recover_stale_restores_a_dead_run(fake: FakeNetworksetup, tmp_path: Path) -> None:
    """Simulates kill -9: a state file whose process no longer exists."""
    (tmp_path / "proxy-state.json").write_text(
        json.dumps(
            {
                "backend": "macos",
                "pid": 999_999,  # not a live process
                "snapshot": {"Wi-Fi": {"Enabled": "No", "Server": "10.0.0.1", "Port": "9999"}},
                "changed": ["Wi-Fi"],
            }
        )
    )
    fake.calls.clear()
    assert recover_stale() is True
    assert not (tmp_path / "proxy-state.json").exists()
    assert _args(fake.calls, "-setsocksfirewallproxystate")


def test_recover_stale_leaves_a_live_run_alone(
    fake: FakeNetworksetup,  # noqa: ARG001 - patches subprocess
    tmp_path: Path,
) -> None:
    """A second client must not rip the proxy out from under the first."""
    import os

    (tmp_path / "proxy-state.json").write_text(
        json.dumps({"backend": "macos", "pid": os.getppid(), "snapshot": {}, "changed": ["Wi-Fi"]})
    )
    assert recover_stale() is False
    assert (tmp_path / "proxy-state.json").exists()


def test_recover_stale_with_no_state_file_is_a_noop(
    fake: FakeNetworksetup,  # noqa: ARG001 - patches subprocess
) -> None:
    assert recover_stale() is False
