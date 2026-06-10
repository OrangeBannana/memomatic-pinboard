#!/usr/bin/env python3
"""
touch_diagnose.py — ADS7846 touch end-to-end diagnostic

Run with: sudo python3 /tmp/touch_diagnose.py

Tests every layer from hardware IRQ to JavaScript event in sequence,
pausing for physical touches where needed. Each layer prints PASS/FAIL
plus the raw data so you can tell exactly where the chain breaks.
"""
import fcntl, struct, os, sys, time, subprocess, socket, base64, json, select, errno

# ── helpers ────────────────────────────────────────────────────────────────

XENV = {'DISPLAY': ':0', 'XAUTHORITY': '/root/.Xauthority', **os.environ}
GREEN  = '\033[92m'
RED    = '\033[91m'
YELLOW = '\033[93m'
RESET  = '\033[0m'

results = {}

def hdr(title):
    print(f'\n{"="*60}')
    print(f'  {title}')
    print('='*60)

def info(msg):  print(f'  {msg}')
def ok(msg):    print(f'  {GREEN}PASS{RESET}  {msg}')
def fail(msg):  print(f'  {RED}FAIL{RESET}  {msg}')
def warn(msg):  print(f'  {YELLOW}WARN{RESET}  {msg}')
def prompt(msg):
    print(f'\n  >>> {msg}')
    input('      Press ENTER when ready... ')

def run(*args, env=None, timeout=3):
    try:
        r = subprocess.run(list(args), capture_output=True, env=env or XENV,
                           timeout=timeout)
        return r.stdout.decode(errors='replace'), r.stderr.decode(errors='replace'), r.returncode
    except subprocess.TimeoutExpired:
        return '', 'TIMEOUT', -1
    except FileNotFoundError:
        return '', f'not found: {args[0]}', -1

# ── Layer 0: identify devices ───────────────────────────────────────────────

hdr('Layer 0: Input device identification')
for i in range(4):
    name_path = f'/sys/class/input/event{i}/device/name'
    if os.path.exists(name_path):
        name = open(name_path).read().strip()
        info(f'event{i}: {name}')
        if 'ADS7846' in name or 'ads7846' in name:
            ok(f'ADS7846 found at /dev/input/event{i}')

dev_path = '/dev/input/event0'
dev_name = open('/sys/class/input/event0/device/name').read().strip() if os.path.exists('/sys/class/input/event0/device/name') else 'unknown'
info(f'Using {dev_path} ({dev_name})')

# ── Layer 1: EVIOCGRAB check ────────────────────────────────────────────────

hdr('Layer 1: EVIOCGRAB — is Xorg holding exclusive grab?')
EVIOCGRAB = 0x40044590
try:
    fd = os.open(dev_path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        fcntl.ioctl(fd, EVIOCGRAB, struct.pack('I', 1))
        fcntl.ioctl(fd, EVIOCGRAB, struct.pack('I', 0))
        ok('Device is NOT grabbed — touch_bridge can read events')
        results['grab'] = 'free'
    except OSError as e:
        if e.errno == errno.EBUSY:
            fail('Device IS GRABBED by Xorg (EBUSY) — touch_bridge gets NOTHING')
            fail('Fix: ensure 99-calibration.conf has  Option "GrabDevice" "no"')
            info('Check: /etc/X11/xorg.conf.d/99-calibration.conf')
            results['grab'] = 'grabbed'
        else:
            warn(f'EVIOCGRAB returned unexpected error: {e}')
            results['grab'] = f'error:{e.errno}'
    os.close(fd)
except OSError as e:
    fail(f'Cannot open {dev_path}: {e}')
    results['grab'] = f'open_error:{e.errno}'

# ── Layer 2: Raw evdev read ─────────────────────────────────────────────────

hdr('Layer 2: Raw evdev events — touch the screen now')
EVENT_FMT  = 'llHHI'
EVENT_SIZE = struct.calcsize(EVENT_FMT)
EV_KEY, EV_ABS, EV_SYN = 1, 3, 0
BTN_TOUCH = 330
ABS_X, ABS_Y = 0, 1

prompt('Touch and release the screen 3-4 times within the next 10 seconds')

events_seen = []
abs_x = abs_y = 0
try:
    fd = os.open(dev_path, os.O_RDONLY | os.O_NONBLOCK)
    deadline = time.time() + 10
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.2)
        if not r:
            continue
        data = os.read(fd, EVENT_SIZE * 16)
        for i in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
            chunk = data[i:i+EVENT_SIZE]
            if len(chunk) < EVENT_SIZE:
                break
            _, _, etype, code, value = struct.unpack(EVENT_FMT, chunk)
            if etype == EV_ABS and code == ABS_X:
                abs_x = value
            elif etype == EV_ABS and code == ABS_Y:
                abs_y = value
            elif etype == EV_KEY and code == BTN_TOUCH:
                state = 'DOWN' if value else 'UP'
                events_seen.append({'state': state, 'x': abs_x, 'y': abs_y})
                info(f'BTN_TOUCH {state}  raw({abs_x},{abs_y})')
    os.close(fd)
except OSError as e:
    fail(f'Cannot read {dev_path}: {e}')

touch_count = len([e for e in events_seen if e['state'] == 'DOWN'])
if touch_count > 0:
    ok(f'Received {touch_count} BTN_TOUCH DOWN events from kernel')
    results['evdev'] = 'pass'
elif results.get('grab') == 'grabbed':
    fail('0 events — expected because Xorg has EVIOCGRAB. Fix Layer 1 first.')
    results['evdev'] = 'blocked'
else:
    fail('0 BTN_TOUCH events — device not reporting touches')
    results['evdev'] = 'fail'

# ── Layer 3: IRQ counter ────────────────────────────────────────────────────

hdr('Layer 3: IRQ counter — /proc/interrupts')

def read_irqs():
    irqs = {}
    for line in open('/proc/interrupts'):
        parts = line.split()
        if not parts:
            continue
        # Sum all CPU columns, grab label at end
        try:
            counts = [int(x) for x in parts[1:] if x.isdigit()]
            total = sum(counts)
            label = ' '.join(parts[len(counts)+1:])
            irqs[label] = total
        except (ValueError, IndexError):
            pass
    return irqs

before = read_irqs()
prompt('Touch the screen 5-10 times')
after = read_irqs()

for label, count_after in after.items():
    if 'ads7846' in label.lower() or 'ADS7846' in label:
        delta = count_after - before.get(label, 0)
        if delta > 0:
            ok(f'IRQ "{label}": +{delta} counts')
            results['irq'] = 'pass'
        else:
            fail(f'IRQ "{label}": delta={delta} — no IRQs fired')
            results['irq'] = 'fail'

if 'irq' not in results:
    # Try to find by GPIO
    for label, count_after in after.items():
        if 'gpio' in label.lower() or 'GPIO' in label or '17' in label:
            delta = count_after - before.get(label, 0)
            if delta > 0:
                info(f'GPIO IRQ "{label}": +{delta}')
    fail('No ads7846 IRQ entry found in /proc/interrupts')
    results['irq'] = 'no_entry'

# ── Layer 4: pen_down sysfs ─────────────────────────────────────────────────

hdr('Layer 4: pen_down sysfs — hold finger on screen')
pen_path = '/sys/bus/spi/devices/spi0.1/pen_down'
if not os.path.exists(pen_path):
    warn(f'{pen_path} does not exist — skip')
    results['pendown'] = 'no_sysfs'
else:
    prompt('HOLD your finger on the screen for 5 seconds (do not release)')
    readings = []
    deadline = time.time() + 5
    while time.time() < deadline:
        val = open(pen_path).read().strip()
        readings.append(val)
        time.sleep(0.1)
    ones = readings.count('1')
    info(f'pen_down readings: {ones}/{len(readings)} were "1" while holding')
    if ones > 10:
        ok(f'pen_down reports 1 during touch ({ones} readings)')
        results['pendown'] = 'pass'
    else:
        fail(f'pen_down mostly 0 while touching ({ones} "1" readings) — SPI/IRQ wiring issue')
        results['pendown'] = 'fail'

# ── Layer 5: GPIO poll ─────────────────────────────────────────────────────

hdr('Layer 5: GPIO poll — GPIO17 and GPIO25 (T_IRQ candidates)')

def export_gpio(n):
    p = f'/sys/class/gpio/gpio{n}'
    if not os.path.exists(p):
        try:
            with open('/sys/class/gpio/export', 'w') as f:
                f.write(str(n))
            time.sleep(0.1)
        except OSError:
            pass
    return os.path.exists(p)

gpio_toggles = {17: 0, 25: 0}
for g in (17, 25):
    export_gpio(g)

prompt('Touch and release 5 times')

for _ in range(50):
    for g in (17, 25):
        p = f'/sys/class/gpio/gpio{g}/value'
        if os.path.exists(p):
            v = open(p).read().strip()
            if v == '0':  # active low
                gpio_toggles[g] += 1
    time.sleep(0.1)

for g in (17, 25):
    count = gpio_toggles[g]
    p = f'/sys/class/gpio/gpio{g}'
    if not os.path.exists(p):
        warn(f'GPIO{g}: not exported (probably in use)')
    elif count > 3:
        ok(f'GPIO{g}: went low {count} times — this is T_IRQ!')
    elif count > 0:
        info(f'GPIO{g}: went low {count} times')
    else:
        info(f'GPIO{g}: never went low')

# ── Layer 6: Xorg cursor movement ──────────────────────────────────────────

hdr('Layer 6: Xorg cursor movement — does touch move cursor?')
out, _, rc = run('xdotool', 'getmouselocation')
if rc != 0:
    warn(f'xdotool not available: {_}')
    results['cursor'] = 'no_xdotool'
else:
    x0 = y0 = 0
    for part in out.split():
        if part.startswith('x:'): x0 = int(part[2:])
        if part.startswith('y:'): y0 = int(part[2:])
    info(f'Cursor before: ({x0},{y0})')
    prompt('Touch different corners of the screen')
    out2, _, _ = run('xdotool', 'getmouselocation')
    x1 = y1 = 0
    for part in out2.split():
        if part.startswith('x:'): x1 = int(part[2:])
        if part.startswith('y:'): y1 = int(part[2:])
    info(f'Cursor after:  ({x1},{y1})')
    moved = abs(x1-x0) > 5 or abs(y1-y0) > 5
    if moved:
        ok(f'Cursor moved ({x0},{y0}) → ({x1},{y1}) — Xorg receives ABS events')
        results['cursor'] = 'pass'
    else:
        fail('Cursor did not move — Xorg not receiving touch motion events')
        results['cursor'] = 'fail'

# ── Layer 7: xdotool click injection (synthetic click) ────────────────────

hdr('Layer 7: xdotool synthetic click → Chrome JS event')

# We need the CDP page ID
def get_page_id():
    try:
        s = socket.create_connection(('127.0.0.1', 9222), timeout=3)
        req = b'GET /json HTTP/1.1\r\nHost: 127.0.0.1:9222\r\nConnection: close\r\n\r\n'
        s.sendall(req)
        resp = b''
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            resp += chunk
        s.close()
        body = resp.split(b'\r\n\r\n', 1)[-1].decode(errors='replace')
        pages = json.loads(body)
        for p in pages:
            if p.get('type') == 'page' and 'frame' in p.get('url', ''):
                return p['id']
        return pages[0]['id'] if pages else None
    except Exception as e:
        return None

page_id = get_page_id()
if not page_id:
    warn('Cannot connect to Chrome CDP on port 9222 — skip CDP layers')
    results['xdotool_click'] = 'no_cdp'
    results['cdp_js'] = 'no_cdp'
else:
    # CDP websocket helper
    def cdp_connect(pid):
        s = socket.create_connection(('127.0.0.1', 9222), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        hs = (f'GET /devtools/page/{pid} HTTP/1.1\r\n'
              f'Host: 127.0.0.1:9222\r\n'
              'Upgrade: websocket\r\nConnection: Upgrade\r\n'
              f'Sec-WebSocket-Key: {key}\r\n'
              'Sec-WebSocket-Version: 13\r\n\r\n')
        s.sendall(hs.encode())
        resp = s.recv(4096)
        assert b'101' in resp, f'WS handshake failed: {resp[:200]}'
        return s

    def ws_send(s, data):
        data = data.encode()
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        hdr = b'\x81'
        n = len(data)
        if n < 126:
            hdr += bytes([0x80 | n])
        else:
            hdr += b'\xfe' + struct.pack('>H', n)
        s.sendall(hdr + mask + masked)

    def ws_recv(s, timeout=2):
        s.settimeout(timeout)
        try:
            hdr = s.recv(2)
        except socket.timeout:
            return None
        if len(hdr) < 2:
            return None
        n = hdr[1] & 0x7F
        if n == 126:
            n = struct.unpack('>H', s.recv(2))[0]
        elif n == 127:
            n = struct.unpack('>Q', s.recv(8))[0]
        data = b''
        while len(data) < n:
            chunk = s.recv(min(65536, n - len(data)))
            if not chunk:
                break
            data += chunk
        return data

    def cdp_eval(s, mid, expr, timeout=3):
        ws_send(s, json.dumps({'id': mid, 'method': 'Runtime.evaluate',
                               'params': {'expression': expr, 'returnByValue': True}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = ws_recv(s, timeout=0.3)
            if raw is None:
                continue
            try:
                obj = json.loads(raw.decode(errors='replace'))
            except Exception:
                continue
            if obj.get('id') == mid:
                return obj.get('result', {}).get('result', {}).get('value')
        return None

    # Test xdotool click → JS
    try:
        ws = cdp_connect(page_id)
        cdp_eval(ws, 1, 'hideMenu()')
        time.sleep(0.5)
        mv_before = cdp_eval(ws, 2, 'menuVisible')
        info(f'menuVisible before xdotool click: {mv_before}')

        # Get current cursor position
        out, _, _ = run('xdotool', 'getmouselocation')
        cx = cy = 240
        for part in out.split():
            if part.startswith('x:'): cx = int(part[2:])
            if part.startswith('y:'): cy = int(part[2:])

        run('xdotool', 'click', '1')
        time.sleep(0.5)
        mv_after = cdp_eval(ws, 3, 'menuVisible')
        info(f'menuVisible after  xdotool click: {mv_after}')

        if mv_after:
            ok('xdotool click → menuVisible=true — synthetic click path works')
            results['xdotool_click'] = 'pass'
        else:
            fail('xdotool click did not set menuVisible=true')
            results['xdotool_click'] = 'fail'
        ws.close()
    except Exception as e:
        fail(f'xdotool/CDP test error: {e}')
        results['xdotool_click'] = f'error:{e}'

# ── Layer 8: touch_bridge service status ───────────────────────────────────

hdr('Layer 8: touch_bridge service + event log')
out, _, rc = run('systemctl', 'is-active', 'pinboard-touch.service', env=os.environ)
active = out.strip() == 'active'
if active:
    ok('pinboard-touch.service is active')
else:
    fail(f'pinboard-touch.service is NOT active: {out.strip()}')

# Check journal for BTN_TOUCH events
journal_out, _, _ = run('journalctl', '-u', 'pinboard-touch.service',
                        '--since', '10 minutes ago', '--no-pager', '-n', '50',
                        env=os.environ, timeout=5)
lines = [l for l in journal_out.splitlines() if 'touch_bridge' in l.lower()]
info(f'Recent touch_bridge journal lines ({len(lines)} total):')
for l in lines[-10:]:
    info(f'  {l}')
if any('BTN_TOUCH' in l or 'xdotool' in l or 'inject' in l.lower() for l in lines):
    ok('touch_bridge has logged touch activity')
    results['bridge'] = 'pass'
elif any('device opened' in l for l in lines):
    warn('touch_bridge opened device but has not logged any BTN_TOUCH events')
    results['bridge'] = 'open_no_events'
else:
    fail('touch_bridge has no activity logged')
    results['bridge'] = 'fail'

# ── Layer 9: CDP JS event monitoring ───────────────────────────────────────

hdr('Layer 9: CDP JavaScript event monitoring (10 seconds)')
if page_id:
    try:
        ws2 = cdp_connect(page_id)
        setup_js = '''
(function() {
  window._diag_events = [];
  var _handlers = ['touchstart','pointerdown','click','mousedown'];
  _handlers.forEach(function(t) {
    document.addEventListener(t, function(e) {
      window._diag_events.push({
        type: e.type,
        t: Date.now(),
        x: e.clientX || (e.touches&&e.touches[0]&&e.touches[0].clientX) || 0,
        y: e.clientY || (e.touches&&e.touches[0]&&e.touches[0].clientY) || 0
      });
    }, {capture:true, passive:true});
  });
  hideMenu();
  return 'setup ok';
})()
'''
        r = cdp_eval(ws2, 10, setup_js)
        info(f'Event monitor setup: {r}')
        prompt('Touch the screen 3-5 times within the next 10 seconds, then press ENTER')
        time.sleep(10)
        r2 = cdp_eval(ws2, 20,
                      'JSON.stringify({count:window._diag_events.length,'
                      'mv:menuVisible,last:window._diag_events.slice(-5)})')
        if r2:
            data = json.loads(r2)
            cnt = data.get('count', 0)
            mv  = data.get('mv', False)
            evs = data.get('last', [])
            info(f'menuVisible: {mv}')
            info(f'Total JS events received: {cnt}')
            for ev in evs:
                info(f'  {ev}')
            if cnt > 0:
                ok(f'Chromium received {cnt} touch/pointer/click events')
                results['cdp_js'] = 'pass'
            elif mv:
                ok('menuVisible=true — events triggered menu even if not captured')
                results['cdp_js'] = 'pass'
            else:
                fail('Chromium received 0 touch events from physical touch')
                results['cdp_js'] = 'fail'
        ws2.close()
    except Exception as e:
        fail(f'CDP JS monitoring error: {e}')
        results['cdp_js'] = f'error:{e}'

# ── Summary ─────────────────────────────────────────────────────────────────

hdr('SUMMARY')
checks = [
    ('grab',          'EVIOCGRAB (device not grabbed)'),
    ('evdev',         'Raw evdev BTN_TOUCH events'),
    ('irq',           'IRQ counter delta'),
    ('pendown',       'pen_down sysfs'),
    ('cursor',        'Xorg cursor movement'),
    ('xdotool_click', 'xdotool click → JS event'),
    ('bridge',        'touch_bridge has events'),
    ('cdp_js',        'Chromium JS touch events'),
]
for key, label in checks:
    val = results.get(key, 'not_tested')
    if val == 'pass':
        ok(label)
    elif val in ('not_tested', 'no_sysfs', 'no_xdotool', 'no_cdp'):
        warn(f'{label}: {val}')
    else:
        fail(f'{label}: {val}')

print()
# Diagnose the break point
if results.get('grab') == 'grabbed':
    print(f'{RED}ROOT CAUSE: Xorg has EVIOCGRAB on the touch device.{RESET}')
    print('  touch_bridge cannot read any events.')
    print('  Fix: ensure /etc/X11/xorg.conf.d/99-calibration.conf has:')
    print('       Option "GrabDevice" "no"')
    print('  Then restart the kiosk: sudo systemctl restart pinboard-kiosk.service')
elif results.get('evdev') == 'pass' and results.get('bridge') in ('open_no_events', 'fail'):
    print(f'{YELLOW}touch_bridge is not logging events even though evdev works.{RESET}')
    print('  The bridge may have started before Xorg initialized the device.')
    print('  Fix: sudo systemctl restart pinboard-touch.service')
elif results.get('xdotool_click') == 'pass' and results.get('cdp_js') == 'fail':
    print(f'{RED}xdotool works but physical touch produces no JS events.{RESET}')
    print('  touch_bridge is the missing link — ensure it is reading evdev events.')
elif results.get('cdp_js') == 'pass':
    print(f'{GREEN}All layers functional — physical touch is reaching Chromium!{RESET}')
else:
    print(f'{YELLOW}Check individual FAIL layers above to trace the break.{RESET}')
