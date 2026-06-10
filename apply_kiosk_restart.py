#!/usr/bin/env python3
"""Apply kiosk.sh changes (--disable-gpu) by restarting pinboard-kiosk.service."""
import paramiko, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect("192.168.1.131", username="memomatic", password="memes", timeout=10)

def run(label, cmd, timeout=30):
    print(f"\n=== {label} ===")
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode(errors="replace").strip()
    err = stderr.read().decode(errors="replace").strip()
    if out: print(out)
    if err: print("[err]", err[:300])
    return out, err

# Restart kiosk (this will restart Xorg + Chromium; ~10s downtime)
print("Restarting pinboard-kiosk.service to apply --disable-gpu flag...")
print("(This causes ~15s of blank screen on the Pi)")
run("restart kiosk", "sudo systemctl restart pinboard-kiosk.service")
time.sleep(15)

run("kiosk active?", "sudo systemctl is-active pinboard-kiosk.service")
run("kiosk log", "journalctl -u pinboard-kiosk.service --since '1 minute ago' --no-pager -n 15 2>&1")
run("chromium running?", "pgrep -c chromium && echo 'chromium running' || echo 'chromium NOT running'")
run("chromium gpu flag", "ps aux | grep chromium | grep -o 'disable-gpu' | head -1 || echo 'no disable-gpu flag (flag may not show in ps output)'")

# After restart, touch_bridge may need to be restarted too (Xorg restarted)
print("\nRestarting touch_bridge (Xorg restarted so XAUTHORITY may have changed)...")
run("restart touch bridge", "sudo systemctl restart pinboard-touch.service")
time.sleep(12)
run("touch bridge active?", "sudo systemctl is-active pinboard-touch.service")
run("touch bridge log", "journalctl -u pinboard-touch.service --since '1 minute ago' --no-pager -n 10 2>&1")

# Verify xdotool still works after restart
run("xdotool test", "sudo DISPLAY=:0 XAUTHORITY=$(ls /tmp/serverauth.* 2>/dev/null | head -1 || echo /root/.Xauthority) xdotool getmouselocation 2>&1 || sudo DISPLAY=:0 XAUTHORITY=/root/.Xauthority xdotool getmouselocation 2>&1")
run("new serverauth", "ls /tmp/serverauth.*")

client.close()
print("\nKiosk restarted.")
