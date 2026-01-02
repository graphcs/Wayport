# Wayport

Internet sharing application using SOCKS5 proxy with WebSocket relay.

## Quick Start

```bash
# Create virtual environment
python3 -m venv .venv

# Activate it
source .venv/bin/activate
# Install
pip install -e .

# Terminal 1: Start relay server
wayport relay

# Terminal 2: Start exit node (shares internet)
wayport server
# Note the connection code displayed (e.g., ABC123)

# Terminal 3: Start client
wayport client ABC123
# Configure browser to use SOCKS5 proxy at 127.0.0.1:1080
```

## Architecture

```
┌─────────────┐     WebSocket      ┌─────────────┐     WebSocket      ┌─────────────┐
│   CLIENT    │◄──────────────────►│   RELAY     │◄──────────────────►│  EXIT NODE  │
│   (macOS)   │                    │  (Cloud)    │                    │ (Win/macOS) │
└─────────────┘                    └─────────────┘                    └─────────────┘
      │
      ▼
  Browser uses
  SOCKS5 proxy
```

## Commands

### Exit Node (Server)
Shares your internet connection:
```bash
wayport server [--relay-url URL] [--device-name NAME] [--no-tui]
```

### Client
Connects through an exit node:
```bash
wayport client [CODE] [--relay-url URL] [--proxy-port PORT] [--no-tui]
```

### Relay
Runs the central relay server:
```bash
wayport relay [--host HOST] [--port PORT]
```

## Configuration

Environment variables:
- `WAYPORT_RELAY_HOST` - Relay server host (default: 0.0.0.0)
- `WAYPORT_RELAY_PORT` - Relay server port (default: 8080)
- `WAYPORT_EXITNODE_RELAY_URL` - Relay URL for exit node
- `WAYPORT_CLIENT_RELAY_URL` - Relay URL for client
- `WAYPORT_CLIENT_PROXY_PORT` - Local SOCKS5 proxy port (default: 1080)
