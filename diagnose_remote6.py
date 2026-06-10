#!/usr/bin/env python3
"""Check triggerhappy config and fix touch routing."""
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
    out = stdout.read().decode(errors="replace").strip()
    err = stderr.read().decode(errors="replace").strip()
    if out:
        print(out)
    if err:
        print("[stderr]:", err[:400])
    return out, err

# thd full command line
run("thd full cmdline", "sudo cat /proc/391/cmdline 2>/dev/null | tr '\\0' ' ' || ps aux | grep thd | grep -v grep")
run("thd status", "sudo systemctl status triggerhappy --no-pager -n 5 2>&1")
run("thd config", "ls /etc/triggerhappy/triggers.d/ && cat /etc/triggerhappy/triggers.d/*.conf 2>/dev/null || echo 'no trigger files'")
run("thd service file", "cat /lib/systemd/system/triggerhappy.service 2>/dev/null || cat /etc/systemd/system/triggerhappy.service 2>/dev/null")
run("thd grab check", "cat /proc/391/status 2>/dev/null | head -10; ls -la /proc/391/fd/ 2>/dev/null | grep event")

# Check if thd uses --grab flag
run("thd man help", "thd --help 2>&1 | head -20 || triggerhappy-udev --help 2>&1 | head -10 || echo 'no help'")

# Test: temporarily stop thd and check if EVIOCGRAB is released
run("stop thd temporarily", "sudo systemctl stop triggerhappy 2>&1 || true")
import time
time.sleep(1)
run("EVIOCGRAB after thd stop", """
sudo python3 -c "
import fcntl, struct, os, errno
EVIOCGRAB = 0x40044590
fd = os.open('/dev/input/event0', os.O_RDONLY | os.O_NONBLOCK)
try:
    fcntl.ioctl(fd, EVIOCGRAB, struct.pack('I', 1))
    fcntl.ioctl(fd, EVIOCGRAB, struct.pack('I', 0))
    print('NOT grabbed after thd stopped')
except OSError as e:
    if e.errno == errno.EBUSY:
        import glob
        print('Still grabbed. Processes with event0:')
        for fd_link in glob.glob('/proc/*/fd/*'):
            try:
                if 'event0' in os.readlink(fd_link):
                    pid = fd_link.split('/')[2]
                    print(f'  PID {pid}:', open(f'/proc/{pid}/cmdline').read().replace(chr(0),' ')[:80])
            except: pass
os.close(fd)
" 2>&1
""")
run("restart thd", "sudo systemctl start triggerhappy 2>&1 || true")

# Also: test what happens with physical-touch pipeline if thd is stopped
# (We can't test physical touch but we can verify touch_bridge reads from event0)
run("evdev read with thd stopped attempt", """
sudo timeout 3 python3 -c "
import select, struct, os, time
FMT = 'llHHI'
SZ = struct.calcsize(FMT)
try:
    fd = os.open('/dev/input/event0', os.O_RDONLY | os.O_NONBLOCK)
    print('Opened event0 for reading. Waiting 2s...')
    r, _, _ = select.select([fd], [], [], 2.0)
    print('readable:', bool(r))
    os.close(fd)
    print('Success - device readable without EVIOCGRAB')
except Exception as e:
    print(f'Error: {e}')
" 2>&1
""")

client.close()
print("\nDone.")
