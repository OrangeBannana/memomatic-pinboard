#!/usr/bin/env python3
"""Targeted freeze/fbcp/uinput diagnostics."""
import paramiko, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect("192.168.1.131", username="memomatic", password="memes", timeout=10)

def run(label, cmd, timeout=15):
    print(f"\n{'='*60}")
    print(f"## {label}")
    print(f"{'='*60}")
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    if out.strip():
        print(out.strip())
    if err.strip():
        print("[stderr]:", err.strip()[:800])
    return out.strip(), err.strip()

run("fbcp processes", "ps aux | grep -i fbcp | grep -v grep || echo 'no fbcp running'")
run("fbcp service status", "sudo systemctl status fbcp-ili9341 2>&1 | head -10 || echo 'service not found'")
run("all procs", "ps aux --sort=pid | grep -v grep | tail -30")
run("spi bus check", "ls /sys/bus/spi/devices/ && cat /sys/bus/spi/devices/spi0.0/modalias 2>/dev/null || echo 'spi0.0 no modalias'")
run("dmesg spi", "sudo dmesg | grep -i spi | head -20")
# Test uinput simulation
run("uinput permissions", "ls -la /dev/uinput")
run("uinput simulate BTN_TOUCH", """
sudo python3 -c "
import struct, time, os

UINPUT = '/dev/uinput'
# uinput setup: UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_ABSBIT, etc.
UI_SET_EVBIT  = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_SET_ABSBIT = 0x40045567
UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502

EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
BTN_TOUCH = 0x14a
ABS_X, ABS_Y = 0, 1

import fcntl

fd = os.open(UINPUT, os.O_WRONLY | os.O_NONBLOCK)

# Enable event types
fcntl.ioctl(fd, UI_SET_EVBIT, EV_SYN)
fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
fcntl.ioctl(fd, UI_SET_EVBIT, EV_ABS)
fcntl.ioctl(fd, UI_SET_KEYBIT, BTN_TOUCH)
fcntl.ioctl(fd, UI_SET_ABSBIT, ABS_X)
fcntl.ioctl(fd, UI_SET_ABSBIT, ABS_Y)

# uinput_setup struct: {id: {bustype, vendor, product, version}, name, ff_effects_max}
# On 32-bit ARM: struct uinput_setup = input_id(8) + name(80) + ff_effects_max(4) = 92 bytes
UI_DEV_SETUP = 0x405c5503
setup = struct.pack('HHHHx80sx4s',  # input_id + name padded + ff_effects_max
    0x18, 0, 0x1EA6, 0,  # BUS_SPI, vendor=0, product=ADS7846, version=0
    b'SimulatedTouch',
    b'\\x00\\x00\\x00\\x00'
)

# Alternative: use the older uinput_user_dev struct
# struct uinput_user_dev: name(80) + input_id(8) + ff_effects_max(4) + absmax[ABS_CNT](4*64) + absmin + absfuzz + absflat
UINPUT_DEV_FMT = '80sHHHHi' + 'i'*64 + 'i'*64 + 'i'*64 + 'i'*64
ABS_CNT = 64
dev_data = struct.pack(
    '80sHHHHi' + 'i'*ABS_CNT * 4,
    b'SimulatedTouch'.ljust(80, b'\\x00'),
    0x18,   # BUS_SPI
    0,      # vendor
    0x1EA6, # product (ADS7846)
    0,      # version
    0,      # ff_effects_max
    *([0]*ABS_CNT),   # absmax
    *([0]*ABS_CNT),   # absmin
    *([0]*ABS_CNT),   # absfuzz
    *([0]*ABS_CNT),   # absflat
)
os.write(fd, dev_data)
os.ioctl = fcntl.ioctl

try:
    fcntl.ioctl(fd, UI_DEV_CREATE)
    print('uinput device created')
    time.sleep(0.5)

    def emit(etype, code, value):
        event = struct.pack('llHHI', 0, 0, etype, code, value)
        os.write(fd, event)

    # Simulate touch down + up
    emit(EV_ABS, ABS_X, 240)
    emit(EV_ABS, ABS_Y, 160)
    emit(EV_KEY, BTN_TOUCH, 1)
    emit(EV_SYN, 0, 0)
    time.sleep(0.1)
    emit(EV_KEY, BTN_TOUCH, 0)
    emit(EV_SYN, 0, 0)
    print('touch event emitted')
    time.sleep(0.5)

    fcntl.ioctl(fd, UI_DEV_DESTROY)
    print('device destroyed')
except Exception as e:
    print(f'ERROR: {e}')
finally:
    os.close(fd)
" 2>&1
""")
# Check evdev after simulation
run("check evdev after sim", """
sudo timeout 2 python3 -c "
import struct, select, os, time
FMT = 'llHHI'
SZ = struct.calcsize(FMT)
# Find event devices
import glob
devs = glob.glob('/dev/input/event*')
print('Available:', devs)
for dev in devs:
    name_path = dev.replace('/dev/input/', '/sys/class/input/').replace('event', 'event') + '/../device/name'
    try:
        import os.path
        name_file = '/sys/class/input/' + dev.split('/')[-1] + '/device/name'
        name = open(name_file).read().strip()
        print(f'{dev}: {name}')
    except:
        pass
" 2>&1
""")
# Check xdotool now vs before to see if cursor moved from simulation
run("cursor position", "sudo DISPLAY=:0 XAUTHORITY=/root/.Xauthority xdotool getmouselocation 2>&1")
# Test xdotool click → verify JS sees it via CDP
run("xdotool click test", """
sudo bash -c '
export DISPLAY=:0
export XAUTHORITY=/root/.Xauthority
xdotool mousemove 240 160
xdotool click 1
echo "click injected at 240,160"
' 2>&1
""")
# Check CDP for menuVisible after the click
run("CDP menuVisible check", """
python3 -c "
import socket, json, struct, os, time, base64

def get_page_id():
    s = socket.create_connection(('127.0.0.1', 9222), timeout=5)
    req = b'GET /json HTTP/1.1\r\nHost: 127.0.0.1:9222\r\nConnection: close\r\n\r\n'
    s.sendall(req)
    resp = b''
    while True:
        chunk = s.recv(4096)
        if not chunk: break
        resp += chunk
    s.close()
    body = resp.split(b'\r\n\r\n', 1)[-1]
    pages = json.loads(body)
    for p in pages:
        if p.get('type') == 'page':
            return p['id']
    return pages[0]['id']

pid = get_page_id()
print('page:', pid)

s = socket.create_connection(('127.0.0.1', 9222), timeout=5)
key = base64.b64encode(os.urandom(16)).decode()
hs = (f'GET /devtools/page/{pid} HTTP/1.1\r\nHost: 127.0.0.1:9222\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n')
s.sendall(hs.encode())
resp = s.recv(4096)
assert b'101' in resp

def ws_send(sock, data):
    data = data.encode()
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i%4] for i,b in enumerate(data))
    hdr = b'\x81'
    n = len(data)
    if n < 126: hdr += bytes([0x80|n])
    else: hdr += b'\xfe' + struct.pack('>H', n)
    sock.sendall(hdr + mask + masked)

def ws_recv(sock, timeout=2):
    sock.settimeout(timeout)
    try:
        hdr = sock.recv(2)
    except socket.timeout:
        return None
    if len(hdr) < 2: return None
    n = hdr[1] & 0x7f
    if n == 126: n = struct.unpack('>H', sock.recv(2))[0]
    data = b''
    while len(data) < n:
        chunk = sock.recv(min(65536, n-len(data)))
        if not chunk: break
        data += chunk
    return data

def eval_js(sock, mid, expr):
    ws_send(sock, json.dumps({'id': mid, 'method': 'Runtime.evaluate', 'params': {'expression': expr, 'returnByValue': True}}))
    deadline = time.time() + 3
    while time.time() < deadline:
        raw = ws_recv(sock)
        if raw is None: continue
        obj = json.loads(raw.decode(errors='replace'))
        if obj.get('id') == mid:
            return obj.get('result', {}).get('result', {}).get('value')
    return 'timeout'

mv = eval_js(s, 1, 'String(menuVisible)')
print('menuVisible:', mv)
loc = eval_js(s, 2, '(function(){var l=document.getElementById(\"menu-overlay\");return l?l.className:\"no element\";})()')
print('menu-overlay class:', loc)
s.close()
" 2>&1
""")

client.close()
print("\nDone.")
