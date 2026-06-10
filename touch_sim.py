#!/usr/bin/env python3
"""
touch_sim.py - GPIO touch monitoring and simulation for ADS7846

Checks GPIO17 (T_IRQ/pendown) and GPIO23 at every available software level,
and can simulate touch events without physical touch.

Levels probed:
  L0  /sys/kernel/debug/gpio      - kernel-internal GPIO state (read-only)
  L1  /proc/interrupts            - IRQ counter for ads7846 (GPIO17 edge)
  L2  /sys/bus/spi/.../pen_down   - ADS7846 driver pen-detect sysfs
  L3  /sys/class/gpio/gpioN/value - raw GPIO sysfs (GPIO23 only; GPIO17 is
                                    driver-owned and cannot be exported)
  L4  /dev/input/event0           - evdev BTN_TOUCH + ABS events
  L5  xdotool click               - synthetic X11 pointer injection
  L6  CDP Runtime.evaluate        - verify JS received the click

Usage:
  sudo python3 touch_sim.py            # monitor + auto-simulate
  sudo python3 touch_sim.py --monitor  # monitor only (no simulation)
  sudo python3 touch_sim.py --sim      # simulate once at each level and exit
"""

import struct, os, sys, time, select, subprocess, socket, json, base64
import fcntl, signal, argparse, errno, glob

# ── config ──────────────────────────────────────────────────────────────────

DEVICE     = "/dev/input/event0"
XENV       = {"DISPLAY": ":0", "XAUTHORITY": "/root/.Xauthority", **os.environ}
PEN_DOWN   = "/sys/bus/spi/devices/spi0.1/pen_down"
CDP_HOST   = "127.0.0.1"
CDP_PORT   = 9222

# On this Pi: gpiochip0 base = 512; BCM GPIO N = sysfs gpio (512 + N)
GPIO_BASE  = 512
GPIO17_SYS = GPIO_BASE + 17   # penirq - owned by ADS7846 driver, cannot export
GPIO23_SYS = GPIO_BASE + 23   # spare GPIO, can export

# evdev struct (32-bit ARM: sec=4 usec=4 type=2 code=2 value=4 = 16 bytes)
EVENT_FMT  = "llHHI"
EVENT_SIZE = struct.calcsize(EVENT_FMT)
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
BTN_TOUCH  = 0x14A
ABS_X, ABS_Y = 0, 1

# uinput ioctl numbers (linux/uinput.h)
UI_SET_EVBIT  = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_SET_ABSBIT = 0x40045567
UI_DEV_CREATE  = 0x5501
UI_DEV_DESTROY = 0x5502
ABS_CNT       = 64

# ── colour helpers ───────────────────────────────────────────────────────────

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"

def ok(msg):   print(f"  {GREEN}OK  {RESET} {msg}")
def fail(msg): print(f"  {RED}FAIL{RESET} {msg}")
def warn(msg): print(f"  {YELLOW}WARN{RESET} {msg}")
def info(msg): print(f"       {msg}")

def hdr(title):
    print(f"\n{'─'*62}")
    print(f"  {CYAN}{title}{RESET}")
    print(f"{'─'*62}")

# ── GPIO sysfs helpers ───────────────────────────────────────────────────────

def export_gpio(sysfs_n):
    """Export a GPIO via sysfs. Returns path to value file or None."""
    gpio_dir = f"/sys/class/gpio/gpio{sysfs_n}"
    if not os.path.exists(gpio_dir):
        try:
            with open("/sys/class/gpio/export", "w") as f:
                f.write(str(sysfs_n))
            time.sleep(0.15)
        except OSError as e:
            return None, str(e)
    try:
        with open(f"{gpio_dir}/direction", "w") as f:
            f.write("in")
    except OSError:
        pass
    val_path = f"{gpio_dir}/value"
    return val_path if os.path.exists(val_path) else None, ""

def read_gpio_value(sysfs_n):
    gpio_dir = f"/sys/class/gpio/gpio{sysfs_n}"
    try:
        return open(f"{gpio_dir}/value").read().strip()
    except OSError:
        return "unavailable"

def read_gpio_debug(bcm_n):
    """Read GPIO state from kernel debug fs (read-only, doesn't require export)."""
    try:
        sysfs_n = GPIO_BASE + bcm_n
        with open("/sys/kernel/debug/gpio") as f:
            for line in f:
                if f"gpio-{sysfs_n}" in line:
                    return line.strip()
    except OSError:
        pass
    return None

def read_irq_count(label="ads7846"):
    """Return IRQ count for ads7846 from /proc/interrupts."""
    try:
        for line in open("/proc/interrupts"):
            if label.lower() in line.lower():
                parts = line.split()
                counts = []
                for p in parts[1:]:
                    try:
                        counts.append(int(p))
                    except ValueError:
                        break
                return sum(counts), line.strip()
    except OSError:
        pass
    return None, ""

# ── Level 0: kernel debug gpio ──────────────────────────────────────────────

def check_l0():
    hdr("L0 — kernel /sys/kernel/debug/gpio")
    for bcm, label in [(17, "GPIO17 (T_IRQ/pendown)"), (23, "GPIO23 (spare)")]:
        line = read_gpio_debug(bcm)
        if line:
            ok(f"{label}: {line}")
        else:
            warn(f"{label}: not found in kernel debug gpio")

# ── Level 1: /proc/interrupts ────────────────────────────────────────────────

def check_l1():
    hdr("L1 — /proc/interrupts IRQ counter")
    count, raw = read_irq_count("ads7846")
    if count is not None:
        ok(f"ads7846 IRQ count = {count}")
        info(raw)
    else:
        fail("No ads7846 entry in /proc/interrupts")
    # GPIO23 has no IRQ registered by default
    info("GPIO23 has no IRQ registered (spare pin)")

# ── Level 2: pen_down sysfs ──────────────────────────────────────────────────

def check_l2():
    hdr("L2 — ADS7846 pen_down sysfs")
    if os.path.exists(PEN_DOWN):
        val = open(PEN_DOWN).read().strip()
        ok(f"pen_down = {val}  (1=touching, 0=not touching)")
    else:
        fail(f"{PEN_DOWN} does not exist")

# ── Level 3: raw GPIO sysfs ──────────────────────────────────────────────────

def check_l3():
    hdr("L3 — raw GPIO sysfs values")
    # GPIO17 is driver-owned; try to export but expect failure
    val17, err17 = export_gpio(GPIO17_SYS)
    if val17:
        v = read_gpio_value(GPIO17_SYS)
        ok(f"GPIO17 (gpio{GPIO17_SYS}) exported, value = {v}  (0=touch active)")
    else:
        warn(f"GPIO17 (gpio{GPIO17_SYS}) cannot be exported (driver-owned): {err17}")
        line = read_gpio_debug(17)
        info(f"  kernel debug: {line or 'unavailable'}")

    val23, err23 = export_gpio(GPIO23_SYS)
    if val23:
        v = read_gpio_value(GPIO23_SYS)
        ok(f"GPIO23 (gpio{GPIO23_SYS}) exported, value = {v}")
    else:
        fail(f"GPIO23 (gpio{GPIO23_SYS}) cannot be exported: {err23}")

# ── Level 4: evdev ────────────────────────────────────────────────────────────

def check_l4(watch_secs=3):
    hdr("L4 — evdev /dev/input/event0 (watching {watch_secs}s for events)")
    # Check EVIOCGRAB status
    EVIOCGRAB = 0x40044590
    grabbed = False
    try:
        fd2 = os.open(DEVICE, os.O_RDONLY | os.O_NONBLOCK)
        try:
            fcntl.ioctl(fd2, EVIOCGRAB, struct.pack("I", 1))
            fcntl.ioctl(fd2, EVIOCGRAB, struct.pack("I", 0))
            ok("Device is NOT grabbed by Xorg (GrabDevice=no works)")
        except OSError as e:
            if e.errno == errno.EBUSY:
                fail("Device IS grabbed by Xorg — touch_bridge cannot read events")
                grabbed = True
            else:
                warn(f"EVIOCGRAB: unexpected errno {e.errno}")
        os.close(fd2)
    except OSError as e:
        fail(f"Cannot open {DEVICE}: {e}")
        return

    if grabbed:
        return

    # Watch for real events
    info(f"Watching {DEVICE} for {watch_secs}s — touch screen if physically possible")
    events = []
    try:
        fd = os.open(DEVICE, os.O_RDONLY | os.O_NONBLOCK)
        deadline = time.time() + watch_secs
        while time.time() < deadline:
            r, _, _ = select.select([fd], [], [], 0.25)
            if r:
                data = os.read(fd, EVENT_SIZE * 32)
                for i in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
                    _, _, etype, code, value = struct.unpack(EVENT_FMT, data[i:i+EVENT_SIZE])
                    if etype == EV_KEY and code == BTN_TOUCH:
                        events.append(("BTN_TOUCH", "DOWN" if value else "UP"))
                    elif etype == EV_ABS and code in (ABS_X, ABS_Y):
                        events.append(("ABS_X" if code == ABS_X else "ABS_Y", value))
        os.close(fd)
    except OSError as e:
        fail(f"evdev read error: {e}")
        return

    if events:
        ok(f"Received {len(events)} events from /dev/input/event0")
        for e in events[:10]:
            info(f"  {e}")
    else:
        info("No events in {watch_secs}s (expected when not physically touched)")

# ── Level 5: xdotool simulation ──────────────────────────────────────────────

def sim_l5_xdotool(x=240, y=160):
    hdr("L5 — Simulate click via xdotool (X11 injection)")
    try:
        r = subprocess.run(
            ["xdotool", "mousemove", str(x), str(y), "click", "1"],
            env=XENV, capture_output=True, timeout=5
        )
        if r.returncode == 0:
            ok(f"xdotool injected click at ({x},{y})")
        else:
            fail(f"xdotool failed: {r.stderr.decode().strip()}")
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        fail(f"xdotool error: {e}")

# ── Level 6: CDP verification ─────────────────────────────────────────────────

def _cdp_get_page_id():
    try:
        s = socket.create_connection((CDP_HOST, CDP_PORT), timeout=3)
        s.sendall(b"GET /json HTTP/1.1\r\nHost: 127.0.0.1:9222\r\nConnection: close\r\n\r\n")
        resp = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            resp += chunk
        s.close()
        body = resp.split(b"\r\n\r\n", 1)[-1]
        pages = json.loads(body)
        for p in pages:
            if p.get("type") == "page" and "frame" in p.get("url", ""):
                return p["id"]
        return pages[0]["id"] if pages else None
    except Exception:
        return None

def _cdp_connect(pid):
    s = socket.create_connection((CDP_HOST, CDP_PORT), timeout=5)
    key = base64.b64encode(os.urandom(16)).decode()
    hs = (
        f"GET /devtools/page/{pid} HTTP/1.1\r\n"
        f"Host: {CDP_HOST}:{CDP_PORT}\r\n"
        "Upgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    s.sendall(hs.encode())
    resp = s.recv(4096)
    assert b"101" in resp, f"WS handshake failed: {resp[:100]}"
    return s

def _ws_send(s, data):
    data = data.encode()
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    hdr = b"\x81"
    n = len(data)
    hdr += bytes([0x80 | n]) if n < 126 else b"\xfe" + struct.pack(">H", n)
    s.sendall(hdr + mask + masked)

def _ws_recv(s, timeout=2):
    s.settimeout(timeout)
    try:
        h = s.recv(2)
    except socket.timeout:
        return None
    if len(h) < 2:
        return None
    n = h[1] & 0x7F
    if n == 126:
        n = struct.unpack(">H", s.recv(2))[0]
    data = b""
    while len(data) < n:
        chunk = s.recv(min(65536, n - len(data)))
        if not chunk:
            break
        data += chunk
    return data

def _cdp_eval(s, mid, expr, timeout=3):
    _ws_send(s, json.dumps({"id": mid, "method": "Runtime.evaluate",
                             "params": {"expression": expr, "returnByValue": True}}))
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = _ws_recv(s, timeout=0.4)
        if raw is None:
            continue
        try:
            obj = json.loads(raw.decode(errors="replace"))
        except Exception:
            continue
        if obj.get("id") == mid:
            return obj.get("result", {}).get("result", {}).get("value")
    return None

def check_l6_cdp():
    hdr("L6 — CDP: verify menu state in Chromium JS")
    pid = _cdp_get_page_id()
    if not pid:
        warn("Cannot connect to Chromium CDP on port 9222")
        return False
    try:
        ws = _cdp_connect(pid)
        mv = _cdp_eval(ws, 1, "String(menuVisible)")
        oc = _cdp_eval(ws, 2,
                       "(document.getElementById('menu-overlay') || {}).className || 'no element'")
        ws.close()
        info(f"menuVisible = {mv}")
        info(f"menu-overlay class = {oc}")
        if mv == "true":
            ok("Menu IS visible in Chromium JS")
            return True
        else:
            info("Menu is not visible (expected before click)")
            return False
    except Exception as e:
        warn(f"CDP error: {e}")
        return False

def sim_l6_cdp_inject(x=240, y=160):
    """Inject a synthetic pointer event directly via CDP (bypasses X11 entirely)."""
    hdr("L6 — Simulate touch via CDP Input.dispatchTouchEvent")
    pid = _cdp_get_page_id()
    if not pid:
        warn("Cannot connect to CDP — skip CDP injection")
        return
    try:
        ws = _cdp_connect(pid)
        # First hide menu to get a clean state
        _cdp_eval(ws, 10, "hideMenu()")
        time.sleep(0.3)
        mv_before = _cdp_eval(ws, 11, "String(menuVisible)")
        info(f"menuVisible before CDP touch: {mv_before}")

        # Dispatch a touchStart + touchEnd via CDP
        touch_start = json.dumps({
            "id": 20,
            "method": "Input.dispatchTouchEvent",
            "params": {
                "type": "touchStart",
                "touchPoints": [{"x": x, "y": y, "id": 0}],
                "modifiers": 0,
                "timestamp": time.time()
            }
        })
        _ws_send(ws, touch_start)
        _ws_recv(ws, timeout=1)
        time.sleep(0.05)

        touch_end = json.dumps({
            "id": 21,
            "method": "Input.dispatchTouchEvent",
            "params": {
                "type": "touchEnd",
                "touchPoints": [{"x": x, "y": y, "id": 0}],
                "modifiers": 0,
                "timestamp": time.time()
            }
        })
        _ws_send(ws, touch_end)
        _ws_recv(ws, timeout=1)
        time.sleep(0.5)

        mv_after = _cdp_eval(ws, 22, "String(menuVisible)")
        info(f"menuVisible after CDP touch: {mv_after}")
        if mv_after == "true":
            ok("CDP touch injection triggered menu — full pipeline works via CDP")
        else:
            # Also try Input.dispatchMouseEvent as fallback
            _cdp_eval(ws, 30, "hideMenu()")
            time.sleep(0.2)
            mouse_click = json.dumps({
                "id": 31,
                "method": "Input.dispatchMouseEvent",
                "params": {
                    "type": "mousePressed",
                    "x": x, "y": y,
                    "button": "left",
                    "clickCount": 1,
                    "timestamp": time.time()
                }
            })
            _ws_send(ws, mouse_click)
            _ws_recv(ws, timeout=1)
            mouse_release = json.dumps({
                "id": 32,
                "method": "Input.dispatchMouseEvent",
                "params": {
                    "type": "mouseReleased",
                    "x": x, "y": y,
                    "button": "left",
                    "clickCount": 1,
                    "timestamp": time.time()
                }
            })
            _ws_send(ws, mouse_release)
            _ws_recv(ws, timeout=1)
            time.sleep(0.5)
            mv_after2 = _cdp_eval(ws, 33, "String(menuVisible)")
            info(f"menuVisible after CDP mouse click: {mv_after2}")
            if mv_after2 == "true":
                ok("CDP mouse click triggered menu")
            else:
                fail("Neither CDP touch nor mouse event triggered menu")
        ws.close()
    except Exception as e:
        fail(f"CDP injection error: {e}")

# ── uinput simulation ─────────────────────────────────────────────────────────

def sim_uinput(x=240, y=160):
    """Simulate a BTN_TOUCH event via uinput (creates virtual input device)."""
    hdr("L4 — Simulate touch via uinput (kernel evdev injection)")
    try:
        fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_SYN)
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_ABS)
        fcntl.ioctl(fd, UI_SET_KEYBIT, BTN_TOUCH)
        fcntl.ioctl(fd, UI_SET_ABSBIT, ABS_X)
        fcntl.ioctl(fd, UI_SET_ABSBIT, ABS_Y)

        # Write uinput_user_dev: name(80) + input_id(8) + ff_effects_max(4) + abs arrays(4*64*4)
        dev_data = struct.pack(
            "80sHHHHi" + "i" * ABS_CNT * 4,
            b"SimTouch".ljust(80, b"\x00"),
            0x18, 0, 0x1EA6, 0,  # BUS_SPI, vendor, product(ADS7846), version
            0,                    # ff_effects_max
            *([0] * ABS_CNT),    # absmax
            *([0] * ABS_CNT),    # absmin
            *([0] * ABS_CNT),    # absfuzz
            *([0] * ABS_CNT),    # absflat
        )
        os.write(fd, dev_data)
        fcntl.ioctl(fd, UI_DEV_CREATE)

        # Find the new device
        time.sleep(0.4)
        new_devs = [d for d in glob.glob("/dev/input/event*") if d != DEVICE]
        info(f"uinput created virtual device(s): {new_devs or ['(check /dev/input/)']}")

        def emit(etype, code, value):
            os.write(fd, struct.pack(EVENT_FMT, 0, 0, etype, code, value))

        emit(EV_ABS, ABS_X, x)
        emit(EV_ABS, ABS_Y, y)
        emit(EV_KEY, BTN_TOUCH, 1)
        emit(EV_SYN, 0, 0)
        time.sleep(0.1)
        emit(EV_KEY, BTN_TOUCH, 0)
        emit(EV_SYN, 0, 0)

        ok(f"uinput BTN_TOUCH emitted at abs({x},{y})")
        info("Note: touch_bridge monitors only event0; this tests the kernel layer")

        time.sleep(0.3)
        fcntl.ioctl(fd, UI_DEV_DESTROY)
        os.close(fd)
    except Exception as e:
        fail(f"uinput simulation error: {e}")

# ── continuous GPIO+evdev monitor ────────────────────────────────────────────

def monitor_loop(duration=30):
    """Continuously monitor GPIO17/23 and evdev for touch activity."""
    hdr(f"Monitor mode — watching all levels for {duration}s")
    info("Polling pen_down, GPIO23, IRQ counter, and evdev for touch events...")
    print()

    irq_start, _ = read_irq_count()
    irq_start = irq_start or 0

    val23_path, _ = export_gpio(GPIO23_SYS)

    try:
        fd = os.open(DEVICE, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as e:
        fail(f"Cannot open {DEVICE}: {e}")
        return

    last_pendown = -1
    deadline = time.time() + duration
    t0 = time.time()

    while time.time() < deadline:
        t = time.time() - t0

        # pen_down sysfs
        try:
            pd = int(open(PEN_DOWN).read().strip())
        except (OSError, ValueError):
            pd = -1

        # GPIO23 sysfs
        gpio23 = "?"
        if val23_path:
            try:
                gpio23 = open(val23_path).read().strip()
            except OSError:
                pass

        # IRQ counter delta
        irq_now, _ = read_irq_count()
        irq_delta = (irq_now or 0) - irq_start

        # evdev events
        evdev_events = []
        r, _, _ = select.select([fd], [], [], 0.1)
        if r:
            data = os.read(fd, EVENT_SIZE * 32)
            for i in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
                _, _, etype, code, value = struct.unpack(EVENT_FMT, data[i:i+EVENT_SIZE])
                if etype == EV_KEY and code == BTN_TOUCH:
                    evdev_events.append(f"BTN_TOUCH={'DOWN' if value else 'UP'}")
                elif etype == EV_ABS and code == ABS_X:
                    evdev_events.append(f"ABS_X={value}")
                elif etype == EV_ABS and code == ABS_Y:
                    evdev_events.append(f"ABS_Y={value}")

        if pd != last_pendown or evdev_events or irq_delta > 0:
            last_pendown = pd
            print(f"  t={t:5.1f}s | pen_down={pd} | GPIO23={gpio23} | "
                  f"IRQ+{irq_delta} | evdev: {evdev_events or '[]'}")

        time.sleep(0.05)

    os.close(fd)
    irq_end, _ = read_irq_count()
    total_irqs = (irq_end or 0) - irq_start
    info(f"Monitor complete. Total ads7846 IRQs during session: {total_irqs}")

# ── SPI freeze diagnosis ──────────────────────────────────────────────────────

def diagnose_freeze():
    hdr("Freeze diagnosis — SPI bus sharing between fbcp and ADS7846")
    # Check if fbcp-ili9341 is running
    try:
        r = subprocess.run(["pgrep", "-l", "fbcp"], capture_output=True, timeout=3)
        if r.returncode == 0:
            procs = r.stdout.decode().strip()
            warn(f"fbcp-ili9341 IS running: {procs}")
            info("This is the likely cause of screen freeze when touched:")
            info("  ADS7846 IRQ fires → driver waits for SPI bus")
            info("  fbcp holds SPI bus for DMA transfers → stall in IRQ context")
            info("")
            info("  The build at build-safe-rot180 uses 'safe' access but may")
            info("  still conflict at high transfer rates. Possible mitigations:")
            info("  1) Reduce ads7846 SPI speed: edit config.txt speed= param")
            info("     Current: speed=50000 (50kHz). Try: speed=500000 (0.5MHz)")
            info("     Higher speed = shorter bus hold time per conversion")
            info("  2) Add debounce to reduce IRQ frequency:")
            info("     dtoverlay=ads7846,...,debounce=100")
            info("  3) Rebuild fbcp with --UPDATE_THRESHOLD=500 flag")
            info("     (reduces frame rate, gives ADS7846 more bus windows)")
            info("  4) Use EVIOCGRAB in touch_bridge to prevent Xorg double-processing")
        else:
            ok("fbcp-ili9341 is NOT running — SPI bus contention is not the cause")
    except Exception as e:
        warn(f"Could not check fbcp: {e}")

    # Check for chromium GPU errors
    try:
        r = subprocess.run(
            ["journalctl", "-u", "pinboard-kiosk", "-n", "50", "--no-pager",
             "--grep", "ContextResult"],
            capture_output=True, timeout=5
        )
        if r.stdout:
            lines = r.stdout.decode().strip().splitlines()
            if lines:
                warn("Chromium GPU errors detected (renderer falls back to software):")
                for l in lines[-5:]:
                    info(f"  {l}")
                info("  Mitigation: add --disable-gpu to kiosk.sh for stable software rendering")
    except Exception:
        pass

# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Touch GPIO monitor and simulator")
    parser.add_argument("--monitor", action="store_true", help="Monitor mode only")
    parser.add_argument("--sim", action="store_true", help="Simulate touch at all levels and exit")
    parser.add_argument("--duration", type=int, default=30, help="Monitor duration seconds (default 30)")
    parser.add_argument("--x", type=int, default=240, help="Simulated touch X coordinate")
    parser.add_argument("--y", type=int, default=160, help="Simulated touch Y coordinate")
    args = parser.parse_args()

    if os.geteuid() != 0:
        print(f"{RED}Run as root (sudo python3 touch_sim.py){RESET}")
        sys.exit(1)

    print(f"\n{CYAN}=== Memomatic Touch Diagnostic & Simulator ==={RESET}")
    print(f"Platform: {os.uname().machine}  Device: {DEVICE}\n")

    # Always run the checks
    check_l0()
    check_l1()
    check_l2()
    check_l3()
    diagnose_freeze()

    if args.monitor:
        check_l4(watch_secs=5)
        monitor_loop(duration=args.duration)
        return

    if args.sim:
        # Simulate at every available level
        check_l4(watch_secs=2)

        # L4: uinput
        sim_uinput(args.x, args.y)

        # L5: xdotool
        sim_l5_xdotool(args.x, args.y)
        time.sleep(0.5)

        # L6: check CDP state, then inject
        check_l6_cdp()
        sim_l6_cdp_inject(args.x, args.y)
        time.sleep(0.5)

        # Final CDP state check
        hdr("Final state check")
        check_l6_cdp()
        return

    # Default: check all levels + simulate via xdotool + verify via CDP
    check_l4(watch_secs=2)

    hdr("Running simulation sequence (xdotool + CDP verification)...")
    sim_l5_xdotool(args.x, args.y)
    time.sleep(0.5)
    visible = check_l6_cdp()
    if visible:
        ok("End-to-end simulation successful: xdotool → Chromium JS")
    else:
        fail("Simulation did not trigger menu — check touch_bridge.service")

    sim_l6_cdp_inject(args.x, args.y)

    hdr("Summary")
    print("  GPIO17 (T_IRQ): monitored via L0/L1/L2 (driver-owned, cannot simulate directly)")
    print("  GPIO23 (spare): monitored via L3 sysfs")
    print("  Simulation levels available:")
    print("    uinput  (L4) — kernel evdev injection (requires touch_bridge reading that device)")
    print("    xdotool (L5) — X11 synthetic pointer event")
    print("    CDP     (L6) — direct Chromium JS event injection (bypasses X11)")

if __name__ == "__main__":
    main()
