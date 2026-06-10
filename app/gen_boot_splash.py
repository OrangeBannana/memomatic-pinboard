#!/usr/bin/env python3
"""Generate app/boot_splash.png — run once to regenerate the splash image."""

import os
from PIL import Image, ImageDraw, ImageFont

W, H = 480, 320
BG        = (11,  11,  14)       # --bg
ACCENT    = (124, 106, 245)      # --accent
TEXT      = (237, 233, 225)      # --text
TEXT_DIM  = (100, 97,  92)       # muted

img  = Image.new("RGB", (W, H), BG)
draw = ImageDraw.Draw(img)

# Faint radial-ish top glow (horizontal bands fading from accent)
for y in range(H // 3):
    t   = 1.0 - y / (H / 3)
    a   = int(18 * t * t)
    col = tuple(min(255, BG[i] + int((ACCENT[i] - BG[i]) * a / 255)) for i in range(3))
    draw.line([(0, y), (W, y)], fill=col)

# Try system fonts; fall back to PIL default
def _font(size, bold=False):
    candidates = [
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'Bold' if not bold else '-Bold'}.ttf",
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()

font_title = _font(52, bold=True)
font_sub   = _font(20)
font_hint  = _font(14)

def text_center(draw, y, text, font, fill):
    bb  = draw.textbbox((0, 0), text, font=font)
    tw  = bb[2] - bb[0]
    draw.text(((W - tw) // 2, y), text, fill=fill, font=font)

# Accent pill above text
pill_w, pill_h = 48, 4
pill_x = (W - pill_w) // 2
pill_y = H // 2 - 64
draw.rounded_rectangle([pill_x, pill_y, pill_x + pill_w, pill_y + pill_h], radius=2, fill=ACCENT)

# Title
text_center(draw, H // 2 - 50, "Memomatic", font_title, TEXT)

# Subtitle
text_center(draw, H // 2 + 14, "Pinboard", font_sub, TEXT_DIM)

# Hint
text_center(draw, H - 30, "Starting up…", font_hint, (60, 58, 56))

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "boot_splash.png")
img.save(out, "PNG")
print(f"Saved {out}  ({W}×{H})")
