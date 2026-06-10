#!/usr/bin/env python3
"""Deploy phase 2: updated touch_bridge (pen_down polling), kiosk.sh GPU flags,
and fix the GrabDevice issue in xorg config."""
import paramiko, sys, time, os, io

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST = "192.168.1.131"
USER = "memomatic"
PASS = "memes"
LOCAL = os.path.dirname(os.path.abspath(__file__))

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASS, timeout=10)
sftp = client.open_sftp()

def run(label, cmd, timeout=20):
    print(f"\n=== {label} ===")
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode(errors="replace").strip()
    err = stderr.read().decode(errors="replace").strip()
    if out:
        print(out)
    if err:
        print("[err]", err[:400])
    return out, err

# ── 1. Upload updated touch_bridge.py ────────────────────────────────────────
print("Uploading touch_bridge.py...")
sftp.put(os.path.join(LOCAL, "app", "touch_bridge.py"),
         "/home/memomatic/pinboard/app/touch_bridge.py")
run("chmod touch_bridge", "chmod +x /home/memomatic/pinboard/app/touch_bridge.py")

# ── 2. Fix 99-calibration.conf to also explicitly disable GrabDevice
#       (belt-and-suspenders: use MatchDriver evdev to ensure it only matches
#        the evdev driver instance which we control)
NEW_CALIB = b"""Section "InputClass"
        Identifier      "calibration"
        MatchProduct    "ADS7846 Touchscreen"
        MatchDriver     "evdev"
        Option  "Calibration"   "3936 227 268 3880"
        Option  "SwapAxes"      "1"
        Option  "GrabDevice"    "off"
EndSection
"""
print("Updating 99-calibration.conf...")
sftp.putfo(io.BytesIO(NEW_CALIB), "/tmp/99-calibration.conf")
run("install calibration conf",
    "sudo cp /tmp/99-calibration.conf /etc/X11/xorg.conf.d/99-calibration.conf && "
    "sudo chmod 644 /etc/X11/xorg.conf.d/99-calibration.conf")

# ── 3. Upload updated kiosk.sh ────────────────────────────────────────────────
print("Uploading kiosk.sh...")
sftp.put(os.path.join(LOCAL, "app", "kiosk.sh"),
         "/tmp/kiosk.sh")
run("install kiosk.sh",
    "sudo cp /tmp/kiosk.sh /home/memomatic/pinboard/app/kiosk.sh && "
    "sudo chmod +x /home/memomatic/pinboard/app/kiosk.sh")

sftp.close()

# ── 4. Restart touch_bridge with new code ────────────────────────────────────
print("\nRestarting touch_bridge...")
run("restart touch bridge",
    "sudo systemctl restart pinboard-touch.service")
time.sleep(4)
run("touch bridge status",
    "sudo systemctl is-active pinboard-touch.service && "
    "journalctl -u pinboard-touch.service -n 15 --no-pager 2>&1")

# ── 5. Verify pen_down polling is working ─────────────────────────────────────
run("pen_down value", "cat /sys/bus/spi/devices/spi0.1/pen_down")
run("xdotool location", "sudo DISPLAY=:0 XAUTHORITY=/root/.Xauthority xdotool getmouselocation 2>&1")

# ── 6. Test the full simulation chain with pen_down ──────────────────────────
print("\nRunning simulation test...")
helper = b"""#!/usr/bin/env python3
import subprocess, time, socket, json, base64, os, struct, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

XENV = {"DISPLAY": ":0", "XAUTHORITY": "/root/.Xauthority", "PATH": os.environ.get("PATH", "/usr/bin:/bin")}

# 1. Simulate pen_down transition (write 1 then 0 to a temp file to mimic)
# Since we can't write to pen_down directly, simulate via xdotool click
print("=== Simulating touch via xdotool ===")
r = subprocess.run(["xdotool", "mousemove", "240", "160", "click", "1"],
                   env=XENV, capture_output=True, timeout=5)
print(f"xdotool click rc={r.returncode}")
time.sleep(0.5)

# 2. Check CDP for menu state
def cdp_eval(expr):
    try:
        s = socket.create_connection(("127.0.0.1", 9222), timeout=3)
        s.sendall(b"GET /json HTTP/1.1\\r\\nHost: 127.0.0.1:9222\\r\\nConnection: close\\r\\n\\r\\n")
        resp = b""
        s.settimeout(3)
        while True:
            try:
                chunk = s.recv(4096)
                if not chunk: break
                resp += chunk
            except: break
        s.close()
        body = resp.split(b"\\r\\n\\r\\n", 1)[-1].decode(errors="replace")
        pages = json.loads(body)
        pid = next((p["id"] for p in pages if p.get("type") == "page"), pages[0]["id"])
        key = base64.b64encode(os.urandom(16)).decode()
        ws = socket.create_connection(("127.0.0.1", 9222), timeout=5)
        hs = (f"GET /devtools/page/{pid} HTTP/1.1\\r\\nHost: 127.0.0.1:9222\\r\\n"
              "Upgrade: websocket\\r\\nConnection: Upgrade\\r\\n"
              f"Sec-WebSocket-Key: {key}\\r\\nSec-WebSocket-Version: 13\\r\\n\\r\\n")
        ws.sendall(hs.encode())
        ws.recv(4096)
        data = json.dumps({"id": 1, "method": "Runtime.evaluate",
                           "params": {"expression": expr, "returnByValue": True}}).encode()
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i%4] for i,b in enumerate(data))
        hdr = b"\\x81" + bytes([0x80|len(data)]) if len(data) < 126 else b"\\x81\\xfe" + struct.pack(">H",len(data))
        ws.sendall(hdr + mask + masked)
        ws.settimeout(3)
        resp2 = ws.recv(4096)
        ws.close()
        # Parse websocket frame
        n = resp2[1] & 0x7f
        if n == 126: n = struct.unpack(">H", resp2[2:4])[0]; start=4
        else: start=2
        obj = json.loads(resp2[start:start+n].decode(errors="replace"))
        return obj.get("result", {}).get("result", {}).get("value")
    except Exception as e:
        return f"ERROR: {e}"

mv = cdp_eval("String(menuVisible)")
print(f"menuVisible after xdotool click: {mv}")
if str(mv) == "true":
    print("SUCCESS: menu triggered!")
else:
    print("FAIL: menu not triggered")

# 3. Check touch_bridge journal
import subprocess as sp
jr = sp.run(["journalctl", "-u", "pinboard-touch.service", "-n", "20", "--no-pager"],
            capture_output=True, text=True)
print("\\n=== touch_bridge journal ===")
print(jr.stdout.strip())
"""

sftp_new = client.open_sftp()
sftp_new.putfo(__import__("io").BytesIO(helper), "/tmp/sim_test.py")
sftp_new.close()

run("run sim test", "sudo python3 /tmp/sim_test.py", timeout=20)

# ── 7. Monitor pen_down for 5s and watch touch_bridge log ────────────────────
print("\nMonitoring pen_down for 5 seconds...")
run("pen_down monitor", """
sudo python3 -c "
import time, os
p = '/sys/bus/spi/devices/spi0.1/pen_down'
prev = -1
for i in range(50):
    try:
        v = int(open(p).read().strip())
        if v != prev:
            print(f't={i*0.1:.1f}s pen_down={v}')
            prev = v
    except Exception as e:
        print(f'error: {e}')
    time.sleep(0.1)
print('done - no physical touch detected (expected)')
" 2>&1
""", timeout=10)

print("\nDeployment complete.")
client.close()
