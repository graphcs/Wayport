#!/usr/bin/env bash
# Set up Wayport on this machine. Run once after cloning.
#
#   ./scripts/setup.sh
#   ./scripts/setup.sh --token abc123 --secret my-shared-secret
set -euo pipefail

RELAY_URL="wss://relay-production-587a.up.railway.app"
TOKEN=""
SECRET=""

while [ $# -gt 0 ]; do
    case "$1" in
        --relay-url) RELAY_URL="$2"; shift 2 ;;
        --token)     TOKEN="$2";     shift 2 ;;
        --secret)    SECRET="$2";    shift 2 ;;
        -h|--help)
            sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

cyan()  { printf '\n\033[36m==> %s\033[0m\n' "$1"; }
green() { printf '    \033[32m%s\033[0m\n' "$1"; }
amber() { printf '    \033[33m%s\033[0m\n' "$1"; }

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

printf '\n  \033[1mWayport setup\033[0m\n  \033[2m%s\033[0m\n' "$REPO_ROOT"

# --- 1. Python ------------------------------------------------------------
cyan "Looking for Python 3.11 or newer"
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        PYTHON="$candidate"
        green "Found $("$candidate" -c 'import sys; print("Python %d.%d.%d" % sys.version_info[:3])')"
        break
    fi
done
if [ -z "$PYTHON" ]; then
    echo "  Python 3.11 or newer is required and was not found." >&2
    echo "  macOS:  brew install python@3.12" >&2
    echo "  Linux:  sudo apt install python3.12 python3.12-venv" >&2
    exit 1
fi

# --- 2. Virtual environment ----------------------------------------------
cyan "Creating the virtual environment"
if [ -d .venv ]; then
    green "Reusing the existing .venv"
else
    "$PYTHON" -m venv .venv
    green "Created .venv"
fi
VENV_PY="$REPO_ROOT/.venv/bin/python"
WAYPORT="$REPO_ROOT/.venv/bin/wayport"

# --- 3. Install -----------------------------------------------------------
cyan "Installing Wayport (this takes a minute the first time)"
"$VENV_PY" -m pip install --quiet --upgrade pip
"$VENV_PY" -m pip install --quiet -e .
green "Installed"

# --- 4. Relay settings ----------------------------------------------------
cyan "Saving relay settings"
if [ -z "$TOKEN" ]; then
    if [ -f "$HOME/.wayport-relay-token" ]; then
        TOKEN="$(cat "$HOME/.wayport-relay-token")"
        green "Using the token from ~/.wayport-relay-token"
    else
        printf '    The relay token must match the other machine.\n'
        read -r -s -p "    Relay token: " TOKEN
        printf '\n'
    fi
fi
[ -n "$TOKEN" ] || { echo "  A relay token is required" >&2; exit 1; }

if [ -n "$SECRET" ]; then
    "$WAYPORT" setup --relay-url "$RELAY_URL" --relay-token "$TOKEN" --secret "$SECRET"
else
    "$WAYPORT" setup --relay-url "$RELAY_URL" --relay-token "$TOKEN"
fi

# --- 5. Check -------------------------------------------------------------
cyan "Checking everything works"
"$WAYPORT" doctor

# --- 6. Next steps --------------------------------------------------------
printf '\n  \033[1mDone. Use Wayport with:\033[0m\n\n'
printf '    \033[32m./.venv/bin/wayport share\033[0m            \033[2m# share this connection\033[0m\n'
printf '    \033[32m./.venv/bin/wayport connect <code>\033[0m   \033[2m# use the other machine'"'"'s\033[0m\n\n'
printf '  \033[2mOr activate the environment first, then just '"'"'wayport'"'"':\033[0m\n'
printf '    \033[2msource .venv/bin/activate\033[0m\n\n'

if [ -z "$SECRET" ]; then
    amber "No shared secret set, so the relay can read your traffic."
    amber "Set the same one on both machines with:  wayport setup --secret <value>"
    printf '\n'
fi
