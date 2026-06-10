#!/usr/bin/env python3
"""Investigate GrabDevice=no and CDP connectivity."""
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

# 1) Xorg log — look for GrabDevice and ADS7846
run("Xorg log GrabDevice", "grep -i -E 'grab|ads7846|calibr|evdev|adding input|input device' /var/log/Xorg.0.log 2>/dev/null | head -40")
run("Xorg log errors", "grep -i error /var/log/Xorg.0.log 2>/dev/null | head -20")
run("list xorg conf files", "ls -la /etc/X11/xorg.conf.d/")
run("all xorg conf names", "for f in /etc/X11/xorg.conf.d/*.conf; do echo \"=== $f ===\"; cat $f; echo; done")

# 2) CDP connectivity from localhost
run("CDP port 9222 check", """
python3 -c "
import socket
try:
    s = socket.create_connection(('127.0.0.1', 9222), timeout=3)
    s.sendall(b'GET /json HTTP/1.1\r\nHost: 127.0.0.1:9222\r\nConnection: close\r\n\r\n')
    data = s.recv(4096)
    s.close()
    print('Connected. Response:', data[:200])
except Exception as e:
    print(f'FAILED: {e}')
" 2>&1
""")

# 3) Check if Chromium CDP is actually listening
run("netstat 9222", "ss -tlnp | grep 9222 || netstat -tlnp 2>/dev/null | grep 9222 || echo 'not listening'")
run("chromium cdp flag", "ps aux | grep chromium | grep -o 'remote-debugging[^ ]*'")

# 4) EVIOCGRAB investigation — who holds it?
run("evdev grab state", """
sudo python3 -c "
import fcntl, struct, os, errno
EVIOCGRAB = 0x40044590
dev = '/dev/input/event0'
try:
    fd = os.open(dev, os.O_RDONLY | os.O_NONBLOCK)
    try:
        fcntl.ioctl(fd, EVIOCGRAB, struct.pack('I', 1))
        fcntl.ioctl(fd, EVIOCGRAB, struct.pack('I', 0))
        print('NOT grabbed — GrabDevice=no working')
    except OSError as e:
        if e.errno == errno.EBUSY:
            # Check who has it: look at /proc
            import subprocess
            r = subprocess.run(['lsof', '/dev/input/event0'], capture_output=True, text=True)
            print('GRABBED (EBUSY). lsof output:')
            print(r.stdout or 'lsof not available')
            # Also check via /proc/*/fd
            import glob
            for proc_fd in glob.glob('/proc/*/fd/*'):
                try:
                    target = os.readlink(proc_fd)
                    if 'event0' in target:
                        pid = proc_fd.split('/')[2]
                        cmd = open(f'/proc/{pid}/cmdline').read().replace(chr(0), ' ').strip()
                        print(f'  PID {pid} has event0 open: {cmd[:80]}')
                except (OSError, PermissionError):
                    pass
        else:
            print(f'Unexpected error: {e}')
    os.close(fd)
except Exception as e:
    print(f'open error: {e}')
" 2>&1
""")

# 5) Check if GrabDevice=no is in the right file format
run("calibration conf detail", "cat /etc/X11/xorg.conf.d/99-calibration.conf 2>/dev/null || echo 'file not found'")
# Check for the AutoAddDevices=false conflict
run("serverflags in all confs", "grep -r -i 'AutoAdd\|AutoEnable\|GrabDevice' /etc/X11/xorg.conf.d/ 2>/dev/null")

# 6) Check what xorg actually loaded for input
run("xinput list", "DISPLAY=:0 XAUTHORITY=/root/.Xauthority xinput list 2>/dev/null || sudo DISPLAY=:0 XAUTHORITY=/root/.Xauthority xinput list 2>&1")

# 7) Check if touch_bridge is actually receiving events (monitor for 5s)
run("touch_bridge receiving?", """
sudo timeout 5 python3 -c "
import select, struct, os, time
FMT = 'llHHI'
SZ = struct.calcsize(FMT)
dev = '/dev/input/event0'
try:
    fd = os.open(dev, os.O_RDONLY | os.O_NONBLOCK)
    print(f'Opened {dev} for reading. Waiting 3s for any events...')
    deadline = time.time() + 3
    count = 0
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            data = os.read(fd, SZ * 16)
            for i in range(0, len(data)-SZ+1, SZ):
                _, _, etype, code, val = struct.unpack(FMT, data[i:i+SZ])
                count += 1
                print(f'  event: type={etype} code={code} val={val}')
    os.close(fd)
    print(f'Total events: {count} (0 expected without physical touch)')
except Exception as e:
    print(f'ERROR: {e}')
" 2>&1
""")

# 8) Check GPIO17 is actually ADS7846 penirq and NOT something else
run("gpio17 details via debug", "sudo cat /sys/kernel/debug/gpio 2>/dev/null | grep -E 'gpio-529|GPIO17'")
run("ads7846 penirq property", "cat /sys/bus/spi/devices/spi0.1/of_node/penirq 2>/dev/null || cat /proc/device-tree/soc/spi@7e204000/ads7846@1/penirq 2>/dev/null || echo 'no DT penirq'")

client.close()
print("\nDone.")
