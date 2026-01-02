"""Textual TUI for the exit node server."""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Footer, Header, Static

from wayport.common.config import ExitNodeSettings
from wayport.exitnode.server import ExitNodeServer


class ConnectionCodeDisplay(Static):
    """Widget to display the connection code."""

    DEFAULT_CSS = """
    ConnectionCodeDisplay {
        width: 100%;
        height: auto;
        padding: 1 2;
        text-align: center;
    }

    ConnectionCodeDisplay .code {
        text-style: bold;
        color: $success;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._code: str | None = None

    def set_code(self, code: str | None) -> None:
        """Set the connection code to display."""
        self._code = code
        self._update_display()

    def _update_display(self) -> None:
        """Update the display."""
        if self._code:
            self.update(f"Connection Code: [bold green]{self._code}[/bold green]")
        else:
            self.update("Waiting for connection code...")


class StatusDisplay(Static):
    """Widget to display connection status."""

    DEFAULT_CSS = """
    StatusDisplay {
        width: 100%;
        height: auto;
        padding: 1 2;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._relay_status = "disconnected"
        self._client_status = "no client"

    def set_relay_status(self, status: str) -> None:
        """Set relay connection status."""
        self._relay_status = status
        self._update_display()

    def set_client_status(self, status: str) -> None:
        """Set client connection status."""
        self._client_status = status
        self._update_display()

    def _update_display(self) -> None:
        """Update the display."""
        relay_color = {
            "connecting": "yellow",
            "connected": "green",
            "disconnected": "red",
        }.get(self._relay_status, "white")

        client_color = "green" if "connected" in self._client_status.lower() else "dim"

        self.update(
            f"Relay: [{relay_color}]{self._relay_status}[/{relay_color}]  |  "
            f"Client: [{client_color}]{self._client_status}[/{client_color}]"
        )


class InstructionsDisplay(Static):
    """Widget to display usage instructions."""

    DEFAULT_CSS = """
    InstructionsDisplay {
        width: 100%;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: round $primary;
        margin: 1 0;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static(
            "[bold]How to connect:[/bold]\n\n"
            "1. On the client machine, run: [cyan]wayport client[/cyan]\n"
            "2. Enter the connection code shown above\n"
            "3. Configure your browser to use SOCKS5 proxy:\n"
            "   [dim]Host: 127.0.0.1  Port: 1080[/dim]\n\n"
            "[dim]Press Q to quit[/dim]"
        )


class ExitNodeApp(App):
    """Textual app for the exit node."""

    TITLE = "Wayport Exit Node"
    CSS = """
    Screen {
        align: center middle;
    }

    #main-container {
        width: 60;
        height: auto;
        padding: 1 2;
    }

    #code-box {
        width: 100%;
        height: auto;
        border: heavy $accent;
        padding: 1;
        margin-bottom: 1;
        text-align: center;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
    ]

    def __init__(self, settings: ExitNodeSettings | None = None) -> None:
        super().__init__()
        self.settings = settings or ExitNodeSettings()
        self._server: ExitNodeServer | None = None
        self._server_task: asyncio.Task[None] | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Container(
                ConnectionCodeDisplay(id="code-display"),
                id="code-box",
            ),
            StatusDisplay(id="status-display"),
            InstructionsDisplay(),
            id="main-container",
        )
        yield Footer()

    async def on_mount(self) -> None:
        """Start the server when app mounts."""
        self._server = ExitNodeServer(
            settings=self.settings,
            on_code_received=self._on_code_received,
            on_client_connected=self._on_client_connected,
            on_client_disconnected=self._on_client_disconnected,
            on_connection_status=self._on_connection_status,
        )
        self._server_task = asyncio.create_task(self._server.start())

    async def on_unmount(self) -> None:
        """Stop the server when app unmounts."""
        if self._server:
            await self._server.stop()
        if self._server_task:
            self._server_task.cancel()

    def _on_code_received(self, code: str, expires_at: str) -> None:
        """Handle receiving the connection code."""
        self.call_from_thread(self._update_code, code)

    def _on_client_connected(self, tunnel_id: str) -> None:
        """Handle client connecting."""
        self.call_from_thread(self._update_client_status, "connected")

    def _on_client_disconnected(self, reason: str) -> None:
        """Handle client disconnecting."""
        self.call_from_thread(self._update_client_status, "no client")

    def _on_connection_status(self, status: str) -> None:
        """Handle connection status change."""
        self.call_from_thread(self._update_relay_status, status)
        if status == "disconnected":
            self.call_from_thread(self._update_code, None)

    def _update_code(self, code: str | None) -> None:
        """Update the code display."""
        code_display = self.query_one("#code-display", ConnectionCodeDisplay)
        code_display.set_code(code)

    def _update_relay_status(self, status: str) -> None:
        """Update the relay status display."""
        status_display = self.query_one("#status-display", StatusDisplay)
        status_display.set_relay_status(status)

    def _update_client_status(self, status: str) -> None:
        """Update the client status display."""
        status_display = self.query_one("#status-display", StatusDisplay)
        status_display.set_client_status(status)


def run_exitnode_ui(settings: ExitNodeSettings | None = None) -> None:
    """Run the exit node with TUI.

    Args:
        settings: Server settings
    """
    app = ExitNodeApp(settings)
    app.run()
