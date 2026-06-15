# Touchscreen regression (#25) — fix attempt + hardware test plan

**Branch:** `issue-25-touch-regression-fix`
**Status:** implemented without hardware access — **needs on-device verification before merging.**

## Root cause — CONFIRMED on-device 2026-06-11

The original suspects are **disproven on this device**: baseline `touch_diag.sh` output shows
`/boot/firmware/config.txt` contains **none** of #4's lines (no `gpu_mem`, no `dtoverlay=disable-bt`,
no `disable_splash`) and `vcgencmd` reports `gpu=64M`. `install.sh` was never re-run here, so #4's
boot-config edits never reached the hardware.

The journal showed the real picture: touch was **intermittent**, not dead — `touch-down at (109, 205)`
/ `(367, 78)` succeeded while most touches logged `touch-down (fallback 240,100)`.

**Measured mechanism:**

| | |
|---|---|
| `spi_touch_read` wall time, fbcp idle (static screen) | **0.33 – 0.69 s** (5 runs, on-device) |
| `touch_bridge.py` subprocess timeout | **0.5 s** |

The helper's TA-detection spin was 5,000,000 uncached MMIO reads (~64 ns each, slower under Chromium
load). fbcp only drives SPI when screen content changes, so on a static slideshow the spin always ran
to completion — racing the 0.5 s timeout. Killed → fallback (240,100); fbcp active at call time → fast
TA path → success. The fallback coordinate intentionally toggles the menu, which made earlier
validation look like "touch works", and the richer menu turned fallback taps into wrong-button presses
(the spontaneous `slideshow_mode` flip to `memes` on 2026-06-11 was fallback taps hitting the mode button).

**Fix in this branch:**
- `spi_touch_read.c`: TA-detection window 5,000,000 → 250,000 iterations (~16–50 ms, longer than one
  60 fps frame period, so an actively-transmitting fbcp is still always caught; an idle bus is safe to
  read immediately).
- `touch_bridge.py`: one retry on failure (covers a frame starting mid-read), failure reason logged at
  info level so every fallback in the journal is explained.

Remote verification after deploy: helper wall time drops to < 0.1 s untouched (`err`, clean rail
values). The finger-on-screen test still needs a human.

## What this branch changes

| Change | File | Why |
|---|---|---|
| `gpu_mem=16` → `gpu_mem=64` (and remediates an existing `gpu_mem=16` line) | `install.sh` | **Prime suspect.** The fbcp-ili9341 "safe build" mirrors the framebuffer through the GPU; starving the GPU to 16 MB can change the SPI frame cadence that `spi_touch_read.c` busy-waits on to catch the ~2 ms inter-frame gap. Shifted timing ⇒ corrupted reads / `err`. |
| Compile step no longer swallows errors | `systemd/pinboard-touch.service` | Previously `gcc … 2>/dev/null; true` hid every failure. A missing gcc or corrupt source produced no binary and **every touch fell back to the fixed (240, 100) coordinate** — which matches "reverted to an earlier broken state". The journal now logs `compiled OK` / explicit `ERROR:` lines. |
| Post-unbind state logged | `systemd/pinboard-touch.service` | Journal now states whether `ads7846` actually released `spi0.1`. A still-bound kernel driver corrupts fbcp's SPI state (bug #2 in CLAUDE.md). |
| New `app/touch_diag.sh` | `install.sh`, `deploy.py` | One-shot on-device report covering every known failure point, so future no-hardware sessions can triage from pasted output. |

`dtoverlay=disable-bt` (suspect 2, lower probability) is **intentionally left in place** on this branch so only one boot-config variable changes at a time. Note: the Bluetooth feature branch for issue #1 (`issue-1-bluetooth-guest-uploads`) removes `disable-bt` entirely — if this branch alone doesn't fix touch, testing the #1 branch doubles as the disable-bt experiment.

## On-device test plan (human, ~15 min)

1. **Capture the broken baseline first** (so we learn which suspect it was):
   ```bash
   sudo sh /home/memomatic/pinboard/app/touch_diag.sh > /tmp/touch-before.txt 2>&1
   ```
   Hold a finger on the screen when section 5 prompts. Save/paste this output to issue #25.

2. **Check the cheap explanation before rebooting** — in the baseline output:
   - Section 2: is `spi_touch_read` **missing**, or older than the `.c` file, or is gcc missing? If yes, the regression is the silent-compile bug, not `gpu_mem`. The new service file fixes the visibility; `apt install build-essential` fixes a missing gcc.
   - Section 4: does `vcgencmd get_mem gpu` say `gpu=16M`? Then the `gpu_mem` theory is live.

3. **Deploy this branch:** from a checkout of this branch run `py deploy.py` (or on the Pi: `git pull && sudo ./install.sh`). Note: `deploy.py` does not edit `/boot/firmware/config.txt`; either re-run `sudo ./install.sh` (now remediates `gpu_mem=16` → `64`) or edit the line manually.

4. **Reboot** (boot-config changes need it): `sudo reboot`.

5. **Verify the chain:**
   ```bash
   sudo sh /home/memomatic/pinboard/app/touch_diag.sh > /tmp/touch-after.txt 2>&1
   ```
   Expected good output:
   - Section 1: all four services active; `touch_bridge.py` running.
   - Section 2: binary present and newer than source; test compile OK.
   - Section 3: `spi0.1 driver: none`; `gpio529 exported`; `raspi-gpio get 7` shows `func=ALT0` *while a read is in flight* (it may legitimately show INPUT when idle — `spi_touch_read` re-applies ALT0 per read).
   - Section 4: `gpu=64M`.
   - Section 5: with a finger held on the screen, prints two integers within 0–479 / 0–319 and `ta_seen=1`; untouched prints `err` (rail values filtered) — that's correct.
   - Journal: `spi_touch_read compiled OK` and `ads7846 unbind OK`.

6. **Functional test on the frame:** short tap toggles the menu; ≥ 2 s hold opens the Wi-Fi panel; taps land where the finger is (not always at the same spot — "always the same spot" = fallback (240,100) = helper failing). Re-verify the #23 startup-grace behaviour: no menu/WiFi panel should self-open in the first seconds after boot.

## Decision tree after testing

- **Touch works** → merge; close #25 referencing which suspect the before/after diag output confirmed; keep the remaining #4 boot optimisations.
- **Coordinates land but are offset** → calibration drift, not this bug: re-run `sudo python3 app/raw_touch.py`, touch 4 corners, update the `CAL_*` constants in `spi_touch_read.c`, redeploy.
- **Still `err` / fallback coords with `gpu_mem=64`** → next experiment is `dtoverlay=disable-bt`: remove the line (or deploy the issue #1 branch, which removes it and re-enables Bluetooth), reboot, re-run diag. Check `raspi-gpio get 7 17` for unexpected pin functions.
- **`ta_seen=0` in section 5** → fbcp isn't producing SPI frames at all; touch is a casualty, not the disease — check `fbcp-ili9341.service` and the display itself.
- **Binary missing / compile errors in journal** → toolchain or source-transfer problem; `deploy.py`'s checksum step should rule out corruption; `which gcc` / `apt install build-essential` for the toolchain.

## Problems future sessions should expect

- `deploy.py` **does not touch boot config**; any fix involving `/boot/firmware/config.txt` needs `install.sh` or manual editing, plus a reboot.
- A user may have set their own `gpu_mem` value; `install.sh` only rewrites the exact line `gpu_mem=16` and otherwise leaves existing settings alone.
- `systemctl mask bluetooth.service hciuart.service` from #4 is still in effect on provisioned devices. Unrelated to touch (masking only stops services; the overlay is what changes pins) but worth remembering when issue #1 lands: that branch must unmask them.
- If both this and the #1 branch get merged, `install.sh` will contain both the `gpu_mem=64` remediation and the Bluetooth re-enable — re-run it once on the device rather than cherry-picking lines.
