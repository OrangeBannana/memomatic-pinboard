"""Read raw evdev events from /dev/input/event0 for 10 seconds."""
import struct, time, sys, os

# Try both 16-byte (32-bit ARM) and 24-byte (64-bit) struct layouts
# Linux input_event on 64-bit: sec(8) + usec(8) + type(2) + code(2) + value(4) = 24 bytes
# Linux input_event on 32-bit: sec(4) + usec(4) + type(2) + code(2) + value(4) = 16 bytes

arch = os.uname().machine
print("arch:", arch)

if '64' in arch:
    FMT = 'qqHHI'
else:
    FMT = 'llHHI'

SZ = struct.calcsize(FMT)
print(f"event size: {SZ} bytes")

EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
ABS_NAMES = {0: 'ABS_X', 1: 'ABS_Y', 24: 'ABS_PRESSURE', 28: 'ABS_DISTANCE'}
KEY_NAMES = {330: 'BTN_TOUCH', 272: 'BTN_LEFT', 0x14a: 'BTN_TOUCH(0x14a)'}

count = 0
start = time.time()
deadline = start + 10

print(f"Reading /dev/input/event0 for 10 seconds — please touch the screen now!")
sys.stdout.flush()

try:
    with open('/dev/input/event0', 'rb') as f:
        f.fileno()  # ensure it's a real fd
        import select
        while time.time() < deadline:
            r, _, _ = select.select([f], [], [], 0.5)
            if r:
                data = f.read(SZ)
                if len(data) < SZ:
                    continue
                vals = struct.unpack(FMT, data)
                _, _, etype, code, value = vals
                count += 1
                if etype == EV_ABS:
                    name = ABS_NAMES.get(code, f'ABS_{code}')
                    print(f"  EV_ABS {name}={value}")
                elif etype == EV_KEY:
                    name = KEY_NAMES.get(code, f'KEY_{code}')
                    print(f"  EV_KEY {name} value={value}")
                elif etype == EV_SYN:
                    print(f"  EV_SYN")
                else:
                    print(f"  type={etype} code={code} value={value}")
                sys.stdout.flush()
except PermissionError as e:
    print(f"Permission denied: {e}")
except Exception as e:
    print(f"Error: {e}")

elapsed = time.time() - start
print(f"\nDone. {count} events in {elapsed:.1f}s")
