#!/bin/sh
# touch_diag.sh — one-shot diagnostic for the on-device touch system (issue #25).
#
# Run on the Pi:  sudo sh /home/memomatic/pinboard/app/touch_diag.sh
#
# Collects every known failure point of the touch chain (see CLAUDE.md
# "On-device touch system") into one report so a session without hardware
# access can triage from the pasted output. Read-only except for one test
# compile into /tmp.

APP_DIR="${APP_DIR:-/home/memomatic/pinboard/app}"
SRC="$APP_DIR/spi_touch_read.c"
BIN="$APP_DIR/spi_touch_read"

section() {
  echo ""
  echo "=== $1 ==="
}

section "1. Services"
for svc in fbcp-ili9341 pinboard-touch pinboard-kiosk pinboard-app; do
  printf '%-18s active=%s enabled=%s\n' "$svc" \
    "$(systemctl is-active "$svc.service" 2>/dev/null)" \
    "$(systemctl is-enabled "$svc.service" 2>/dev/null)"
done
pgrep -af fbcp-ili9341 || echo "fbcp-ili9341 process NOT running"
pgrep -af touch_bridge || echo "touch_bridge.py process NOT running"

section "2. Toolchain + helper binary"
if command -v gcc >/dev/null 2>&1; then
  echo "gcc: $(gcc --version | head -1)"
else
  echo "gcc: NOT INSTALLED  <-- helper can never be (re)built; install build-essential"
fi
for f in "$SRC" "$BIN"; do
  if [ -e "$f" ]; then
    echo "present: $f  ($(stat -c '%y' "$f" 2>/dev/null | cut -d. -f1))"
  else
    echo "MISSING: $f"
  fi
done
if [ -e "$SRC" ] && [ -e "$BIN" ] && [ "$SRC" -nt "$BIN" ]; then
  echo "WARNING: source is NEWER than binary — service compile step is failing or skipped"
fi
if command -v gcc >/dev/null 2>&1 && [ -e "$SRC" ]; then
  if gcc -O2 -o /tmp/spi_touch_read_diag "$SRC" 2>/tmp/spi_touch_read_diag.err; then
    echo "test compile: OK"
  else
    echo "test compile: FAILED:"
    cat /tmp/spi_touch_read_diag.err
  fi
  rm -f /tmp/spi_touch_read_diag /tmp/spi_touch_read_diag.err
fi

section "3. Kernel driver / GPIO state"
if [ -e /sys/bus/spi/devices/spi0.1/driver ]; then
  echo "spi0.1 driver: $(basename "$(readlink /sys/bus/spi/devices/spi0.1/driver)")  <-- ads7846 must be UNBOUND while fbcp runs"
else
  echo "spi0.1 driver: none (ads7846 unbound — correct)"
fi
if [ -e /sys/class/gpio/gpio529/value ]; then
  echo "gpio529 (T_IRQ): exported, value=$(cat /sys/class/gpio/gpio529/value) (1=released, 0=touching)"
else
  echo "gpio529 (T_IRQ): NOT exported  <-- touch_bridge cannot detect touches"
fi
if command -v raspi-gpio >/dev/null 2>&1; then
  raspi-gpio get 7 17 2>/dev/null   # GPIO7=CS1 (needs ALT0 during reads), GPIO17=T_IRQ
fi

section "4. Boot config (/boot/firmware/config.txt or /boot/config.txt)"
for cfg in /boot/firmware/config.txt /boot/config.txt; do
  [ -f "$cfg" ] || continue
  echo "-- $cfg"
  grep -nE "^(gpu_mem|dtoverlay|dtparam|disable_splash|hdmi_)" "$cfg"
done
if command -v vcgencmd >/dev/null 2>&1; then
  echo "effective GPU memory: $(vcgencmd get_mem gpu 2>/dev/null)"
  echo "(issue #25 fix expects gpu=64M; gpu=16M is the suspected regression)"
fi

section "5. Live coordinate read (spi_touch_read)"
if [ -x "$BIN" ]; then
  echo "Three reads. Untouched the screen reads 'err' (rail values filtered);"
  echo "HOLD A FINGER ON THE SCREEN during the next 6 seconds to get coordinates."
  for i in 1 2 3; do
    sleep 2
    echo "-- read $i:"
    "$BIN" 2>&1
  done
  echo "(stderr lines show ta_seen + raw ADC samples; ta_seen=0 means fbcp's"
  echo " SPI frame was never detected; all-zero bytes mean CS1/GPIO7 is not ALT0)"
else
  echo "skipped — $BIN missing or not executable"
fi

section "6. X / input injection"
echo "DISPLAY test (xdotool needs Xorg up):"
DISPLAY=:0 XAUTHORITY=/root/.Xauthority xdotool getdisplaygeometry 2>&1 || echo "xdotool failed — kiosk/Xorg not ready?"

section "7. Recent pinboard-touch journal"
journalctl -u pinboard-touch.service -n 40 --no-pager 2>/dev/null || echo "journalctl unavailable"

echo ""
echo "=== done — paste this entire output into issue #25 ==="
