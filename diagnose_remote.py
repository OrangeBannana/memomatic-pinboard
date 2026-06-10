#!/usr/bin/env python3
"""Run touch diagnostics on the Pi over SSH."""
import paramiko, sys

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
    return out, err

run("kernel/arch", "uname -m")
run("kernel modules", "lsmod | grep -E 'ads7846|spi|fb|ili|waveshare|fbtft|flexfb'")
run("dmesg touch", "sudo dmesg | grep -i -E 'ads7846|spi.*touch|tsc|waveshare|ili9341|fbtft|touch' | tail -30")
run("input devices", "ls -la /dev/input/ 2>/dev/null")
run("event names", "for f in /sys/class/input/event*/device/name; do echo \"$f: $(cat $f 2>/dev/null)\"; done")
run("proc interrupts", "cat /proc/interrupts | grep -E 'ads7846|spi|gpio|17|23|25'")
run("spi devices", "ls /sys/bus/spi/devices/ 2>/dev/null; for d in /sys/bus/spi/devices/*; do echo \"$d: $(cat $d/modalias 2>/dev/null)\"; done")
run("xorg config", "find /etc/X11 -name '*.conf' 2>/dev/null | xargs cat 2>/dev/null || echo 'no xorg conf'")
run("services status", "systemctl is-active pinboard-app pinboard-kiosk pinboard-touch 2>&1 || true")
run("touch service log", "journalctl -u pinboard-touch.service -n 40 --no-pager 2>&1")
run("kiosk log", "journalctl -u pinboard-kiosk.service -n 20 --no-pager 2>&1")
run("gpio sysfs list", "ls /sys/class/gpio/ 2>/dev/null")
run("gpio 17", "cat /sys/class/gpio/gpio17/value 2>/dev/null || echo 'gpio17 not exported'")
run("gpio 23", "cat /sys/class/gpio/gpio23/value 2>/dev/null || echo 'gpio23 not exported'")
run("gpio 25", "cat /sys/class/gpio/gpio25/value 2>/dev/null || echo 'gpio25 not exported'")
run("fbdev", "ls -la /dev/fb* 2>/dev/null")
run("boot config", "cat /boot/firmware/config.txt 2>/dev/null || cat /boot/config.txt 2>/dev/null | grep -v '^#' | grep -v '^$'")
run("pen_down", "ls /sys/bus/spi/devices/spi0.*/pen_down 2>/dev/null || echo 'no pen_down sysfs'")
run("event0 uevent", "cat /sys/class/input/event0/device/uevent 2>/dev/null")
run("xorg processes", "ps aux | grep -i xorg | grep -v grep")
run("chromium processes", "ps aux | grep chromium | grep -v grep | head -5")
run("touch_bridge file", "ls -la /home/memomatic/pinboard/app/touch_bridge.py 2>/dev/null || echo 'file not found'")
run("DISPLAY check", "DISPLAY=:0 XAUTHORITY=/root/.Xauthority xdotool getmouselocation 2>&1 || echo 'xdotool failed'")
run("gpio gpiochip", "ls /sys/class/gpio/ | grep gpiochip")
run("raspi gpio", "cat /sys/kernel/debug/gpio 2>/dev/null | head -40 || echo 'no kernel debug gpio'")

client.close()
print("\nDone.")
