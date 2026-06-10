# Memomatic Pinboard — Design Guidelines

## Overview

All three UI surfaces (admin, guest, frame) share one unified visual language: **dark glassmorphism**. The starting reference is the translucent menu overlay in `frame.html` — translucent panels, backdrop blur, warm-white text, and a vibrant accent colour on a deep-dark background. Admin and guest pages follow the same system.

---

## Color System

All colours are CSS custom properties defined in `:root`. This makes them overridable at runtime for user-configurable colour schemes (see the colour-scheme customisation issue).

### Base tokens

| Token | Default | Purpose |
|-------|---------|---------|
| `--bg` | `#0b0b0e` | Page background |
| `--glass-bg` | `rgba(20, 20, 28, 0.82)` | Panel / card fill |
| `--glass-border` | `rgba(255, 255, 255, 0.10)` | Panel borders |
| `--glass-blur` | `20px` | Backdrop blur radius |
| `--text` | `#ede9e1` | Primary text (warm off-white) |
| `--text-muted` | `rgba(237, 233, 225, 0.58)` | Secondary / label text |
| `--text-subtle` | `rgba(237, 233, 225, 0.30)` | Tertiary / hint text |
| `--line` | `rgba(255, 255, 255, 0.08)` | Dividers |

### Accent tokens (defaults — designed to be swapped per colour scheme)

| Token | Default | Purpose |
|-------|---------|---------|
| `--accent` | `#7c6af5` | Primary actions, focus rings |
| `--accent-dim` | `rgba(124, 106, 245, 0.16)` | Subtle tinted fills |
| `--danger` | `#e85454` | Destructive actions |
| `--danger-dim` | `rgba(232, 84, 84, 0.14)` | Danger fill backgrounds |
| `--success` | `#52c47a` | Confirmations / success states |

### Dark mode design intent

- Background is `#0b0b0e` — deep navy-black, not pure `#000000`. Pure black looks flat on OLED and creates harshness on TFT.
- A very faint radial accent gradient at the top of the page (`rgba(accent, 0.07)`) adds depth without visible colour.
- Panels use transparency + blur so the background gradient/imagery reads through. This is consistent with `frame.html`'s translucent menu.
- Text uses warm off-white (`#ede9e1`) not pure white, which reduces harshness on dark backgrounds and feels more premium.

---

## Typography

| Property | Value |
|----------|-------|
| Font stack | `system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif` |
| Base size | `15px` (not `14px`) |
| Min body text | `14px` |
| Min label text | `13px` |
| Min hint text | `12px` |
| Heading weight | `650` or `700` |
| Letter spacing | `0` on headings (avoids overly spaced feel) |

Never use font sizes below `11px` — the physical screen makes small text unreadable.

---

## Touch and Screen Size Constraints

### Physical display

The primary display is an **Inland 3.5-inch TFT at 480 × 320 logical pixels** (about 160 DPI). This is extremely small — roughly ¼ the screen area of a modern smartphone. Every pixel-level decision for `frame.html` must account for this.

Key physical facts:
- A typical adult fingertip is 8–10 mm wide. At 160 DPI that is roughly **50–63 pixels**. A button smaller than 44 px risks the user missing it entirely.
- The screen diagonal is ~3.5 inches. At 480 × 320, **three rows of UI is the practical maximum** before scrolling is needed.
- There is no native OS virtual keyboard — `frame.html` provides its own custom onscreen keyboard.

### Resistive touchscreen reliability

This device uses an **ADS7846 resistive touchscreen**, which is fundamentally less accurate than modern capacitive screens:

| Characteristic | Implication |
|----------------|-------------|
| Tap position can drift ± 5 mm | Hit-test area must be much larger than the visible button |
| Light touches may not register | Buttons must be visually prominent so users press firmly |
| No multi-touch | All interactions are single-pointer only |
| Stylus gives more precision | Fingertip input requires larger targets |
| Calibration helps but drift recurs | Do not rely on pixel-perfect tap registration |

**Design rule: every interactive element in `frame.html` must have a minimum touch target of 48 × 48 px. Prefer 56 px height for primary actions.** Increase spacing between adjacent touch targets to at least 12 px so a drifted tap does not land on the wrong element.

The on-screen keyboard keys use `height: 36px` which is an acceptable trade-off given the keyboard fills the full width and keys are wide. Never reduce below `32px` for keyboard keys.

### Frame UI layout budget (480 × 320 px)

The menu panel sits at the bottom of the 480 × 320 frame. Available height is approximately 220 px for the panel after leaving image visible above it. At 48 px per button, that allows for **3–4 interactive rows** maximum. Keep the menu compact.

| Element | Min height |
|---------|-----------|
| Menu header row | 48 px |
| Action button | 48 px |
| Keyboard key | 36 px |
| Close button (×) | 44 × 44 px |
| Network form input | 44 px |

### Web UIs (admin and guest)

`admin.html` and `guest.html` are used on phones and laptops, not the physical TFT. Follow standard mobile-web guidelines:

| Rule | Value |
|------|-------|
| Min button height | 48 px |
| Min input height | 48 px |
| Min tap target (any element) | 44 × 44 px |
| Comfortable button padding | `12 px` top/bottom, `20 px` left/right |
| Font size for interactive labels | ≥ 15 px |

Assume the user is holding a phone one-handed with their thumb. Avoid placing critical actions (delete, submit) where thumb reach is awkward at the top-right of a tall card.

---

## Component Patterns

### Glass panel

```css
.panel {
  background: var(--glass-bg);                         /* rgba(20,20,28,0.82) */
  border: 1px solid var(--glass-border);               /* rgba(255,255,255,0.10) */
  border-radius: 16px;
  backdrop-filter: blur(var(--glass-blur)) saturate(1.3);
  -webkit-backdrop-filter: blur(var(--glass-blur)) saturate(1.3);
}
```

### Primary button

```css
button {
  min-height: 48px;
  padding: 11px 20px;
  border: 1px solid transparent;
  border-radius: 10px;
  background: var(--accent);
  color: #fff;
  font-size: 15px;
  font-weight: 600;
}
```

### Secondary / ghost button

```css
button.secondary {
  background: rgba(255, 255, 255, 0.08);
  border-color: rgba(255, 255, 255, 0.13);
  color: var(--text);
}
```

### Danger button

```css
button.danger {
  background: var(--danger-dim);
  border-color: var(--danger);
  color: var(--danger);
}
```

### Text input / select

```css
input, select, textarea {
  min-height: 48px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.11);
  border-radius: 10px;
  color: var(--text);
  padding: 11px 14px;
  font: inherit;
  font-size: 15px;
}
input:focus, select:focus, textarea:focus {
  outline: none;
  border-color: var(--accent);
}
```

---

## Future: Colour Scheme Customisation

All colour values live in CSS custom properties on `:root`. The colour-scheme feature (tracked separately) will:

1. Store named schemes as JSON in the `settings` table.
2. On page load, fetch the active scheme and call `document.documentElement.style.setProperty('--accent', value)` etc.
3. The admin console will expose a colour picker / scheme editor.

When adding new UI elements, always use a token from the table above — never hardcode a hex colour outside `:root`.

---

## Page-level Notes

### `frame.html` (physical TFT kiosk)
- All interactions are touch-only; no hover states matter here.
- Menu must remain compact enough to show part of the current image behind it.
- The onscreen keyboard must not obstruct the SSID / password fields — use the `kbd-open` class on the overlay to float it to the top when the keyboard is visible.

### `admin.html` (owner web UI, phone/laptop)
- Two-column adaptive grid for image cards (`minmax(160px, 1fr)`).
- Actions grid inside each card uses `1fr 1fr` with the last odd-position button spanning full width to avoid orphaned half-rows.
- Settings use `auto-fit minmax(155px, 1fr)` — adapts from 2 to 4 columns without JS.

### `guest.html` (guest web UI, phone)
- Fullscreen card layout, centred vertically; optimised for one-handed use.
- Tabs use pill/segment style at the top of the card.
- A single large submit button per tab; no secondary actions.

### Boot splash (`app/boot_splash.png`)
- Canvas size: **480 × 320 px** (matches TFT logical resolution; `show_splash.py` scales to actual framebuffer if different).
- Background: `#0b0b0e` — same as `--bg`, keeps visual continuity with `frame.html`.
- Layout (top→bottom): faint accent radial glow at top → short accent pill → "Memomatic" title (large bold) → "Pinboard" subtitle → "Starting up…" hint text at bottom.
- Regenerate with `python3 app/gen_boot_splash.py` whenever the brand colours change.
