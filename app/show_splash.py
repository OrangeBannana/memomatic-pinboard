#!/usr/bin/env python3
"""Write boot_splash.png to /dev/fb0 so fbcp-ili9341 mirrors it to the TFT.
Runs once at boot as a systemd oneshot service and exits immediately."""

import os, struct, sys

FB   = "/dev/fb0"
HERE = os.path.dirname(os.path.abspath(__file__))
PNG  = os.path.join(HERE, "boot_splash.png")


def _read_fb_info():
    base = "/sys/class/graphics/fb0"
    with open(os.path.join(base, "virtual_size")) as f:
        w, h = map(int, f.read().strip().split(","))
    with open(os.path.join(base, "bits_per_pixel")) as f:
        bpp = int(f.read().strip())
    return w, h, bpp


def main():
    try:
        from PIL import Image
    except ImportError:
        sys.exit(0)  # Pillow not available — non-fatal

    try:
        fw, fh, bpp = _read_fb_info()
    except Exception:
        sys.exit(0)

    try:
        img = Image.open(PNG).resize((fw, fh), Image.LANCZOS).convert("RGB")
    except Exception:
        sys.exit(0)

    try:
        if bpp == 16:
            r, g, b = img.split()
            rb, gb, bb = r.tobytes(), g.tobytes(), b.tobytes()
            data = bytearray(fw * fh * 2)
            for i in range(fw * fh):
                pixel = ((rb[i] & 0xF8) << 8) | ((gb[i] & 0xFC) << 3) | (bb[i] >> 3)
                struct.pack_into("<H", data, i * 2, pixel)
        else:
            # 24 or 32-bit — write RGBX (pad to 4 bytes per pixel if needed)
            if bpp == 32:
                out = img.convert("RGBA")
                # Force full alpha so the pixel is opaque
                r, g, b, _ = out.split()
                a = Image.new("L", img.size, 255)
                out = Image.merge("RGBA", (r, g, b, a))
                data = out.tobytes()
            else:
                data = img.tobytes()

        with open(FB, "wb") as fb:
            fb.write(bytes(data))
    except Exception:
        pass  # Non-fatal: boot continues normally


if __name__ == "__main__":
    main()
