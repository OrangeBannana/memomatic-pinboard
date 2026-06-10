#!/usr/bin/env python3
"""Diagnose white screen after touch: check journals, fbcp, Chromium state."""
import paramiko, sys, time, io
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect("192.168.1.131", username="memomatic", password="memes", timeout=10)

def run(label, cmd, timeout=25):
    print(f"\n=== {label} ===")
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode(errors="replace").strip()
    err = stderr.read().decode(errors="replace").strip()
    if out: print(out)
    if err: print("[err]", err[:500])
    return out, err

# Service status
run("kiosk journal (last 40)", "journalctl -u pinboard-kiosk.service -n 40 --no-pager 2>&1")
run("touch bridge journal (last 40)", "journalctl -u pinboard-touch.service -n 40 --no-pager 2>&1")
run("app journal (last 20)", "journalctl -u pinboard-app.service -n 20 --no-pager 2>&1")

# fbcp status
run("fbcp running?", "pgrep -a fbcp 2>/dev/null || echo 'NO fbcp process'")
run("fbcp service", "sudo systemctl status fbcp.service 2>/dev/null | head -10 || sudo systemctl status fbi.service 2>/dev/null | head -5 || echo 'no fbcp service unit found'")

# Display / framebuffer
run("framebuffer", "ls -la /dev/fb* 2>/dev/null")
run("dmesg SPI/touch errors", "sudo dmesg | grep -iE 'spi|ads7846|ili9341|fbcp|oom|kill' | tail -20 2>&1")

# Chromium
run("chromium count", "pgrep -c chromium 2>/dev/null && echo processes || echo 'chromium NOT running'")

# CDP - what page is loaded?
cdp_script = b'''import socket, json, struct, base64, os, time

def cdp_pages():
    s = socket.create_connection(("127.0.0.1", 9222), timeout=3)
    s.sendall(b"GET /json HTTP/1.1\\r\\nHost: 127.0.0.1:9222\\r\\nConnection: close\\r\\n\\r\\n")
    resp = b""
    s.settimeout(3)
    while True:
        try:
            c = s.recv(4096)
            if not c: break
            resp += c
        except: break
    s.close()
    body = resp.split(b"\\r\\n\\r\\n", 1)[-1].decode(errors="replace")
    return json.loads(body)

try:
    pages = cdp_pages()
    for p in pages:
        print(f"  type={p.get('type')} url={p.get('url','?')} title={p.get('title','')[:60]}")
except Exception as e:
    print(f"CDP error: {e} (Chromium may be crashed or not on port 9222)")
'''

sftp = client.open_sftp()
sftp.putfo(io.BytesIO(cdp_script), "/tmp/check_cdp2.py")
sftp.close()
run("chromium pages via CDP", "python3 /tmp/check_cdp2.py 2>&1")

# Check if there's a crash dump
run("chromium crash", "ls /home/memomatic/pinboard/chromium-profile/Crash\\ Reports/ 2>/dev/null | tail -5 || ls /home/memomatic/pinboard/chromium-profile/ 2>/dev/null | head -20")

# pen_down current state
run("pen_down now", "cat /sys/bus/spi/devices/spi0.1/pen_down 2>/dev/null || echo 'sysfs not found'")

# touch_bridge process
run("touch_bridge threads", "pid=$(pgrep -f touch_bridge.py); echo pid=$pid; ls /proc/$pid/task/ 2>/dev/null | wc -l; ps -p $pid -o pid,stat,vsz,rss 2>/dev/null")

client.close()
print("\nDone.")
