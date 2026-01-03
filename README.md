# Wayport

Share your internet connection between computers. One computer (the "exit node") shares its internet, and other computers (clients) can browse the web through it.

**Use case:** Access IP-restricted websites from a different computer by routing traffic through a machine that has access.

## How It Works

```
Your Computer                    Relay Server                   Exit Node
(Client)                         (in the cloud)                 (shares internet)
    |                                 |                              |
    |-------- connects to ----------->|<-------- connects to --------|
    |                                 |                              |
    | Browser traffic flows through the tunnel to exit node          |
    |================================================================|
```

Your browser connects to a local proxy on your computer. Traffic goes through the relay to the exit node, which makes the actual internet requests. Websites see the exit node's IP address.

---

## Setup Instructions

### Step 1: Install Python

**Mac:**
Python 3 is usually pre-installed. Check with:
```bash
python3 --version
```
If not installed, get it from https://www.python.org/downloads/

**Windows:**
1. Download Python from https://www.python.org/downloads/
2. Run the installer
3. **IMPORTANT:** Check the box "Add Python to PATH" during installation

---

### Step 2: Download Wayport

```bash
git clone https://github.com/graphcs/Wayport.git
cd Wayport
```

Or download and extract the ZIP from GitHub.

---

### Step 3: Create Virtual Environment

**Mac (Terminal):**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

**Windows (Command Prompt):**
```cmd
python -m venv .venv
.venv\Scripts\activate
```

**Windows (PowerShell):**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

If PowerShell gives an error about scripts being disabled, run this first:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

You'll know it worked when you see `(.venv)` at the start of your command line.

---

### Step 4: Install Wayport

```bash
pip install -e .
```

**Windows only:** Also install colorama for colored output:
```bash
pip install colorama
```

---

## Running Wayport

You need 3 components running:

### 1. Relay Server (run on a server both machines can reach)

```bash
wayport relay
```

This starts on port 8080 by default. For a cloud server, make sure port 8080 is open.

### 2. Exit Node (run on the computer sharing internet)

```bash
wayport server --relay-url ws://RELAY_IP:8080
```

Replace `RELAY_IP` with:
- `localhost` if relay is on the same machine
- The IP address or domain of your relay server

You'll see a **connection code** like `ABC123`. Share this with the client.

**Tip:** Use `--code MYCODE` to request a specific code:
```bash
wayport server --relay-url ws://RELAY_IP:8080 --code MYCODE
```

### 3. Client (run on the computer that wants to use the shared internet)

```bash
wayport client ABC123 --relay-url ws://RELAY_IP:8080
```

Replace `ABC123` with the code from the exit node.

---

## Configure Your Browser

Once connected, configure your browser to use the SOCKS5 proxy:

**Firefox:**
1. Settings → General → Network Settings → Settings
2. Select "Manual proxy configuration"
3. SOCKS Host: `127.0.0.1`
4. Port: `1080`
5. Select "SOCKS v5"
6. Check "Proxy DNS when using SOCKS v5" (important!)
7. Click OK

**Chrome (Mac):**
Chrome uses system proxy settings, or use an extension like "Proxy SwitchyOmega"

**Chrome (Windows):**
Settings → System → Open your computer's proxy settings, or use an extension

---

## Quick Test (All on One Machine)

Open 3 terminal windows:

**Terminal 1 - Relay:**
```bash
cd Wayport
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
wayport relay
```

**Terminal 2 - Exit Node:**
```bash
cd Wayport
source .venv/bin/activate
wayport server
```
Note the code shown (e.g., `ABC123`)

**Terminal 3 - Client:**
```bash
cd Wayport
source .venv/bin/activate
wayport client ABC123
```

Then configure your browser to use SOCKS5 proxy at `127.0.0.1:1080`

---

## All Command Options

### Relay Server
```bash
wayport relay [OPTIONS]

Options:
  --host HOST        IP to bind to (default: 0.0.0.0)
  --port PORT        Port to bind to (default: 8080)
  --log-level LEVEL  DEBUG, INFO, WARNING, ERROR (default: INFO)
```

### Exit Node (Server)
```bash
wayport server [OPTIONS]

Options:
  --relay-url URL      Relay server URL (default: ws://localhost:8080)
  --device-name NAME   Name shown to clients
  --code CODE          Preferred connection code
  --log-level LEVEL    DEBUG, INFO, WARNING, ERROR (default: INFO)
```

### Client
```bash
wayport client [CODE] [OPTIONS]

Options:
  CODE                 Connection code from exit node
  --relay-url URL      Relay server URL (default: ws://localhost:8080)
  --proxy-port PORT    Local SOCKS5 proxy port (default: 1080)
  --log-level LEVEL    DEBUG, INFO, WARNING, ERROR (default: INFO)
```

---

## Status Display

While running, you'll see a status line that updates every 5 seconds:

```
[OK] Code: ABC123 | Relay: CONNECTED | CLIENT | Sent: 4.5KB | Recv: 0.6KB
```

- `[OK]` = Everything working
- `[~]` = Partially connected (waiting)
- `[!]` = Disconnected (will auto-reconnect)

---

## Troubleshooting

**"Address already in use" error:**
Another process is using the port. Kill it:
```bash
# Mac/Linux
pkill -f wayport

# Windows
taskkill /F /IM python.exe
```

**Client can't connect:**
- Make sure relay URL is correct and reachable
- Check firewall allows the relay port
- Verify the connection code is correct

**Browser not working through proxy:**
- Make sure "Proxy DNS when using SOCKS v5" is checked in Firefox
- Try visiting http://httpbin.org/ip to check your exit IP

**SSL certificate errors (with corporate proxy like Zscaler):**
You may need to install the corporate root CA certificate on your client machine.

---

## Environment Variables

Instead of command-line options, you can use environment variables:

```bash
WAYPORT_RELAY_HOST=0.0.0.0
WAYPORT_RELAY_PORT=8080
WAYPORT_EXITNODE_RELAY_URL=ws://relay.example.com:8080
WAYPORT_CLIENT_RELAY_URL=ws://relay.example.com:8080
WAYPORT_CLIENT_PROXY_PORT=1080
```
