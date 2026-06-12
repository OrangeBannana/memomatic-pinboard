#!/usr/bin/env python3
"""
Touch bridge: GPIO17 polling + ADS7846 coordinate reading via C helper.

The ADS7846 kernel driver is UNBOUND (by ExecStartPre) so it cannot issue
spi_sync() on touch, which would timeout because fbcp-ili9341 holds the SPI
hardware via /dev/mem direct register access.

Detection:   poll /sys/class/gpio/gpio529/value (active-low T_IRQ)
Coordinates: call spi_touch_read (compiled C binary) which busy-waits for the
             fbcp inter-frame gap then reads ADS7846 X,Y in < 200 µs.
             Python cannot catch the ~2 ms inter-frame window reliably (GIL +
             interpreter overhead); C busy-wait can.
Injection:   xdotool mousemove X Y mousedown 1  (on touch-down)
             xdotool mouseup 1                  (on touch-up)
             Sending real mousedown/mouseup lets frame.html detect hold duration
             for long-press gestures.

Fallback:    if spi_touch_read fails, coordinates default to (240, 100) —
             above the menu panel, usable for basic menu toggle.
"""
import os, subprocess, time, logging, threading

logging.basicConfig(level=logging.INFO, format="%(asctime)s touch_bridge: %(message)s")
log = logging.getLogger(__name__)

GPIO_PIN  = os.environ.get("GPIO_TOUCH_PIN", "529")
GPIO_PATH = f"/sys/class/gpio/gpio{GPIO_PIN}/value"
XENV      = {"DISPLAY":    os.environ.get("DISPLAY",    ":0"),
             "XAUTHORITY": os.environ.get("XAUTHORITY", "/root/.Xauthority"),
             **os.environ}

# C SPI helper — compiled by ExecStartPre in pinboard-touch.service
HELPER_BIN  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spi_touch_read")
FALLBACK_XY = (240, 100)   # above menu panel, works for basic toggle

DEBOUNCE_SEC  = 0.3    # minimum gap between complete gestures
POLL_INTERVAL = 0.02   # 20 ms GPIO poll


def _ensure_gpio():
    vp = f"/sys/class/gpio/gpio{GPIO_PIN}/value"
    if not os.path.exists(vp):
        try:
            open("/sys/class/gpio/export", "w").write(GPIO_PIN)
            time.sleep(0.1)
        except OSError:
            pass
    dp = f"/sys/class/gpio/gpio{GPIO_PIN}/direction"
    if os.path.exists(dp):
        try:
            open(dp, "w").write("in")
        except OSError:
            pass


def _get_coords():
    """Call the C SPI helper; return (sx, sy) or None on failure.

    One retry: if fbcp happens to start a frame mid-read, the samples come
    back corrupt and the helper prints "err" — the second attempt lands in
    clean bus state.  Failures are logged with their reason so the journal
    explains every fallback coordinate (issue #25)."""
    if not os.path.exists(HELPER_BIN):
        return None
    reason = "unknown"
    for attempt in (1, 2):
        try:
            r = subprocess.run([HELPER_BIN], capture_output=True, timeout=0.5)
            if r.returncode == 0:
                parts = r.stdout.decode().strip().split()
                if len(parts) == 2:
                    sx, sy = int(parts[0]), int(parts[1])
                    if 0 <= sx < 480 and 0 <= sy < 320:
                        return sx, sy
                reason = "bad output %r" % r.stdout.decode().strip()
            else:
                reason = "err (filtered/no samples)"
        except subprocess.TimeoutExpired:
            reason = "timeout >0.5s"
        except Exception as e:
            reason = str(e)
    log.info("spi helper failed twice (%s)", reason)
    return None


def _xdotool(*args):
    r = subprocess.run(["xdotool", *args], env=XENV, capture_output=True)
    if r.returncode != 0:
        log.warning("xdotool %s: %s", " ".join(args), r.stderr.decode().strip())
    return r.returncode == 0


def gpio_poll_thread():
    log.info("gpio: starting, path=%s", GPIO_PATH)
    _ensure_gpio()

    if not os.path.exists(GPIO_PATH):
        log.error("gpio: %s not found — is ads7846 driver unbound?", GPIO_PATH)
        return

    helper_ok = os.path.exists(HELPER_BIN)
    log.info("spi helper: %s", "found" if helper_ok else "NOT found — using fallback position")

    log.info("gpio: polling (0=touching, 1=released)")
    prev          = 1
    gesture_start = 0.0
    last_up_time  = 0.0

    while True:
        try:
            val = int(open(GPIO_PATH).read().strip())
        except (OSError, ValueError):
            time.sleep(0.5)
            continue

        now = time.time()

        if prev == 1 and val == 0:
            # Touch-down: read coordinates and press mouse button
            if now - last_up_time >= DEBOUNCE_SEC:
                gesture_start = now
                coords = _get_coords()
                if coords:
                    sx, sy = coords
                    log.info("touch-down at (%d, %d)", sx, sy)
                else:
                    sx, sy = FALLBACK_XY
                    log.info("touch-down (fallback %d,%d)", sx, sy)
                _xdotool("mousemove", str(sx), str(sy), "mousedown", "1")
            else:
                gesture_start = 0.0   # debounced — skip this gesture

        elif prev == 0 and val == 1:
            # Touch-up: release mouse button
            if gesture_start > 0:
                held = now - gesture_start
                log.info("touch-up (held %.2f s) → mouseup", held)
                _xdotool("mouseup", "1")
                last_up_time  = now
                gesture_start = 0.0

        prev = val
        time.sleep(POLL_INTERVAL)


def main():
    log.info("starting (GPIO%s polling + C SPI coords)", GPIO_PIN)
    t = threading.Thread(target=gpio_poll_thread, daemon=True, name="gpio-poll")
    t.start()
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
