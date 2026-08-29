"""The UI must never crash on a console that cannot represent its symbols.

A Windows console commonly uses cp1252, which has no U+2713 TICK. Writing one
raises UnicodeEncodeError mid-command -- which broke `wayport setup` outright.
"""

from __future__ import annotations

import io

from wayport.common.ui import UI


class LegacyConsole(io.TextIOBase):
    """A stream that behaves like a cp1252 Windows console."""

    encoding = "cp1252"

    def __init__(self) -> None:
        self.written: list[str] = []

    def write(self, text: str) -> int:
        # Raises exactly as the real console does for unrepresentable text.
        text.encode(self.encoding)
        self.written.append(text)
        return len(text)

    def isatty(self) -> bool:
        return False


def test_success_does_not_crash_on_a_cp1252_console() -> None:
    """Regression: this raised UnicodeEncodeError and aborted the command."""
    console = LegacyConsole()
    ui = UI(stream=console, color=False)
    ui.success("saved")
    assert "".join(console.written).strip() == "+ saved"


class Utf8Console(LegacyConsole):
    """A console that can represent the tick."""

    encoding = "utf-8"


def test_tick_is_used_where_the_encoding_allows_it() -> None:
    console = Utf8Console()
    ui = UI(stream=console, color=False)
    ui.success("saved")
    assert "\u2713 saved" in "".join(console.written)


def test_every_message_type_survives_a_cp1252_console() -> None:
    console = LegacyConsole()
    ui = UI(stream=console, color=False)
    ui.banner("Wayport", "subtitle")
    ui.field("Label", "value")
    ui.info("info")
    ui.success("success")
    ui.warn("warn")
    ui.hint(["hint"])
    ui.blank()
    # Nothing raised, and something was written.
    assert console.written


def test_stream_without_an_encoding_attribute_is_handled() -> None:
    """Not every file-like object declares an encoding."""
    stream = io.StringIO()
    ui = UI(stream=stream, color=False)
    ui.success("saved")
    assert "saved" in stream.getvalue()
