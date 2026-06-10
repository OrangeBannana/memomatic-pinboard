#!/usr/bin/env python3
"""
raw_touch.py — minimal touch calibration helper
Run as root. Touches produce raw ADC output only, no framebuffer, no services stopped.

sudo python3 app/raw_touch.py

Touch each of the 4 corners of the TFT in this order:
  1. Top-left
  2. Top-right
  3. Bottom-right
  4. Bottom-left

Then Ctrl+C and paste the output.
"""
import os, subprocess, sys, time

GPIO  = "/sys/class/gpio/gpio529/value"
HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spi_touch_read")
LABELS = ["top-left", "top-right", "bottom-right", "bottom-left"]

if os.geteuid() != 0:
    print("Run as root: sudo python3 app/raw_touch.py")
    sys.exit(1)

# Export GPIO if needed
if not os.path.exists(GPIO):
    with open("/sys/class/gpio/export", "w") as f: f.write("529")
    time.sleep(0.1)
    with open("/sys/class/gpio/gpio529/direction", "w") as f: f.write("in")

count = 0
prev = 1
print(f"Touch the 4 corners in order: {', '.join(LABELS)}")
print("─" * 55)

while True:
    try:
        v = int(open(GPIO).read().strip())
    except Exception:
        time.sleep(0.05)
        continue

    if prev == 1 and v == 0:
        r = subprocess.run([HELPER], capture_output=True, timeout=0.5)
        coords = r.stdout.decode().strip()
        raw = r.stderr.decode().strip().replace("\n", "  ")
        label = LABELS[count] if count < len(LABELS) else f"touch{count+1}"
        print(f"[{count+1}] {label:12s}  coords={coords:12s}  {raw}")
        count += 1
        if count >= len(LABELS):
            print("─" * 55)
            print("Done — paste this output to Claude.")
            sys.exit(0)

    prev = v
    time.sleep(0.02)
