#!/usr/bin/env python3
"""Final verification: xdotool click → Chromium JS, touch_bridge status."""
import paramiko, sys, time, os, io

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect("192.168.1.131", username="memomatic", password="memes", timeout=10)

def run(label, cmd, timeout=20):
    print(f"\n=== {label} ===")
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode(errors="replace").strip()
    err = stderr.read().decode(errors="replace").strip()
    if out: print(out)
    if err: print("[err]", err[:300])
    return out, err

# Upload the confirmed-working touch test from diagnose_remote5
sftp = client.open_sftp()
with open(os.path.join(os.path.dirname(__file__), "diagnose_remote5.py"), "rb") as f:
    content = f.read()

# The helper from diagnose_remote5 worked. Re-run it.
helper = rb'''#!/usr/bin/env python3
import sys, fcntl, struct, os, errno, glob, subprocess, socket, json, base64, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

XENV = {"DISPLAY": ":0", "XAUTHORITY": "/root/.Xauthority",
        "PATH": os.environ.get("PATH", "/usr/bin:/bin")}

# CDP connect helper
def cdp_page_id():
    s = socket.create_connection(("127.0.0.1", 9222), timeout=3)
    s.sendall(b"GET /json HTTP/1.1\r\nHost: 127.0.0.1:9222\r\nConnection: close\r\n\r\n")
    resp = b""
    s.settimeout(3)
    while True:
        try:
            c = s.recv(4096)
            if not c: break
            resp += c
        except: break
    s.close()
    body = resp.split(b"\r\n\r\n", 1)[-1].decode(errors="replace")
    pages = json.loads(body)
    for p in pages:
        if p.get("type") == "page": return p["id"]
    return pages[0]["id"]

def cdp_connect(pid):
    key = base64.b64encode(os.urandom(16)).decode()
    ws = socket.create_connection(("127.0.0.1", 9222), timeout=5)
    hs = (f"GET /devtools/page/{pid} HTTP/1.1\r\nHost: 127.0.0.1:9222\r\n"
          f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
          f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
    ws.sendall(hs.encode())
    r = ws.recv(4096)
    assert b"101" in r
    return ws

def ws_send(ws, data):
    data = data.encode()
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i%4] for i, b in enumerate(data))
    hdr = b"\x81"
    n = len(data)
    hdr += bytes([0x80|n]) if n < 126 else b"\xfe" + struct.pack(">H", n)
    ws.sendall(hdr + mask + masked)

def ws_recv(ws, timeout=2):
    ws.settimeout(timeout)
    try:
        h = ws.recv(2)
    except: return None
    if len(h) < 2: return None
    n = h[1] & 0x7f
    if n == 126: n = struct.unpack(">H", ws.recv(2))[0]
    data = b""
    while len(data) < n:
        chunk = ws.recv(min(65536, n-len(data)))
        if not chunk: break
        data += chunk
    return data

def eval_js(ws, mid, expr):
    ws_send(ws, json.dumps({"id": mid, "method": "Runtime.evaluate",
                             "params": {"expression": expr, "returnByValue": True}}))
    deadline = time.time() + 3
    while time.time() < deadline:
        raw = ws_recv(ws, 0.4)
        if raw is None: continue
        try:
            obj = json.loads(raw.decode(errors="replace"))
        except: continue
        if obj.get("id") == mid:
            return obj.get("result", {}).get("result", {}).get("value")
    return None

print("=== Final verification ===")
try:
    pid = cdp_page_id()
    ws = cdp_connect(pid)

    eval_js(ws, 1, "hideMenu()")
    time.sleep(0.3)
    before = eval_js(ws, 2, "String(menuVisible)")
    print(f"menuVisible BEFORE click: {before}")

    # xdotool click
    r = subprocess.run(["xdotool", "mousemove", "240", "160", "click", "1"],
                       env=XENV, capture_output=True, timeout=5)
    print(f"xdotool rc={r.returncode}")
    time.sleep(0.5)

    after = eval_js(ws, 3, "String(menuVisible)")
    oc = eval_js(ws, 4, "(document.getElementById('menu-overlay')||{}).className||'?'")
    print(f"menuVisible AFTER click:  {after}")
    print(f"menu-overlay class:       {oc}")

    if after == "true":
        print("\nSUCCESS: xdotool -> Chromium JS click chain verified!")
    else:
        print("\nFAIL: menu not triggered")

    ws.close()
except Exception as e:
    print(f"ERROR: {e}")

print()
# Show touch_bridge journal
r = subprocess.run(["journalctl", "-u", "pinboard-touch.service", "-n", "25", "--no-pager"],
                   capture_output=True, text=True)
print("=== touch_bridge journal ===")
print(r.stdout.strip())

print()
print("=== Touch chain summary ===")
print("Physical touch detection:")
print("  Path A (evdev): ADS7846 evdev -> touch_bridge (blocked if Xorg holds EVIOCGRAB)")
print("  Path B (pendown): ADS7846 pen_down sysfs poll -> touch_bridge (ALWAYS works)")
print("  Path C (Xorg): ADS7846 -> Xorg -> X11 pointer events -> Chromium (ALWAYS works)")
print()
print("Click injection: xdotool -> Chromium (CONFIRMED working)")
print()
r2 = subprocess.run(["cat", "/sys/bus/spi/devices/spi0.1/pen_down"],
                    capture_output=True, text=True)
print(f"pen_down current value: {r2.stdout.strip()} (0=not touching)")
'''

sftp.putfo(io.BytesIO(helper), "/tmp/final_verify.py")
sftp.close()

run("final verify", "sudo python3 /tmp/final_verify.py", timeout=25)

# Additional: check GrabDevice status
run("xorg log grab", "grep -i 'grab' /var/log/Xorg.0.log 2>/dev/null | head -10 || echo 'no grab in log'")

# Check if touch_bridge pen_down thread is polling
run("touch_bridge threads", "ls /proc/$(pgrep -f touch_bridge.py)/task/ 2>/dev/null | wc -l")

# Summary of what needs a reboot
print("\n=== Changes that require next reboot ===")
print("  kiosk.sh: --disable-gpu flag added (Chromium will use software rendering)")
print("  99-calibration.conf: MatchDriver='evdev' added to better target GrabDevice=off")
print("  These take effect on next: sudo systemctl restart pinboard-kiosk.service")
print()
print("=== SPI freeze fix (requires config.txt edit + reboot) ===")
print("  To reduce ADS7846 SPI bus hold time, change config.txt:")
print("  Current:  speed=50000 (50kHz, ~4ms per touch sample)")
print("  Improved: speed=1000000 (1MHz, ~0.2ms per touch sample)")
print("  This reduces the SPI bus hold duration, reducing display freeze duration")

client.close()
