import time, sys

print("Press and hold finger on touchscreen for 10 seconds!")
sys.stdout.flush()

with open('/sys/bus/spi/devices/spi0.1/pen_down') as f:
    for _ in range(40):
        f.seek(0)
        val = f.read().strip()
        print(f"{time.time():.3f} pen_down={val}")
        sys.stdout.flush()
        time.sleep(0.25)

# Also print IRQ count
with open('/proc/interrupts') as f:
    for line in f:
        if 'ads7846' in line:
            print("IRQ:", line.strip())
