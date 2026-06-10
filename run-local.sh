#!/bin/sh
# run-local.sh — start Memomatic Pinboard locally for development/testing.
#
# Works on Linux, WSL2 (Ubuntu), and macOS.  Does NOT require a Raspberry Pi.
# Wi-Fi API endpoints return canned stub data via local/bin/nmcli + local/bin/sudo.
#
# Usage:
#   ./run-local.sh              # start on default port 8080
#   PORT=9000 ./run-local.sh    # start on a custom port
#
# Then open:
#   http://127.0.0.1:${PORT:-8080}/admin   (owner token: dev)
#   http://127.0.0.1:${PORT:-8080}/frame
#   http://127.0.0.1:${PORT:-8080}/guest/<token>   (enable guest in /admin first)

set -eu

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-8080}"
PINBOARD_HOME="${PINBOARD_HOME:-/tmp/pinboard-local}"
PINBOARD_OWNER_TOKEN="${PINBOARD_OWNER_TOKEN:-dev}"

# ── dependency check ──────────────────────────────────────────────────────────
# PIL is the import name for the Pillow package; fastapi/uvicorn are their own names.
if ! python3 -c "import fastapi, uvicorn, PIL" 2>/dev/null; then
  echo "Installing Python dependencies (fastapi, uvicorn, Pillow, ...)..."
  python3 -m pip install -r "$REPO_DIR/requirements.txt"
fi

# ── ensure data directories exist (app.py mounts them at import time) ─────────
mkdir -p "$PINBOARD_HOME/data" \
         "$PINBOARD_HOME/images/originals" \
         "$PINBOARD_HOME/images/display"

# ── prepend stub helpers to PATH ──────────────────────────────────────────────
# local/bin/sudo  intercepts "sudo /usr/bin/nmcli ..." calls from app.py
# local/bin/nmcli returns canned Wi-Fi scan / connect responses
export PATH="$REPO_DIR/local/bin:$PATH"

echo "---"
echo "Memomatic Pinboard — local dev server"
echo "  Data dir   : $PINBOARD_HOME"
echo "  Owner token: $PINBOARD_OWNER_TOKEN"
echo "  URL        : http://127.0.0.1:$PORT/admin"
echo "---"

export PINBOARD_HOME
export PINBOARD_OWNER_TOKEN

exec python3 -m uvicorn app:app \
  --reload \
  --port "$PORT" \
  --app-dir "$REPO_DIR/app"
