#!/usr/bin/env python3
"""
touch_test.py  —  Standalone touch validator for Memomatic Pi
==============================================================
Ground-up touchscreen tester. Does NOT use xdotool or X11 — purely
validates the hardware detection layer independently of the main app.

Run as root:
    sudo python3 app/touch_test.py              # system check + both methods (30s each)
    sudo python3 app/touch_test.py --method evdev
    sudo python3 app/touch_test.py --method gpio
    sudo python3 app/touch_test.py --duration 60
    sudo python3 app/touch_test.py --no-fb      # skip framebuffer visualisation

Output: verbose console log + optional RGB565 crosshair on /dev/fb0.
Each touch event prints a timestamped line with raw ADC values and
computed screen coordinates so calibration problems are easy to spot.
"""

import argparse
import errno
import fcntl
import mmap
import os
import select
import struct
import subprocess
import sys
import time

# ── Hardware constants ────────────────────────────────────────────────────────

FB_DEV     = "/dev/fb0"
FB_W       = 480
FB_H       = 320

# GPIO17 = T_IRQ (active-low pen-down signal from ADS7846).
# On this Pi gpiochip0 base = 512, so BCM17 = sysfs gpio 529.
GPIO_BCM   = 17
GPIO_BASE  = 512
GPIO_SYSFS = GPIO_BASE + GPIO_BCM   # 529

EVDEV_DIR  = "/dev/input"

# spi_touch_read binary lives next to this file; compiled from spi_touch_read.c
HELPER     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spi_touch_read")
HELPER_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spi_touch_read.c")

# ADS7846 calibration — matches /etc/X11/xorg.conf.d/99-calibration.conf
# Option "Calibration" "3936 227 268 3880"  +  Option "SwapAxes" "1"
#
# When reading raw evdev (before X11):
#   ABS_Y carries physical Y channel (CMD_Y 0x90) → mapped to screen X: 268..3880 → 0..479
#   ABS_X carries physical X channel (CMD_X 0xD0) → mapped to screen Y: 3936..227 → 0..319
#
# When using spi_touch_read the calibration is already applied; output is screen coords.
CAL_SCREEN_X_RAW_MIN = 268
CAL_SCREEN_X_RAW_MAX = 3880
CAL_SCREEN_Y_RAW_MIN = 3936
CAL_SCREEN_Y_RAW_MAX = 227   # < MIN because axis is inverted

# Linux evdev constants
EV_FMT    = "llHHi"
EV_SIZE   = struct.calcsize(EV_FMT)
EV_KEY    = 1
EV_ABS    = 3
BTN_TOUCH = 0x14A
ABS_X     = 0
ABS_Y     = 1
EVIOCGRAB = 0x40044590

# RGB565 palette
BLACK  = 0x0000
DKGRAY = 0x2104
GRID   = 0x4208
WHITE  = 0xFFFF
RED    = 0xF800
GREEN  = 0x07E0
YELLOW = 0xFFE0
CYAN   = 0x07FF

# ── Terminal colours ──────────────────────────────────────────────────────────
GRN = "\033[92m"; RED_T = "\033[91m"; YLW = "\033[93m"
CYN = "\033[96m"; WHT   = "\033[97m"; RST = "\033[0m"

def ok(s):   print(f"  {GRN}✓{RST}  {s}")
def fail(s): print(f"  {RED_T}✗{RST}  {s}")
def warn(s): print(f"  {YLW}!{RST}  {s}")
def info(s): print(f"     {s}")
def hdr(s):  print(f"\n{CYN}── {s} {'─' * max(0, 54 - len(s))}{RST}")


# ── Framebuffer ───────────────────────────────────────────────────────────────

class FB:
    """Minimal RGB565 framebuffer writer."""

    def __init__(self):
        self._mm = None
        try:
            f = open(FB_DEV, "r+b")
            self._mm = mmap.mmap(f.fileno(), FB_W * FB_H * 2)
            f.close()
            self.fill(DKGRAY)
            ok(f"Framebuffer: {FB_DEV}  ({FB_W}×{FB_H} RGB565)")
        except Exception as e:
            warn(f"Framebuffer unavailable: {e}")

    @property
    def ok(self):
        return self._mm is not None

    def _put(self, x, y, color):
        if self._mm and 0 <= x < FB_W and 0 <= y < FB_H:
            self._mm.seek((y * FB_W + x) * 2)
            self._mm.write(struct.pack("<H", color))

    def fill(self, color=DKGRAY):
        if self._mm:
            self._mm.seek(0)
            self._mm.write(struct.pack("<H", color) * (FB_W * FB_H))

    def grid(self, step=40, color=GRID):
        """Draw a faint reference grid so touch position is easy to judge."""
        for x in range(0, FB_W, step):
            for y in range(FB_H):
                self._put(x, y, color)
        for y in range(0, FB_H, step):
            for x in range(FB_W):
                self._put(x, y, color)

    def crosshair(self, x, y, color=RED, arm=16):
        """Full-arm crosshair with a bright centre dot."""
        for dx in range(-arm, arm + 1):
            self._put(x + dx, y, color)
        for dy in range(-arm, arm + 1):
            self._put(x, y + dy, color)
        for dx in range(-3, 4):
            for dy in range(-3, 4):
                self._put(x + dx, y + dy, WHITE)

    def dot(self, x, y, color=YELLOW, r=4):
        """Small filled circle — used to mark touch-up position."""
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                if dx * dx + dy * dy <= r * r:
                    self._put(x + dx, y + dy, color)

    def close(self):
        if self._mm:
            self._mm.close()
            self._mm = None


# ── GPIO helpers ──────────────────────────────────────────────────────────────

def _gpio_value_path(n=GPIO_SYSFS):
    return f"/sys/class/gpio/gpio{n}/value"


def ensure_gpio(n=GPIO_SYSFS):
    """Export the GPIO sysfs node if not already done. Returns (ok, msg)."""
    vp = _gpio_value_path(n)
    if os.path.exists(vp):
        return True, "already exported"
    try:
        with open("/sys/class/gpio/export", "w") as f:
            f.write(str(n))
        time.sleep(0.15)
    except OSError as e:
        return False, str(e)
    try:
        with open(f"/sys/class/gpio/gpio{n}/direction", "w") as f:
            f.write("in")
    except OSError:
        pass
    return os.path.exists(vp), ""


def read_gpio(n=GPIO_SYSFS):
    """Return 0/1 or -1 on error."""
    try:
        return int(open(_gpio_value_path(n)).read().strip())
    except (OSError, ValueError):
        return -1


# ── Calibration ───────────────────────────────────────────────────────────────

def raw_to_screen(abs_y_raw, abs_x_raw):
    """
    Apply xorg calibration to raw evdev ABS values.
    ABS_Y (physical Y channel) → screen X
    ABS_X (physical X channel) → screen Y  (inverted range)
    Returns (sx, sy) clamped to screen bounds.
    """
    sx = int((abs_y_raw - CAL_SCREEN_X_RAW_MIN) /
             (CAL_SCREEN_X_RAW_MAX - CAL_SCREEN_X_RAW_MIN) * FB_W)
    sy = int((abs_x_raw - CAL_SCREEN_Y_RAW_MIN) /
             (CAL_SCREEN_Y_RAW_MAX - CAL_SCREEN_Y_RAW_MIN) * FB_H)
    return max(0, min(FB_W - 1, sx)), max(0, min(FB_H - 1, sy))


# ── spi_touch_read wrapper ────────────────────────────────────────────────────

def compile_helper():
    """Try to compile spi_touch_read.c. Returns (success, message)."""
    if not os.path.exists(HELPER_SRC):
        return False, f"source not found: {HELPER_SRC}"
    r = subprocess.run(
        ["gcc", "-O2", "-o", HELPER, HELPER_SRC],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return False, r.stderr.strip()
    return True, "compiled ok"


def call_spi_touch():
    """
    Run spi_touch_read binary. Returns (sx, sy) in screen coords or None.
    The binary already applies calibration; output is "sx sy\\n".
    """
    try:
        r = subprocess.run([HELPER], capture_output=True, timeout=0.5)
        if r.returncode == 0:
            parts = r.stdout.decode().strip().split()
            if len(parts) == 2:
                sx, sy = int(parts[0]), int(parts[1])
                if 0 <= sx < FB_W and 0 <= sy < FB_H:
                    return sx, sy
    except Exception:
        pass
    return None


# ── System check ──────────────────────────────────────────────────────────────

def system_check():
    hdr("System")
    info(f"Platform: {os.uname().machine}   Kernel: {os.uname().release}")

    if os.geteuid() != 0:
        fail("Not running as root — GPIO and /dev/mem access will fail")
    else:
        ok("Running as root")

    hdr(f"GPIO T_IRQ  (BCM{GPIO_BCM} → sysfs gpio{GPIO_SYSFS})")
    exported, msg = ensure_gpio()
    if exported:
        val = read_gpio()
        state = {0: f"{GRN}TOUCH ACTIVE{RST}", 1: "idle (no touch)"}.get(val, "read error")
        ok(f"gpio{GPIO_SYSFS} exported  value={val}  ({state})")
        info(f"  ({msg})")
    else:
        fail(f"Cannot export gpio{GPIO_SYSFS}: {msg}")
        warn("The ads7846 kernel driver may still own this GPIO pin.")

    hdr("ADS7846 kernel driver binding")
    bound = os.path.exists("/sys/bus/spi/drivers/ads7846/spi0.1")
    if bound:
        warn("ads7846 driver IS bound to spi0.1")
        info("→ evdev method SHOULD work (kernel driver is active, events go to /dev/input)")
        info("→ GPIO+SPI method WILL CONFLICT with the driver over the SPI bus")
        info("  To use GPIO+SPI: echo spi0.1 > /sys/bus/spi/drivers/ads7846/unbind")
    else:
        ok("ads7846 driver is NOT bound to spi0.1")
        info("→ GPIO+SPI method should work (touch_bridge.py path)")
        info("→ evdev method will NOT produce events (no kernel driver to generate them)")

    hdr("spi_touch_read binary")
    if os.path.exists(HELPER):
        ok(f"Binary found: {HELPER}")
        # Quick sanity test (should print 'err' when not touching, not crash)
        try:
            r = subprocess.run([HELPER], capture_output=True, timeout=0.5)
            out = r.stdout.decode().strip()
            info(f"  Test run output: {out!r}  (rc={r.returncode})  "
                 f"— 'err' is expected when not touching")
        except Exception as e:
            warn(f"  Test run failed: {e}")
    else:
        warn(f"Binary not found: {HELPER}")
        if os.path.exists(HELPER_SRC):
            info("  Attempting to compile from source...")
            ok_c, msg_c = compile_helper()
            if ok_c:
                ok(f"  {msg_c}")
            else:
                fail(f"  Compile failed: {msg_c}")
        else:
            fail(f"  Source also missing: {HELPER_SRC}")

    hdr("fbcp-ili9341 display driver")
    r = subprocess.run(["pgrep", "-x", "fbcp"], capture_output=True, text=True)
    if r.returncode == 0:
        ok(f"fbcp running (pid {r.stdout.strip()})  — spi_touch_read will sync to frame gaps")
    else:
        warn("fbcp NOT running — display may be blank; spi_touch_read will attempt reads anyway")

    hdr(f"Input devices ({EVDEV_DIR})")
    try:
        events = sorted(e for e in os.listdir(EVDEV_DIR) if e.startswith("event"))
        if events:
            for ev in events:
                np = f"/sys/class/input/{ev}/device/name"
                name = open(np).read().strip() if os.path.exists(np) else "unknown"
                marker = f"  {GRN}← likely touch{RST}" if any(
                    k in name.lower() for k in ("ads7846", "touch", "pen")) else ""
                ok(f"{EVDEV_DIR}/{ev}  →  {name}{marker}")
        else:
            warn("No event devices found in /dev/input")
    except OSError as e:
        fail(f"Cannot list {EVDEV_DIR}: {e}")


# ── Method A: evdev ───────────────────────────────────────────────────────────

def run_evdev(fb, duration):
    hdr(f"Method A: evdev  ({duration}s — touch the screen now)")

    # Find the touch device by name, fall back to event0
    device = None
    try:
        for ev in sorted(os.listdir(EVDEV_DIR)):
            if not ev.startswith("event"):
                continue
            np = f"/sys/class/input/{ev}/device/name"
            name = open(np).read().strip() if os.path.exists(np) else ""
            if any(k in name.lower() for k in ("ads7846", "touch", "pen")):
                device = f"{EVDEV_DIR}/{ev}"
                ok(f"Using {device}  ({name})")
                break
    except OSError:
        pass

    if not device:
        device = f"{EVDEV_DIR}/event0"
        warn(f"No named touch device found — trying {device}")

    try:
        fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as e:
        fail(f"Cannot open {device}: {e}")
        return 0

    # Test for Xorg EVIOCGRAB
    try:
        fcntl.ioctl(fd, EVIOCGRAB, struct.pack("I", 1))
        fcntl.ioctl(fd, EVIOCGRAB, struct.pack("I", 0))
        ok("Device is free (not grabbed by Xorg)")
    except OSError as e:
        if e.errno == errno.EBUSY:
            fail("Device IS GRABBED by Xorg — reads will be empty")
            info('Fix: add Option "GrabDevice" "off" in xorg InputClass for this device')
            os.close(fd)
            return 0
        warn(f"EVIOCGRAB: unexpected errno {e.errno}")

    print()
    info("Raw ADS7846 values are 0–4095. Calibration note:")
    info(f"  ABS_Y (physical Y channel) → screen X  "
         f"(raw {CAL_SCREEN_X_RAW_MIN}..{CAL_SCREEN_X_RAW_MAX} → 0..{FB_W - 1})")
    info(f"  ABS_X (physical X channel) → screen Y  "
         f"(raw {CAL_SCREEN_Y_RAW_MIN}..{CAL_SCREEN_Y_RAW_MAX} → 0..{FB_H - 1}, inverted)")
    info("If screen coords look wrong, update CAL_* constants at the top of this file.")
    print()

    abs_x_raw = abs_y_raw = 0
    event_count = 0
    deadline = time.time() + duration

    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.25)
        if not r:
            continue
        data = os.read(fd, EV_SIZE * 64)
        for i in range(0, len(data) - EV_SIZE + 1, EV_SIZE):
            ts_s, ts_us, etype, code, value = struct.unpack(EV_FMT, data[i:i + EV_SIZE])
            ts = f"{ts_s}.{ts_us:06d}"
            event_count += 1

            if etype == EV_ABS and code == ABS_X:
                abs_x_raw = value
                print(f"  [{ts}] ABS_X  raw={value:5d}"
                      f"  (phys X → screen Y, expect ~{CAL_SCREEN_Y_RAW_MIN}..{CAL_SCREEN_Y_RAW_MAX})",
                      flush=True)

            elif etype == EV_ABS and code == ABS_Y:
                abs_y_raw = value
                print(f"  [{ts}] ABS_Y  raw={value:5d}"
                      f"  (phys Y → screen X, expect ~{CAL_SCREEN_X_RAW_MIN}..{CAL_SCREEN_X_RAW_MAX})",
                      flush=True)

            elif etype == EV_KEY and code == BTN_TOUCH:
                sx, sy = raw_to_screen(abs_y_raw, abs_x_raw)
                state_s = f"{GRN}DOWN{RST}" if value else f"{YLW}UP  {RST}"
                print(f"  [{ts}] BTN_TOUCH {state_s}"
                      f"  raw=({abs_x_raw},{abs_y_raw})"
                      f"  screen=({sx},{sy})",
                      flush=True)
                if fb and fb.ok:
                    if value:
                        fb.fill(DKGRAY)
                        fb.grid()
                        fb.crosshair(sx, sy, RED)
                    else:
                        fb.dot(sx, sy, YELLOW)

    os.close(fd)
    print()
    if event_count:
        ok(f"evdev: {event_count} events received — method is working")
    else:
        warn(f"evdev: no events in {duration}s")
        info("Likely causes: driver is unbound (no kernel driver → no events),")
        info("               device grabbed by Xorg, or no physical touch occurred.")
    return event_count


# ── Method B: GPIO poll + spi_touch_read ──────────────────────────────────────

def run_gpio(fb, duration):
    hdr(f"Method B: GPIO{GPIO_BCM} poll + spi_touch_read  ({duration}s — touch the screen now)")

    # Ensure GPIO is accessible
    exported, msg = ensure_gpio()
    if not exported:
        fail(f"GPIO{GPIO_SYSFS} not available: {msg}")
        info("Is the ads7846 driver still bound? It may own this GPIO.")
        return 0

    # Ensure binary exists
    if not os.path.exists(HELPER):
        ok_c, msg_c = compile_helper()
        if not ok_c:
            fail(f"spi_touch_read not available: {msg_c}")
            return 0
        ok("Compiled spi_touch_read on the fly")

    ok(f"GPIO{GPIO_SYSFS} ready  (0=touch, 1=idle)  polling every 20 ms")
    ok(f"Coordinates from: {HELPER}")
    info("spi_touch_read already applies calibration; output is screen coords directly.")
    print()

    prev_irq   = 1
    touch_count = 0
    last_down   = 0.0
    deadline    = time.time() + duration

    while time.time() < deadline:
        irq = read_gpio()
        now = time.time()

        if irq == -1:
            fail(f"GPIO read error  t={now:.2f}")
            time.sleep(0.5)
            prev_irq = 1
            continue

        if prev_irq == 1 and irq == 0:
            # Rising edge → touch down
            touch_count += 1
            last_down = now
            coords = call_spi_touch()
            if coords:
                sx, sy = coords
                print(f"  [{now:.4f}] {GRN}TOUCH DOWN{RST}  screen=({sx:3d},{sy:3d})",
                      flush=True)
                if fb and fb.ok:
                    fb.fill(DKGRAY)
                    fb.grid()
                    fb.crosshair(sx, sy, RED)
            else:
                print(f"  [{now:.4f}] {GRN}TOUCH DOWN{RST}  "
                      f"{YLW}spi_touch_read returned err{RST}",
                      flush=True)
                info("  Possible causes: fbcp not running, SPI bus busy, not touching during read window")

        elif prev_irq == 0 and irq == 1:
            # Falling edge → touch up
            held = now - last_down if last_down else 0.0
            print(f"  [{now:.4f}] {YLW}TOUCH UP  {RST} held={held:.3f}s",
                  flush=True)

        prev_irq = irq
        time.sleep(0.02)

    print()
    if touch_count:
        ok(f"GPIO method: {touch_count} touch-down events detected — method is working")
    else:
        warn(f"GPIO method: no touch-down events in {duration}s")
        info("Possible causes: ads7846 driver still bound and owns the GPIO interrupt,")
        info("                 wiring issue with T_IRQ (GPIO17), or no physical touch.")
    return touch_count


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Standalone Memomatic touch validator — run as root on the Pi",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  sudo python3 app/touch_test.py                 # full run, 30s/method\n"
            "  sudo python3 app/touch_test.py --method evdev  # evdev only\n"
            "  sudo python3 app/touch_test.py --method gpio   # GPIO+SPI only\n"
            "  sudo python3 app/touch_test.py --duration 60   # longer window\n"
            "  sudo python3 app/touch_test.py --no-fb         # console only\n"
        ),
    )
    parser.add_argument(
        "--method", choices=["evdev", "gpio", "all"], default="all",
        help="Which detection method to test (default: all)",
    )
    parser.add_argument(
        "--duration", type=int, default=30,
        help="Seconds to listen per method (default: 30)",
    )
    parser.add_argument(
        "--no-fb", action="store_true",
        help="Disable framebuffer visualisation",
    )
    args = parser.parse_args()

    if os.geteuid() != 0:
        print(f"\n{RED_T}Run as root:{RST}  sudo python3 app/touch_test.py\n")
        sys.exit(1)

    print(f"\n{WHT}{'═' * 62}{RST}")
    print(f"  {WHT}Memomatic Touch Tester{RST}  —  ground-up hardware validator")
    print(f"{WHT}{'═' * 62}{RST}\n")

    system_check()

    fb = FB() if not args.no_fb else None
    if fb and fb.ok:
        fb.grid()

    evdev_n = gpio_n = 0
    try:
        if args.method in ("evdev", "all"):
            evdev_n = run_evdev(fb, args.duration)
        if args.method in ("gpio", "all"):
            gpio_n = run_gpio(fb, args.duration)
    except KeyboardInterrupt:
        print(f"\n{YLW}Interrupted.{RST}")

    hdr("Summary")
    evdev_r = f"{GRN}✓ {evdev_n} events{RST}" if evdev_n else f"{RED_T}✗ no events{RST}"
    gpio_r  = f"{GRN}✓ {gpio_n} touches{RST}" if gpio_n else f"{RED_T}✗ no touches{RST}"
    print(f"  evdev method:   {evdev_r}")
    print(f"  GPIO+SPI method: {gpio_r}")
    print()

    if evdev_n and not gpio_n:
        info(f"{GRN}→{RST} ads7846 kernel driver is active and working via evdev.")
        info("  Standard X11 input should work without touch_bridge.")
        info("  The GPIO+SPI path requires the driver to be unbound first.")
    elif gpio_n and not evdev_n:
        info(f"{GRN}→{RST} GPIO+SPI path is working (touch_bridge.py mode).")
        info("  ads7846 driver appears unbound — evdev will have no events.")
    elif evdev_n and gpio_n:
        info(f"{YLW}→{RST} Both methods report events — driver may be partially bound.")
        info("  Prefer evdev if possible: simpler and kernel-managed.")
    else:
        info(f"{RED_T}→{RST} Neither method detected touch events.")
        info("  Check: GPIO17 wiring, T_IRQ signal, ads7846 driver binding,")
        info("         and that you physically touched the screen during each window.")

    print()
    if fb:
        fb.close()


if __name__ == "__main__":
    main()
