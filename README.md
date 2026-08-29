# Wayport

Share one machine's internet connection with another, over an encrypted tunnel.

Run `wayport share` on the machine whose connection you want to use, and
`wayport connect <code>` on the machine that should use it. A browser window
opens on the second machine whose traffic exits from the first. Nothing else on
that machine is affected unless you ask for it.

```
machine A                                machine B
─────────────────────────────────        ─────────────────────────────────
$ wayport share                          $ wayport connect blue-otter-42

  Device      johns-macbook                Connected to  johns-macbook
  Your code   blue-otter-42                Proxy         127.0.0.1:1080
                                           Exiting via   203.0.113.5
  ✓ A client connected.                    ✓ Opened a browser window.

                                           [s] system proxy  [b] browser
```

## Install

Python 3.11 or newer.

```bash
git clone https://github.com/graphcs/Wayport.git
cd Wayport
python3 -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e .
```

## First run

Once per machine:

```bash
wayport setup
```

This asks for the relay URL and token and saves them to `~/.config/wayport/config.toml`
(`%LOCALAPPDATA%\Wayport` on Windows) with owner-only permissions. Both machines
need the **same relay token**. After that, neither side needs command-line flags.

Check everything is in order:

```bash
wayport doctor
```

## What gets routed

There is no per-application proxying on macOS, Windows or Linux without a signed
system extension, so Wayport scopes traffic three ways instead. They compose.

| Mode | What uses the tunnel | How |
|---|---|---|
| **Browser** (default) | One dedicated Chrome window | `wayport connect <code>` |
| **Shell** | One terminal session | `wayport shell` |
| **System** | Everything on the machine | press `s`, or `--mode system` |

**Browser** is the default because it cannot break anything: your normal browser
keeps using your normal connection, and the tunnelled window has its own profile.
Good for a web app you want to reach from elsewhere.

**Shell** covers command-line tools — `curl`, `git`, `gcloud`, `aws`, anything
honouring proxy environment variables. Only that terminal is affected:

```bash
wayport shell            # in a second terminal, while connect is running
(wayport) $ gcloud ...   # goes through the tunnel
```

**System** routes the whole machine, including GUI apps that ignore environment
variables. Press `s` while connected to toggle it on and off without dropping the
tunnel. Previous settings are restored when you disconnect — including on Ctrl+C,
and on the next launch if the process is killed outright.

If something does go wrong, `wayport restore` puts your network settings back.

## Connection codes

Each machine has a stable code like `blue-otter-42`, so you can memorise it. It is
derived from a random key stored on that machine, not from its hostname — knowing
the machine's name tells you nothing about its code. Rotate with
`wayport share --new-code`.

Codes are matched loosely: `blue-otter-42`, `BLUE OTTER 42` and `BlueOtter42` all
work. They expire 24 hours after registration, and one exit node serves one client
at a time.

## Encryption

Set a shared secret during `wayport setup` and traffic is encrypted end to end with
AES-256-GCM, so the relay cannot read it. Both machines must use the same secret.

Without a secret the relay can see your traffic. It is a machine you control, but
set a secret anyway.

## Commands

| Command | What it does |
|---|---|
| `wayport share` | Share this machine's connection |
| `wayport connect <code>` | Use another machine's connection |
| `wayport shell` | Open a shell that uses a running tunnel |
| `wayport setup` | Save relay settings (`--show` to print them) |
| `wayport doctor` | Check configuration, relay, ports, browser |
| `wayport restore` | Undo system proxy settings after a crash |
| `wayport relay` | Run a relay server yourself |

Useful options: `--mode browser|system|none`, `--proxy-port auto`, `-v` for
diagnostics, `-vv` for more.

Configuration is read from, in order of precedence: command-line flags,
`WAYPORT_*` environment variables, `config.toml`, then built-in defaults.

## Running your own relay

The relay brokers connections; both machines dial out to it, so neither needs an
open inbound port. See [docs/deploy.md](docs/deploy.md) for deploying to Railway.

Always set `WAYPORT_RELAY_TOKEN`. Without it, anyone who finds the URL can use
your relay, and route traffic through whichever machine is sharing.

## Troubleshooting

**"Invalid or expired connection code"** — run `wayport share` on the other machine
and use the code it prints. It exits immediately rather than retrying, so this is
quick to spot.

**The browser loads nothing** — check `wayport doctor`. If the two machines have
different secrets, connecting fails with a clear message rather than hanging.

**Port 1080 already in use** — another client is running. Use `--proxy-port auto`.

**Corporate TLS interception (Zscaler and similar)** — Wayport uses the operating
system's trust store, so a corporate root CA already installed on the machine works
without extra configuration.

**Network settings look wrong after a crash** — `wayport restore`.

## Development

```bash
pip install -e ".[dev]"
ruff check src tests && ruff format --check src tests
mypy src/wayport
pytest -q
```

CI runs the same checks on macOS, Windows and Linux.

## License

MIT
