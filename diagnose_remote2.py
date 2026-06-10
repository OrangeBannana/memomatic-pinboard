#!/usr/bin/env python3
"""Detailed touch/service diagnostics - phase 2."""
import paramiko, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect("192.168.1.131", username="memomatic", password="memes", timeout=10)

def run(label, cmd, timeout=15):
    print(f"\n{'='*60}")
    print(f"## {label}")
    print(f"{'='*60}")
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout, get_pty=False)
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    if out.strip():
        print(out.strip())
    if err.strip():
        print("[stderr]:", err.strip()[:800])
    return out, err

# Check kiosk service file on Pi vs repo
run("kiosk service on pi", "cat /etc/systemd/system/pinboard-kiosk.service 2>/dev/null || cat /lib/systemd/system/pinboard-kiosk.service 2>/dev/null")
run("touch service on pi", "cat /etc/systemd/system/pinboard-touch.service 2>/dev/null || echo 'not installed'")
run("app service on pi", "cat /etc/systemd/system/pinboard-app.service 2>/dev/null")
run("touch service status", "sudo systemctl status pinboard-touch.service 2>&1 || true")
run("all services", "sudo systemctl status pinboard-app pinboard-kiosk pinboard-touch 2>&1 | head -60 || true")
run("dmesg recent", "sudo dmesg | tail -40")
run("xauth check root", "sudo ls -la /root/.Xauthority 2>/dev/null && sudo cat /root/.Xauthority | xxd | head -5 2>/dev/null || echo 'no /root/.Xauth'")
run("xauth list", "sudo DISPLAY=:0 XAUTHORITY=/root/.Xauthority xauth list 2>&1 || echo 'xauth failed'")
run("xdotool test root", "sudo DISPLAY=:0 XAUTHORITY=/root/.Xauthority xdotool getmouselocation 2>&1")
# Check if gpio529 (BCM17) can be exported and read
run("gpio529 export", """
sudo bash -c '
GPIO=529
if [ ! -d /sys/class/gpio/gpio$GPIO ]; then
  echo "$GPIO" > /sys/class/gpio/export 2>&1 && echo "exported gpio$GPIO" || echo "export failed"
else
  echo "gpio$GPIO already exported"
fi
cat /sys/class/gpio/gpio$GPIO/value 2>/dev/null && echo "value read ok" || echo "value read failed"
cat /sys/class/gpio/gpio$GPIO/direction 2>/dev/null || echo "no direction"
'
""")
run("gpio535 export (gpio23)", """
sudo bash -c '
GPIO=535
if [ ! -d /sys/class/gpio/gpio$GPIO ]; then
  echo "$GPIO" > /sys/class/gpio/export 2>&1 && echo "exported gpio$GPIO" || echo "export failed"
else
  echo "gpio$GPIO already exported"
fi
cat /sys/class/gpio/gpio$GPIO/value 2>/dev/null || echo "value read failed"
'
""")
# Read evdev events non-blocking
run("evdev event0 poll 3s", """
sudo timeout 3 python3 -c "
import struct, select, os, time
FMT = 'llHHI'
SZ = struct.calcsize(FMT)
fd = os.open('/dev/input/event0', os.O_RDONLY | os.O_NONBLOCK)
print('device opened, reading for 3s...')
deadline = time.time() + 3
count = 0
while time.time() < deadline:
    r, _, _ = select.select([fd], [], [], 0.2)
    if r:
        data = os.read(fd, SZ * 16)
        for i in range(0, len(data) - SZ + 1, SZ):
            _, _, etype, code, value = struct.unpack(FMT, data[i:i+SZ])
            count += 1
            print(f'  ev type={etype} code={code} val={value}')
os.close(fd)
print(f'total {count} events (none expected without physical touch)')
" 2>&1
""")
# Simulate a touch event using evemu-event or similar
run("evemu available", "which evemu-event evemu-play uinput 2>/dev/null || echo 'none found'")
run("python3-evdev", "python3 -c 'import evdev; print(evdev.__file__)' 2>/dev/null || echo 'evdev not available'")
run("uinput module", "lsmod | grep uinput; ls /dev/uinput 2>/dev/null || echo 'no /dev/uinput'")
# Check for freeze-related kernel messages
run("kernel oops/errors", "sudo dmesg | grep -i -E 'oops|panic|hang|freeze|lock|stuck|spi.*error|ads7846.*error' | tail -20")
run("pen_down current", "cat /sys/bus/spi/devices/spi0.1/pen_down 2>/dev/null")
run("irq count ads7846", "grep -E 'ads7846|199' /proc/interrupts")
run("chromium gpu flags", "ps aux | grep chromium | grep -v grep | head -1 | tr ' ' '\n' | grep -E 'gpu|gl|accel|render|swiftshader'")

client.close()
print("\nDone.")
