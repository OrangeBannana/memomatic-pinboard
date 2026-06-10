#!/bin/sh
set -eu

if command -v xset >/dev/null 2>&1; then
  xset s off || true
  xset -dpms || true
  xset s noblank || true
fi

export XDG_RUNTIME_DIR=/tmp/pinboard-runtime
mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

exec /usr/lib/chromium/chromium \
  --kiosk \
  --no-sandbox \
  --noerrdialogs \
  --disable-dev-shm-usage \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --check-for-update-interval=31536000 \
  --touch-events=enabled \
  --disable-gpu \
  --remote-debugging-port=9222 \
  --remote-debugging-address=0.0.0.0 \
  --user-data-dir=/home/memomatic/pinboard/chromium-profile \
  --app=http://127.0.0.1:8080/frame
