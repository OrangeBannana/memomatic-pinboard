#!/usr/bin/env python3
"""Deploy touch fixes to Pi and run the simulation test."""
import paramiko, sys, time, os

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST = "192.168.1.131"
USER = "memomatic"
PASS = "memes"

LOCAL_BASE = os.path.dirname(os.path.abspath(__file__))

FILES_TO_UPLOAD = [
    # (local_path, remote_path, sudo_copy_dest)
    (
        os.path.join(LOCAL_BASE, "app", "touch_bridge.py"),
        "/home/memomatic/pinboard/app/touch_bridge.py",
        None,
    ),
    (
        os.path.join(LOCAL_BASE, "touch_sim.py"),
        "/tmp/touch_sim.py",
        None,
    ),
    (
        os.path.join(LOCAL_BASE, "systemd", "pinboard-touch.service"),
        "/tmp/pinboard-touch.service",
        "/etc/systemd/system/pinboard-touch.service",
    ),
]

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASS, timeout=10)
sftp = client.open_sftp()


def run(cmd, timeout=20, show=True):
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout, get_pty=False)
    out = stdout.read().decode(errors="replace").strip()
    err = stderr.read().decode(errors="replace").strip()
    if show:
        if out:
            print(out)
        if err:
            print("[err]", err[:400])
    return out, err


print("=== Uploading files ===")
for local, remote, sudo_dest in FILES_TO_UPLOAD:
    print(f"  {os.path.basename(local)} → {remote}")
    sftp.put(local, remote)
    run(f"chmod +x {remote}", show=False)
    if sudo_dest:
        run(f"sudo cp {remote} {sudo_dest} && sudo chmod 644 {sudo_dest}", show=True)

sftp.close()

print("\n=== Reloading systemd and installing touch service ===")
run("sudo systemctl daemon-reload")
run("sudo systemctl enable pinboard-touch.service")
print("Stopping old touch service (if running)...")
run("sudo systemctl stop pinboard-touch.service 2>/dev/null || true", show=False)
print("Starting touch service...")
run("sudo systemctl start pinboard-touch.service")
time.sleep(3)
out, _ = run("sudo systemctl is-active pinboard-touch.service")
print(f"touch service status: {out}")

print("\n=== Touch service journal ===")
run("journalctl -u pinboard-touch.service -n 20 --no-pager 2>&1", timeout=10)

print("\n=== Running touch_sim.py --sim (full simulation test) ===")
out, err = run(
    "sudo python3 /tmp/touch_sim.py --sim",
    timeout=30,
)
if out:
    print(out)
if err:
    print("[stderr]", err[:600])

print("\n=== Final touch service status ===")
run("sudo systemctl status pinboard-touch.service --no-pager -n 10 2>&1")

client.close()
print("\nDone.")
