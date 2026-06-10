#!/usr/bin/env python3
"""
touch_test.py  —  Standalone touch validator for Memomatic Pi
==============================================================
Ground-up touchscreen tester. Does NOT use xdotool or X11 — purely
validates the hardware detection layer independently of the main app.

By default stops pinboard-kiosk and pinboard-touch before running so
touch_bridge.py and Chromium don't race for the SPI bus or overwrite
the framebuffer.  Both services restart on exit (or Ctrl-C).

Run as root:
    sudo python3 app/touch_test.py              # both methods, 30s each
    sudo python3 app/touch_test.py --method gpio
    sudo python3 app/touch_test.py --method evdev
    sudo python3 app/touch_test.py --duration 60
    sudo python3 app/touch_test.py --no-fb      # console output only
    sudo python3 app/touch_test.py --keep-services  # skip stop/start

Each touch event prints a timestamped line with raw ADC values and
computed screen coordinates.  A crosshair is drawn on /dev/fb0 (fbcp
mirrors it to the TFT) at the reported touch position — if the crosshair
lands where you touched, calibration is correct.

Calibration note: spi_touch_read already applies the constants from
spi_touch_read.c.  To re-derive them, run raw_touch.py instead.
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
# gpiochip0 base = 512 on this Pi → BCM17 = sysfs gpio529.
GPIO_BCM   = 17
GPIO_BASE  = 512
GPIO_SYSFS = GPIO_BASE + GPIO_BCM   # 529

EVDEV_DIR  = "/dev/input"

# spi_touch_read binary lives next to this file; compiled from spi_touch_read.c
HELPER     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spi_touch_read")
HELPER_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spi_touch_read.c")

# Services that must be stopped so they don't race for the SPI bus / fb0
SERVICES_TO_STOP = ["pinboard-kiosk.service", "pinboard-touch.service"]

# Calibration for the evdev path (ads7846 kernel driver, SwapAxes=1).
# Measured empirically via raw_touch.py 4-corner test:
#   ABS_Y (physical Y, CMD_Y 0x90) → screen X:  212..1857 → 0..479
#   ABS_X (physical X, CMD_X 0xD0) → screen Y:  1839..263 → 0..319 (inverted)
CAL_SCREEN_X_RAW_MIN = 212
CAL_SCREEN_X_RAW_MAX = 1857
CAL_SCREEN_Y_RAW_MIN = 1839
CAL_SCREEN_Y_RAW_MAX = 263   # < MIN — axis is inverted

# Linux evdev constants
EV_FMT    = "llHHi"
EV_SIZE   = struct.calcsize(EV_FMT)
EV_KEY    = 1
EV_ABS    = 3
BTN_TOUCH = 0x14A
ABS_X     = 0
ABS_Y     = 1
EVIOCGRAB = 0x40044590

# RGB565 colour palette
DKGRAY = 0x2104
GRID   = 0x4208
WHITE  = 0xFFFF
RED    = 0xF800
YELLOW = 0xFFE0

# ── Terminal colours ──────────────────────────────────────────────────────────
GRN = "\033[92m"; RED_T = "\033[91m"; YLW = "\033[93m"
CYN = "\033[96m"; WHT   = "\033[97m"; RST = "\033[0m"

def ok(s):   print(f"  {GRN}✓{RST}  {s}")
def fail(s): print(f"  {RED_T}✗{RST}  {s}")
def warn(s): print(f"  {YLW}!{RST}  {s}")
def info(s): print(f"     {s}")
def hdr(s):  print(f"\n{CYN}── {s} {'─' * max(0, 54 - len(s))}{RST}")


# ── Service management ────────────────────────────────────────────────────────

def service_active(name):
    r = subprocess.run(["systemctl", "is-active", "--quiet", name])
    return r.returncode == 0


def stop_services():
    hdr("Stopping conflicting services")
    for svc in SERVICES_TO_STOP:
        if service_active(svc):
            r = subprocess.run(["sudo", "systemctl", "stop", svc],
                               capture_output=True, text=True)
            if r.returncode == 0:
                ok(f"Stopped {svc}")
            else:
                warn(f"Could not stop {svc}: {r.stderr.strip()}")
        else:
            info(f"{svc} was not running")
    time.sleep(1)


def start_services():
    hdr("Restarting services")
    for svc in SERVICES_TO_STOP:
        r = subprocess.run(["sudo", "systemctl", "start", svc],
                           capture_output=True, text=True)
        if r.returncode == 0:
            ok(f"Started {svc}")
        else:
            warn(f"Could not start {svc}: {r.stderr.strip()}")


# ── Framebuffer ───────────────────────────────────────────────────────────────

class FB:
    """RGB565 framebuffer writer with yoffset support.

    fbcp-ili9341 keeps running during the test (only kiosk/touch are
    stopped), so writes to /dev/fb0 appear on the TFT immediately.

    Reads the active buffer offset via FBIOGET_VSCREENINFO so writes land
    in the right place even when Xorg has set a non-zero yoffset (double
    buffering).  Maps the whole file and writes at the correct offset.
    """

    _FBIOGET_VSCREENINFO = 0x4600
    # struct fb_var_screeninfo: xres, yres, xres_virtual, yres_virtual,
    #                           xoffset, yoffset, bits_per_pixel, ...
    _VINFO_FMT = "IIIIIII"

    def __init__(self):
        self._mm     = None
        self._offset = 0
        self.w       = FB_W
        self.h       = FB_H
        self.xres_v  = FB_W
        try:
            f = open(FB_DEV, "r+b")
            try:
                buf = b"\x00" * 160
                res = fcntl.ioctl(f.fileno(), self._FBIOGET_VSCREENINFO, buf)
                xres, yres, xres_v, yres_v, xoff, yoff, bpp = \
                    struct.unpack_from(self._VINFO_FMT, res)
                if xres > 0 and yres > 0:
                    self.w, self.h, self.xres_v = xres, yres, xres_v
                self._offset = yoff * xres_v * 2
                info(f"  fb: {xres}×{yres}  virtual={xres_v}×{yres_v}"
                     f"  yoffset={yoff}  bpp={bpp}"
                     f"  → active buffer at byte {self._offset}")
            except Exception as ex:
                warn(f"  FBIOGET_VSCREENINFO failed ({ex}), assuming yoffset=0")
            f.seek(0, 2)
            fsize = f.tell()
            f.seek(0)
            self._mm = mmap.mmap(f.fileno(), fsize)
            f.close()
            self.fill(DKGRAY)
            ok(f"Framebuffer {FB_DEV}  ({self.w}×{self.h}  offset={self._offset})")
        except Exception as e:
            warn(f"Framebuffer unavailable: {e}")

    @property
    def ready(self):
        return self._mm is not None

    def _off(self, x, y):
        return self._offset + (y * self.xres_v + x) * 2

    def _put(self, x, y, color):
        if self._mm and 0 <= x < self.w and 0 <= y < self.h:
            self._mm.seek(self._off(x, y))
            self._mm.write(struct.pack("<H", color))

    def fill(self, color=DKGRAY):
        if not self._mm:
            return
        px = struct.pack("<H", color)
        if self.xres_v == self.w:
            self._mm.seek(self._offset)
            self._mm.write(px * (self.w * self.h))
        else:
            for y in range(self.h):
                self._mm.seek(self._off(0, y))
                self._mm.write(px * self.w)

    def grid(self, step=40, color=GRID):
        """Draw a reference grid with a single write per row."""
        if not self._mm:
            return
        px = struct.pack("<H", color)
        base_row = bytearray(struct.pack("<H", DKGRAY) * self.w)
        for x in range(0, self.w, step):
            base_row[x*2:x*2+2] = px
        for y in range(self.h):
            row = bytearray(px * self.w) if (y % step == 0) else bytearray(base_row)
            self._mm.seek(self._off(0, y))
            self._mm.write(row)

    def crosshair(self, x, y, color=RED, arm=16):
        for dx in range(-arm, arm + 1):
            self._put(x + dx, y, color)
        for dy in range(-arm, arm + 1):
            self._put(x, y + dy, color)
        for dx in range(-3, 4):
            for dy in range(-3, 4):
                self._put(x + dx, y + dy, WHITE)

    def dot(self, x, y, color=YELLOW, r=4):
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
    try:
        return int(open(_gpio_value_path(n)).read().strip())
    except (OSError, ValueError):
        return -1


# ── Calibration (evdev path) ──────────────────────────────────────────────────

def raw_to_screen(abs_y_raw, abs_x_raw):
    """Map raw evdev ABS values to screen coords using the measured calibration."""
    sx = int((abs_y_raw - CAL_SCREEN_X_RAW_MIN) /
             (CAL_SCREEN_X_RAW_MAX - CAL_SCREEN_X_RAW_MIN) * FB_W)
    sy = int((abs_x_raw - CAL_SCREEN_Y_RAW_MIN) /
             (CAL_SCREEN_Y_RAW_MAX - CAL_SCREEN_Y_RAW_MIN) * FB_H)
    return max(0, min(FB_W - 1, sx)), max(0, min(FB_H - 1, sy))


# ── spi_touch_read wrapper ────────────────────────────────────────────────────

def compile_helper():
    if not os.path.exists(HELPER_SRC):
        return False, f"source not found: {HELPER_SRC}"
    r = subprocess.run(["gcc", "-O2", "-o", HELPER, HELPER_SRC],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return False, r.stderr.strip()
    return True, "compiled ok"


def call_spi_touch():
    """Run spi_touch_read. Returns (sx, sy, diag) on success, (None, None, diag) on error.
    diag contains the stderr output: ta_seen flag and per-sample raw ADC values."""
    try:
        r = subprocess.run([HELPER], capture_output=True, timeout=0.5)
        diag = r.stderr.decode().strip().replace("\n", "  ")
        if r.returncode == 0:
            parts = r.stdout.decode().strip().split()
            if len(parts) == 2:
                sx, sy = int(parts[0]), int(parts[1])
                if 0 <= sx < FB_W and 0 <= sy < FB_H:
                    return sx, sy, diag
        return None, None, diag
    except Exception as e:
        return None, None, f"exception={e}"


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
        ok(f"gpio{GPIO_SYSFS}  value={val}  ({state})  ({msg})")
    else:
        fail(f"Cannot export gpio{GPIO_SYSFS}: {msg}")

    hdr("ADS7846 kernel driver binding")
    bound = os.path.exists("/sys/bus/spi/drivers/ads7846/spi0.1")
    if bound:
        warn("ads7846 driver IS bound to spi0.1")
        info("→ evdev method should work  |  GPIO+SPI path needs driver unbound")
    else:
        ok("ads7846 driver is NOT bound to spi0.1")
        info("→ GPIO+SPI path (touch_bridge mode) is active")
        info("→ evdev will have no events without a kernel driver")

    hdr("spi_touch_read binary")
    if os.path.exists(HELPER):
        ok(f"Binary: {HELPER}")
        try:
            r = subprocess.run([HELPER], capture_output=True, timeout=0.5)
            out = r.stdout.decode().strip()
            info(f"  Test call → {out!r}  (rc={r.returncode})  'err' is expected when not touching")
        except Exception as e:
            warn(f"  Test call failed: {e}")
    else:
        warn(f"Binary not found: {HELPER}")
        if os.path.exists(HELPER_SRC):
            info("  Compiling from source...")
            ok_c, msg_c = compile_helper()
            if ok_c:
                ok(f"  {msg_c}")
            else:
                fail(f"  Compile failed: {msg_c}")
        else:
            fail(f"  Source also missing: {HELPER_SRC}")

    hdr("fbcp-ili9341 display driver")
    r = subprocess.run(["pgrep", "-f", "fbcp"], capture_output=True, text=True)
    if r.returncode == 0:
        pid = r.stdout.strip().splitlines()[0]
        ok(f"fbcp running (pid {pid})  — spi_touch_read will sync to frame gaps")
        try:
            name = open(f"/proc/{pid}/comm").read().strip()
            info(f"  Process name: {name}")
        except OSError:
            pass
    else:
        warn("fbcp NOT running — spi_touch_read reads will fail (no inter-frame sync)")

    hdr("Competing services")
    for svc in SERVICES_TO_STOP:
        active = service_active(svc)
        marker = f"{YLW}running — will be stopped{RST}" if active else f"{GRN}not running{RST}"
        info(f"  {svc}: {marker}")

    hdr(f"Input devices ({EVDEV_DIR})")
    try:
        events = sorted(e for e in os.listdir(EVDEV_DIR) if e.startswith("event"))
        if events:
            for ev in events:
                np = f"/sys/class/input/{ev}/device/name"
                name = open(np).read().strip() if os.path.exists(np) else "unknown"
                tag = (f"  {GRN}← likely touch{RST}"
                       if any(k in name.lower() for k in ("ads7846", "touch", "pen"))
                       else "")
                ok(f"{EVDEV_DIR}/{ev}  →  {name}{tag}")
        else:
            warn("No event devices in /dev/input  (expected when driver is unbound)")
    except OSError as e:
        fail(f"Cannot list {EVDEV_DIR}: {e}")


# ── Method A: evdev ───────────────────────────────────────────────────────────

def run_evdev(fb, duration):
    hdr(f"Method A: evdev  ({duration}s — touch the screen now)")

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
        warn(f"No named touch device — trying {device}")

    try:
        fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as e:
        fail(f"Cannot open {device}: {e}")
        info("evdev requires the ads7846 kernel driver to be bound.")
        return 0

    try:
        fcntl.ioctl(fd, EVIOCGRAB, struct.pack("I", 1))
        fcntl.ioctl(fd, EVIOCGRAB, struct.pack("I", 0))
        ok("Device is free (not grabbed)")
    except OSError as e:
        if e.errno == errno.EBUSY:
            fail("Device IS GRABBED — evdev reads blocked")
            info('Fix: Option "GrabDevice" "off" in xorg InputClass config')
            os.close(fd)
            return 0
        warn(f"EVIOCGRAB returned errno {e.errno}")

    print()
    info(f"  ABS_Y (phys Y) → screen X  raw {CAL_SCREEN_X_RAW_MIN}..{CAL_SCREEN_X_RAW_MAX} → 0..{FB_W-1}")
    info(f"  ABS_X (phys X) → screen Y  raw {CAL_SCREEN_Y_RAW_MIN}..{CAL_SCREEN_Y_RAW_MAX} → 0..{FB_H-1} (inverted)")
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
                print(f"  [{ts}] ABS_X  raw={value:5d}", flush=True)
            elif etype == EV_ABS and code == ABS_Y:
                abs_y_raw = value
                print(f"  [{ts}] ABS_Y  raw={value:5d}", flush=True)
            elif etype == EV_KEY and code == BTN_TOUCH:
                sx, sy = raw_to_screen(abs_y_raw, abs_x_raw)
                state_s = f"{GRN}DOWN{RST}" if value else f"{YLW}UP  {RST}"
                print(f"  [{ts}] BTN_TOUCH {state_s}"
                      f"  raw=({abs_x_raw},{abs_y_raw})"
                      f"  screen=({sx},{sy})", flush=True)
                if fb and fb.ready:
                    if value:
                        fb.fill(DKGRAY)
                        fb.grid()
                        fb.crosshair(sx, sy, RED)
                    else:
                        fb.dot(sx, sy, YELLOW)

    os.close(fd)
    print()
    if event_count:
        ok(f"evdev: {event_count} events — method is working")
    else:
        warn(f"evdev: no events in {duration}s")
        info("Expected when ads7846 driver is unbound (no kernel driver = no events).")
    return event_count


# ── Method B: GPIO poll + spi_touch_read ──────────────────────────────────────

def run_gpio(fb, duration):
    hdr(f"Method B: GPIO{GPIO_BCM} poll + spi_touch_read  ({duration}s — touch the screen now)")

    exported, msg = ensure_gpio()
    if not exported:
        fail(f"GPIO{GPIO_SYSFS} not available: {msg}")
        return 0

    # Always recompile so the binary matches the current .c calibration constants
    if os.path.exists(HELPER_SRC):
        ok_c, msg_c = compile_helper()
        if ok_c:
            ok(f"Compiled spi_touch_read  ({msg_c})")
        else:
            fail(f"Compile failed: {msg_c}")
            return 0
    elif not os.path.exists(HELPER):
        fail("spi_touch_read binary and source both missing")
        return 0

    r_fbcp = subprocess.run(["pgrep", "-f", "fbcp"], capture_output=True, text=True)
    fbcp_running = r_fbcp.returncode == 0
    if not fbcp_running:
        warn("fbcp is NOT running — spi_touch_read needs fbcp's SPI frame gaps")
        info("Start fbcp-ili9341 before running this test.")
        print()

    ok(f"Polling GPIO{GPIO_SYSFS} at 20 ms  (0=touch, 1=idle)")
    info("Each TOUCH DOWN line shows: screen coords + raw ADC diagnostics from spi_touch_read")
    print()

    prev_irq    = 1
    touch_count = 0
    spi_ok      = 0
    spi_err     = 0
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
            touch_count += 1
            last_down = now
            sx, sy, diag = call_spi_touch()
            if sx is not None:
                spi_ok += 1
                print(f"  [{now:.4f}] {GRN}TOUCH DOWN{RST}  screen=({sx:3d},{sy:3d})"
                      f"  [{diag}]", flush=True)
                if fb and fb.ready:
                    fb.fill(DKGRAY)
                    fb.grid()
                    fb.crosshair(sx, sy, RED)
            else:
                spi_err += 1
                print(f"  [{now:.4f}] {GRN}TOUCH DOWN{RST}  "
                      f"{YLW}err{RST}  [{diag}]"
                      f"  (ok={spi_ok} err={spi_err})", flush=True)

        elif prev_irq == 0 and irq == 1:
            held = now - last_down if last_down else 0.0
            print(f"  [{now:.4f}] {YLW}TOUCH UP{RST}   held={held:.3f}s", flush=True)

        prev_irq = irq
        time.sleep(0.02)

    print()
    if touch_count:
        ok(f"GPIO: {touch_count} touch-down events  "
           f"(spi coords: {spi_ok} ok / {spi_err} err)")
        if spi_err and not spi_ok:
            warn("spi_touch_read failed on every touch — check [diag] above.")
            if not fbcp_running:
                info("Root cause: fbcp was not running (ta_seen=0 = no inter-frame sync).")
            info("If raw values are 0 or 4095: pen lifted before SPI read completed.")
    else:
        warn(f"GPIO: no touches detected in {duration}s")
        info("Check: ads7846 driver bound? GPIO17 wired? Did you touch the screen?")
    return touch_count


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Standalone Memomatic touch validator — run as root on the Pi",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  sudo python3 app/touch_test.py\n"
            "  sudo python3 app/touch_test.py --method gpio --duration 60\n"
            "  sudo python3 app/touch_test.py --keep-services\n"
            "  sudo python3 app/touch_test.py --no-fb\n"
            "\n"
            "To re-derive calibration constants:\n"
            "  sudo python3 app/raw_touch.py\n"
        ),
    )
    parser.add_argument("--method", choices=["evdev", "gpio", "all"], default="all")
    parser.add_argument("--duration", type=int, default=30,
                        help="Seconds to listen per method (default: 30)")
    parser.add_argument("--no-fb", action="store_true",
                        help="Skip framebuffer visualisation")
    parser.add_argument("--keep-services", action="store_true",
                        help="Don't stop pinboard-kiosk/touch before testing")
    args = parser.parse_args()

    if os.geteuid() != 0:
        print(f"\n{RED_T}Run as root:{RST}  sudo python3 app/touch_test.py\n")
        sys.exit(1)

    print(f"\n{WHT}{'═' * 62}{RST}")
    print(f"  {WHT}Memomatic Touch Tester{RST}  —  ground-up hardware validator")
    print(f"{WHT}{'═' * 62}{RST}\n")

    system_check()

    if not args.keep_services:
        stop_services()
    else:
        warn("--keep-services: kiosk and touch_bridge are still running")
        info("spi_touch_read may race with touch_bridge; framebuffer may be overwritten.")

    fb = FB() if not args.no_fb else None
    if fb and fb.ready:
        fb.grid()

    evdev_n = gpio_n = 0
    try:
        if args.method in ("evdev", "all"):
            evdev_n = run_evdev(fb, args.duration)
        if args.method in ("gpio", "all"):
            gpio_n = run_gpio(fb, args.duration)
    except KeyboardInterrupt:
        print(f"\n{YLW}Interrupted.{RST}")
    finally:
        if not args.keep_services:
            start_services()
        if fb:
            fb.close()

    hdr("Summary")
    evdev_r = (f"{GRN}✓ {evdev_n} events{RST}" if evdev_n else f"{RED_T}✗ no events{RST}")
    gpio_r  = (f"{GRN}✓ {gpio_n} touches{RST}" if gpio_n else f"{RED_T}✗ no touches{RST}")
    print(f"  evdev method:    {evdev_r}")
    print(f"  GPIO+SPI method: {gpio_r}")
    print()

    if gpio_n and not evdev_n:
        info(f"{GRN}→{RST} GPIO+SPI path is working (touch_bridge mode).")
        info("  If crosshairs appeared at wrong positions, re-run raw_touch.py to recalibrate.")
    elif evdev_n and not gpio_n:
        info(f"{GRN}→{RST} evdev path works — ads7846 driver is active.")
    elif evdev_n and gpio_n:
        info(f"{YLW}→{RST} Both methods detected touches.")
    else:
        info(f"{RED_T}→{RST} No touches detected by either method.")
        info("  Check GPIO17 wiring, driver binding, and that you touched the screen.")
    print()


if __name__ == "__main__":
    main()
