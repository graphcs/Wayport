"""Textual TUI for the Wayport client."""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Footer, Header, Input, Static

from wayport.common.config import ClientSettings
from wayport.client.client import WayportClient


class CodeInput(Static):
    """Widget for entering connection code."""

    DEFAULT_CSS = """
    CodeInput {
        width: 100%;
        height: auto;
        padding: 1 2;
    }

    CodeInput Input {
        width: 100%;
    }
    """

    def __init__(self, on_submit: callable, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._on_submit = on_submit

    def compose(self) -> ComposeResult:
        yield Static("Enter connection code:")
        yield Input(placeholder="ABC123", id="code-input")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle code submission."""
        code = event.value.strip().upper()
        if code:
            self._on_submit(code)


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
        self._status = "Enter a connection code to connect"
        self._peer = ""

    def set_status(self, status: str, peer: str = "") -> None:
        """Set the status message."""
        self._status = status
        self._peer = peer
        self._update_display()

    def _update_display(self) -> None:
        """Update the display."""
        if self._peer:
            self.update(f"Status: {self._status}\nConnected to: {self._peer}")
        else:
            self.update(f"Status: {self._status}")


class ProxyInfoDisplay(Static):
    """Widget to display proxy information when connected."""

    DEFAULT_CSS = """
    ProxyInfoDisplay {
        width: 100%;
        height: auto;
        padding: 1 2;
        background: $success 20%;
        border: round $success;
        margin: 1 0;
        display: none;
    }

    ProxyInfoDisplay.visible {
        display: block;
    }
    """

    def __init__(self, host: str, port: int, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._host = host
        self._port = port

    def compose(self) -> ComposeResult:
        yield Static(
            f"[bold green]Connected![/bold green]\n\n"
            f"SOCKS5 proxy available at:\n"
            f"[cyan]Host: {self._host}[/cyan]\n"
            f"[cyan]Port: {self._port}[/cyan]\n\n"
            f"Configure your browser to use this SOCKS5 proxy."
        )

    def show(self) -> None:
        """Show the proxy info."""
        self.add_class("visible")

    def hide(self) -> None:
        """Hide the proxy info."""
        self.remove_class("visible")


class ErrorDisplay(Static):
    """Widget to display errors."""

    DEFAULT_CSS = """
    ErrorDisplay {
        width: 100%;
        height: auto;
        padding: 1 2;
        background: $error 20%;
        border: round $error;
        margin: 1 0;
        display: none;
    }

    ErrorDisplay.visible {
        display: block;
    }
    """

    def set_error(self, message: str) -> None:
        """Set and show an error message."""
        self.update(f"[bold red]Error:[/bold red] {message}")
        self.add_class("visible")

    def clear(self) -> None:
        """Clear the error."""
        self.remove_class("visible")


class ClientApp(App):
    """Textual app for the Wayport client."""

    TITLE = "Wayport Client"
    CSS = """
    Screen {
        align: center middle;
    }

    #main-container {
        width: 60;
        height: auto;
        padding: 1 2;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "reconnect", "Reconnect"),
    ]

    def __init__(
        self,
        settings: ClientSettings | None = None,
        initial_code: str | None = None,
    ) -> None:
        super().__init__()
        self.settings = settings or ClientSettings()
        self.initial_code = initial_code
        self._client: WayportClient | None = None
        self._client_task: asyncio.Task[None] | None = None
        self._current_code: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            CodeInput(on_submit=self._on_code_submit),
            StatusDisplay(id="status-display"),
            ProxyInfoDisplay(
                host=self.settings.proxy_host,
                port=self.settings.proxy_port,
                id="proxy-info",
            ),
            ErrorDisplay(id="error-display"),
            id="main-container",
        )
        yield Footer()

    async def on_mount(self) -> None:
        """Handle app mount."""
        if self.initial_code:
            self._on_code_submit(self.initial_code)

    async def on_unmount(self) -> None:
        """Handle app unmount."""
        if self._client:
            await self._client.disconnect()
        if self._client_task:
            self._client_task.cancel()

    def _on_code_submit(self, code: str) -> None:
        """Handle code submission."""
        self._current_code = code
        self._start_connection(code)

    def _start_connection(self, code: str) -> None:
        """Start connecting to an exit node."""
        # Clear any previous error
        error_display = self.query_one("#error-display", ErrorDisplay)
        error_display.clear()

        # Hide proxy info
        proxy_info = self.query_one("#proxy-info", ProxyInfoDisplay)
        proxy_info.hide()

        # Update status
        status_display = self.query_one("#status-display", StatusDisplay)
        status_display.set_status("Connecting...")

        # Cancel existing connection
        if self._client_task:
            self._client_task.cancel()

        # Create new client and connect
        self._client = WayportClient(
            settings=self.settings,
            on_connected=self._on_connected,
            on_disconnected=self._on_disconnected,
            on_connection_status=self._on_connection_status,
            on_error=self._on_error,
        )
        self._client_task = asyncio.create_task(self._client.connect(code))

    def _on_connected(self, tunnel_id: str, peer_device_name: str) -> None:
        """Handle successful connection."""
        self.call_from_thread(self._update_connected, peer_device_name)

    def _on_disconnected(self, reason: str) -> None:
        """Handle disconnection."""
        self.call_from_thread(self._update_disconnected, reason)

    def _on_connection_status(self, status: str) -> None:
        """Handle status update."""
        status_map = {
            "connecting": "Connecting to relay...",
            "connected_to_relay": "Connected to relay, authenticating...",
            "tunnel_established": "Tunnel established!",
            "disconnected": "Disconnected",
        }
        message = status_map.get(status, status)
        self.call_from_thread(self._update_status, message)

    def _on_error(self, error_code: str, error_message: str) -> None:
        """Handle error."""
        self.call_from_thread(self._show_error, error_message)

    def _update_connected(self, peer_device_name: str) -> None:
        """Update UI for connected state."""
        status_display = self.query_one("#status-display", StatusDisplay)
        status_display.set_status("Connected", peer_device_name)

        proxy_info = self.query_one("#proxy-info", ProxyInfoDisplay)
        proxy_info.show()

    def _update_disconnected(self, reason: str) -> None:
        """Update UI for disconnected state."""
        status_display = self.query_one("#status-display", StatusDisplay)
        status_display.set_status(f"Disconnected: {reason}")

        proxy_info = self.query_one("#proxy-info", ProxyInfoDisplay)
        proxy_info.hide()

    def _update_status(self, message: str) -> None:
        """Update status message."""
        status_display = self.query_one("#status-display", StatusDisplay)
        status_display.set_status(message)

    def _show_error(self, message: str) -> None:
        """Show an error message."""
        error_display = self.query_one("#error-display", ErrorDisplay)
        error_display.set_error(message)

        status_display = self.query_one("#status-display", StatusDisplay)
        status_display.set_status("Connection failed")

    def action_reconnect(self) -> None:
        """Reconnect using the last code."""
        if self._current_code:
            self._start_connection(self._current_code)


def run_client_ui(
    settings: ClientSettings | None = None,
    code: str | None = None,
) -> None:
    """Run the client with TUI.

    Args:
        settings: Client settings
        code: Optional initial connection code
    """
    app = ClientApp(settings, initial_code=code)
    app.run()
