"""Tests for CLI argument handling and configuration precedence."""

from __future__ import annotations

from pathlib import Path

import pytest

from wayport.__main__ import apply_config_defaults, build_parser, settings_kwargs
from wayport.common.config import ClientSettings, RelaySettings


def parse(*argv: str) -> object:
    return build_parser().parse_args(list(argv))


def test_primary_commands_exist() -> None:
    for command in ("share", "connect", "shell", "setup", "restore", "doctor", "relay"):
        assert parse(command).command == command  # type: ignore[attr-defined]


def test_connect_defaults_to_browser_mode() -> None:
    """Browser scope is the safe default: nothing else on the machine changes."""
    assert parse("connect", "blue-otter-42").mode == "browser"  # type: ignore[attr-defined]


def test_omitted_options_are_absent_not_defaulted() -> None:
    """argparse.SUPPRESS is what lets env vars and the config file be seen.

    Regression test: passing argparse defaults through unconditionally made
    every WAYPORT_* variable a no-op.
    """
    args = parse("connect", "blue-otter-42")
    assert "relay_url" not in vars(args)
    assert settings_kwargs(args, ClientSettings) == {}


def test_provided_options_are_forwarded() -> None:
    args = parse("connect", "code", "--relay-url", "ws://example:8080")
    assert settings_kwargs(args, ClientSettings)["relay_url"] == "ws://example:8080"


def test_secret_flag_does_not_collide_with_the_secret_setting() -> None:
    """--secret is a prompt trigger, not a value.

    Sharing the `secret` dest would pass False into a `str | None` field.
    """
    args = parse("connect", "code")
    assert "secret" not in settings_kwargs(args, ClientSettings)
    assert args.prompt_secret is False  # type: ignore[attr-defined]


def test_relay_port_accepts_railway_style_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "4321")
    assert RelaySettings().port == 4321


def test_relay_prefixed_env_wins_over_bare_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "4321")
    monkeypatch.setenv("WAYPORT_RELAY_PORT", "5555")
    assert RelaySettings().port == 5555


def test_config_file_fills_unset_values(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WAYPORT_CONFIG_DIR", str(tmp_path))
    (tmp_path / "config.toml").write_text('relay_url = "ws://from-file:8080"\n')
    assert apply_config_defaults({}, ("relay_url",))["relay_url"] == "ws://from-file:8080"


def test_config_file_does_not_override_an_explicit_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WAYPORT_CONFIG_DIR", str(tmp_path))
    (tmp_path / "config.toml").write_text('relay_url = "ws://from-file:8080"\n')
    kwargs = apply_config_defaults({"relay_url": "ws://from-cli:9999"}, ("relay_url",))
    assert kwargs["relay_url"] == "ws://from-cli:9999"


def test_verbosity_maps_to_log_levels() -> None:
    from wayport.__main__ import _log_level

    assert _log_level(parse("share")) == "WARNING"
    assert _log_level(parse("share", "-v")) == "INFO"
    assert _log_level(parse("share", "-vv")) == "DEBUG"
