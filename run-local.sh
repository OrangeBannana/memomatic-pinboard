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
if ! python3 -c "import fastapi, uvicorn, PIL" 2>/dev/null; then
  echo "Python dependencies missing. Install them with:"
  echo ""
  echo "  On Ubuntu/WSL/Pi:"
  echo "    sudo apt install python3-fastapi python3-uvicorn python3-multipart python3-pil python3-qrcode"
  echo ""
  echo "  On macOS or a venv:"
  echo "    pip install -r requirements.txt"
  echo ""
  exit 1
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
  --host 0.0.0.0 \
  --port "$PORT" \
  --app-dir "$REPO_DIR/app"
