"""Poll GPIOs 17, 18, 24, 25, 26, 27 for 15 seconds to find T_IRQ."""
import time, sys, os

GPIOS = [17, 18, 24, 25, 26, 27]  # BCM numbers
GPIO_BASE = 512  # offset in /sys/kernel/debug/gpio on this Pi

def export_gpio(n):
    sysfs = n + GPIO_BASE
    export_path = f'/sys/class/gpio/gpio{sysfs}/value'
    if not os.path.exists(export_path):
        try:
            with open('/sys/class/gpio/export', 'w') as f:
                f.write(str(sysfs))
            time.sleep(0.1)
        except Exception as e:
            return None
    try:
        with open(f'/sys/class/gpio/gpio{sysfs}/direction', 'w') as f:
            f.write('in')
    except Exception:
        pass
    return export_path

files = {}
for g in GPIOS:
    path = export_gpio(g)
    if path and os.path.exists(path):
        files[g] = open(path)
        print(f"GPIO{g}: monitoring (sysfs gpio{g+GPIO_BASE})")
    else:
        print(f"GPIO{g}: could not export")

if not files:
    print("No GPIOs available to monitor")
    sys.exit(1)

# Read initial state
initial = {}
for g, f in files.items():
    f.seek(0)
    initial[g] = f.read().strip()
print(f"\nInitial states: {initial}")
print("\nTouch the screen now! Monitoring for 15 seconds...")
sys.stdout.flush()

start = time.time()
changes = {g: [] for g in files}

while time.time() - start < 15:
    for g, f in files.items():
        f.seek(0)
        val = f.read().strip()
        if val != initial[g] and (not changes[g] or changes[g][-1][1] != val):
            changes[g].append((time.time() - start, val))
            print(f"  t={time.time()-start:.2f}s GPIO{g} changed to {val}")
            sys.stdout.flush()
    time.sleep(0.01)

print("\nSummary of changes:")
for g in GPIOS:
    if g in changes:
        print(f"  GPIO{g}: {len(changes[g])} changes: {changes[g][:5]}")
    else:
        print(f"  GPIO{g}: not monitored")

for f in files.values():
    f.close()
