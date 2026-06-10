#!/usr/bin/env python3
"""Focused tests: EVIOCGRAB holder, CDP, xinput."""
import paramiko, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect("192.168.1.131", username="memomatic", password="memes", timeout=10)

def run(label, cmd, timeout=20):
    print(f"\n{'='*60}")
    print(f"## {label}")
    print(f"{'='*60}")
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode(errors="replace").strip()
    err = stderr.read().decode(errors="replace").strip()
    if out:
        print(out)
    if err:
        print("[stderr]:", err[:600])
    return out, err

# Upload a helper script file to avoid heredoc issues
import paramiko.sftp_client

helper = b'''#!/usr/bin/env python3
import sys, fcntl, struct, os, errno, glob, subprocess, socket, json, base64, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

EVIOCGRAB = 0x40044590
dev = "/dev/input/event0"

print("=== EVIOCGRAB test ===")
try:
    fd = os.open(dev, os.O_RDONLY | os.O_NONBLOCK)
    try:
        fcntl.ioctl(fd, EVIOCGRAB, struct.pack("I", 1))
        fcntl.ioctl(fd, EVIOCGRAB, struct.pack("I", 0))
        print("OK: NOT grabbed. GrabDevice=no is working.")
    except OSError as e:
        if e.errno == errno.EBUSY:
            print("GRABBED (EBUSY). Finding who holds it...")
            for fd_link in glob.glob("/proc/*/fd/*"):
                try:
                    target = os.readlink(fd_link)
                    if "event0" in target:
                        pid = fd_link.split("/")[2]
                        try:
                            cmd_str = open(f"/proc/{pid}/cmdline").read().replace(chr(0), " ").strip()
                        except:
                            cmd_str = "?"
                        print(f"  PID {pid}: {cmd_str[:100]}")
                except (OSError, PermissionError):
                    pass
        else:
            print(f"Unexpected errno: {e.errno}")
    os.close(fd)
except Exception as e:
    print(f"open error: {e}")

print()
print("=== CDP connection test ===")
try:
    s = socket.create_connection(("127.0.0.1", 9222), timeout=3)
    req = b"GET /json HTTP/1.1\\r\\nHost: 127.0.0.1:9222\\r\\nConnection: close\\r\\n\\r\\n"
    s.sendall(req)
    resp = b""
    s.settimeout(3)
    while True:
        try:
            chunk = s.recv(4096)
            if not chunk:
                break
            resp += chunk
        except:
            break
    s.close()
    body = resp.split(b"\\r\\n\\r\\n", 1)[-1].decode(errors="replace")
    pages = json.loads(body) if body.startswith("[") else []
    print(f"Connected to CDP! {len(pages)} page(s)")
    for p in pages:
        print(f"  {p.get('type')}: {p.get('url', '')[:60]}")
    page_id = None
    for p in pages:
        if p.get("type") == "page":
            page_id = p["id"]
            break
    if not page_id and pages:
        page_id = pages[0]["id"]
    print(f"  Using page id: {page_id}")
except Exception as e:
    print(f"CDP connection failed: {e}")
    page_id = None

print()
print("=== xinput device list ===")
r = subprocess.run(
    ["xinput", "list"],
    env={"DISPLAY": ":0", "XAUTHORITY": "/root/.Xauthority", "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    capture_output=True, text=True, timeout=5
)
print(r.stdout.strip() or "(empty)")
if r.stderr:
    print("[stderr]:", r.stderr.strip()[:200])

print()
print("=== xdotool click and CDP verify ===")
if page_id:
    # Connect to CDP
    key = base64.b64encode(os.urandom(16)).decode()
    ws = socket.create_connection(("127.0.0.1", 9222), timeout=5)
    hs = (f"GET /devtools/page/{page_id} HTTP/1.1\\r\\nHost: 127.0.0.1:9222\\r\\n"
          f"Upgrade: websocket\\r\\nConnection: Upgrade\\r\\n"
          f"Sec-WebSocket-Key: {key}\\r\\nSec-WebSocket-Version: 13\\r\\n\\r\\n")
    ws.sendall(hs.encode())
    r = ws.recv(4096)
    assert b"101" in r, f"WS fail: {r[:100]}"

    def ws_send(data):
        data = data.encode()
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i%4] for i,b in enumerate(data))
        hdr = b"\\x81"
        n = len(data)
        hdr += bytes([0x80|n]) if n < 126 else b"\\xfe" + struct.pack(">H", n)
        ws.sendall(hdr + mask + masked)

    def ws_recv(timeout=2):
        ws.settimeout(timeout)
        try:
            h = ws.recv(2)
        except:
            return None
        if len(h) < 2: return None
        n = h[1] & 0x7f
        if n == 126: n = struct.unpack(">H", ws.recv(2))[0]
        data = b""
        while len(data) < n:
            chunk = ws.recv(min(65536, n-len(data)))
            if not chunk: break
            data += chunk
        return data

    def eval_js(mid, expr):
        ws_send(json.dumps({"id": mid, "method": "Runtime.evaluate",
                             "params": {"expression": expr, "returnByValue": True}}))
        deadline = time.time() + 3
        while time.time() < deadline:
            raw = ws_recv(0.4)
            if raw is None: continue
            try:
                obj = json.loads(raw.decode(errors="replace"))
            except: continue
            if obj.get("id") == mid:
                return obj.get("result", {}).get("result", {}).get("value")
        return None

    # Hide menu, then click, then check
    eval_js(1, "hideMenu()")
    time.sleep(0.3)
    mv_before = eval_js(2, "String(menuVisible)")
    print(f"menuVisible before click: {mv_before}")

    # Inject click via xdotool
    xenv = {"DISPLAY": ":0", "XAUTHORITY": "/root/.Xauthority", "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    r2 = subprocess.run(["xdotool", "mousemove", "240", "160", "click", "1"],
                        env=xenv, capture_output=True, timeout=5)
    print(f"xdotool rc={r2.returncode} {r2.stderr.decode().strip()}")
    time.sleep(0.5)

    mv_after = eval_js(3, "String(menuVisible)")
    oc = eval_js(4, "document.getElementById(\\'menu-overlay\\').className")
    print(f"menuVisible after click: {mv_after}")
    print(f"menu-overlay className: {oc}")

    if mv_after == "true":
        print("SUCCESS: xdotool click triggered menu in Chromium!")
    else:
        print("FAIL: menu not triggered by xdotool click")

    ws.close()
else:
    print("No CDP page available to test")

print()
print("=== touch_bridge journal (last 10 lines) ===")
r = subprocess.run(
    ["journalctl", "-u", "pinboard-touch.service", "-n", "10", "--no-pager"],
    capture_output=True, text=True, timeout=5
)
print(r.stdout.strip())
'''

sftp = client.open_sftp()
with sftp.file("/tmp/touch_test.py", "w") as f:
    f.write(helper.decode())
sftp.close()
print("Helper script uploaded.")

run("Run touch test as root", "sudo python3 /tmp/touch_test.py", timeout=30)
run("touch_bridge journal", "journalctl -u pinboard-touch.service -n 20 --no-pager 2>&1")
run("Xorg log tail", "tail -20 /var/log/Xorg.0.log 2>/dev/null")

client.close()
print("\nDone.")
