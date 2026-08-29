"""Human-facing terminal output.

Kept deliberately small and dependency-free. Diagnostics go to stderr through
structlog; everything here goes to stdout and is meant to be read by a person.

The one invariant that matters: :meth:`UI.status` is the only thing in the
codebase allowed to write a carriage return. Every other method clears the
status line first and redraws it after, which is what stops log lines and
banners from shredding the status display.
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from collections.abc import Sequence

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"


def _enable_windows_vt() -> bool:
    """Turn on ANSI processing in the Windows console.

    Avoids a colorama dependency. Returns True if colour is usable.
    """
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        # -11 is STD_OUTPUT_HANDLE; 0x4 is ENABLE_VIRTUAL_TERMINAL_PROCESSING.
        return bool(kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7))
    except Exception:
        return False


class UI:
    """Formatted output for a person watching a terminal."""

    def __init__(
        self,
        stream: TextIO | None = None,
        color: bool | None = None,
        quiet: bool = False,
    ) -> None:
        self.stream = stream if stream is not None else sys.stdout
        self.quiet = quiet
        self._status_text = ""
        self.color_enabled = self._detect_color() if color is None else color

    def _detect_color(self) -> bool:
        if os.environ.get("FORCE_COLOR"):
            return True
        if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
            return False
        if not self._isatty():
            return False
        return _enable_windows_vt()

    def _isatty(self) -> bool:
        try:
            return bool(self.stream.isatty())
        except Exception:
            return False

    def _c(self, text: str, *codes: str) -> str:
        if not self.color_enabled or not codes:
            return text
        return f"{''.join(codes)}{text}{RESET}"

    def _write(self, text: str) -> None:
        if self.quiet:
            return
        self.clear_status()
        self.stream.write(text + "\n")
        self.stream.flush()
        self._redraw_status()

    # -- structured output ------------------------------------------------

    def banner(self, title: str, subtitle: str | None = None) -> None:
        self._write("")
        self._write(self._c(title, BOLD))
        if subtitle:
            self._write(self._c(subtitle, DIM))

    def field(self, label: str, value: str, emphasis: bool = False) -> None:
        """A padded ``label   value`` line."""
        rendered = self._c(value, BOLD, CYAN) if emphasis else value
        self._write(f"  {self._c(label.ljust(14), DIM)}{rendered}")

    def info(self, message: str) -> None:
        self._write(f"  {message}")

    def success(self, message: str) -> None:
        self._write(f"  {self._c('✓', GREEN)} {message}")

    def warn(self, message: str) -> None:
        self._write(f"  {self._c('!', YELLOW)} {message}")

    def error(self, message: str, hint: str | None = None) -> None:
        """Errors go to stderr so stdout stays parseable."""
        self.clear_status()
        sys.stderr.write(f"\n  {self._c('Error', BOLD, RED)}: {message}\n")
        if hint:
            for line in hint.splitlines():
                sys.stderr.write(f"  {self._c(line, DIM)}\n")
        sys.stderr.flush()
        self._redraw_status()

    def hint(self, lines: Sequence[str]) -> None:
        for line in lines:
            self._write(f"  {self._c(line, DIM)}")

    def blank(self) -> None:
        self._write("")

    # -- transient status line --------------------------------------------

    def status(self, text: str) -> None:
        """Draw the single-line status display.

        A no-op when stdout is not a terminal, so piped output and CI logs stay
        clean rather than accumulating thousands of half-drawn lines.
        """
        if self.quiet or not self._isatty():
            return
        self._status_text = text
        self.stream.write(f"\r\033[K{text}")
        self.stream.flush()

    def clear_status(self) -> None:
        if self._status_text and self._isatty():
            self.stream.write("\r\033[K")
            self.stream.flush()

    def _redraw_status(self) -> None:
        if self._status_text and self._isatty() and not self.quiet:
            self.stream.write(f"\r\033[K{self._status_text}")
            self.stream.flush()

    def end_status(self) -> None:
        """Finish the status line so following output starts on a fresh row."""
        if self._status_text and self._isatty():
            self.stream.write("\r\033[K")
            self.stream.flush()
        self._status_text = ""


ui = UI()
