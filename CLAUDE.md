# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository

All issues, pull requests, and work should target **OrangeBannana/memomatic-pinboard** (this fork). Do not open PRs or issues against the upstream AtomicTrxn/memomatic-pinboard.

## Working with GitHub (read this before reaching for `gh`)

**Use the GitHub MCP server tools, not the `gh`/`hub` CLI or raw REST calls.** Every GitHub action this repo needs — reading/creating/updating issues, opening and merging PRs, commenting, checking CI, browsing files — is available as an MCP tool named `mcp__github__*` (e.g. `mcp__github__issue_write`, `mcp__github__create_pull_request`, `mcp__github__merge_pull_request`, `mcp__github__add_issue_comment`, `mcp__github__list_issues`). These are backed by the **Claude GitHub App**, which is already authenticated for this repo — no token juggling, no `gh auth login`, no `GITHUB_TOKEN` env var.

Past agents have wasted significant time fighting the `gh` CLI (missing auth, wrong scopes, interactive prompts that hang, 403s). Don't. The CLI is **not** the path here.

How to use the MCP tools:
- They may be **deferred** (not loaded at session start). If a `mcp__github__*` tool isn't directly callable, run `ToolSearch` with `select:mcp__github__issue_write,mcp__github__create_pull_request` (comma-separated exact names) or a keyword like `github pull request` to load the schemas, then call them normally.
- All of them take `owner: "OrangeBannana"` and `repo: "memomatic-pinboard"`.
- Typical flow for a change: branch locally → commit (author `Claude <noreply@anthropic.com>`) → `git push -u origin <branch>` → `mcp__github__create_pull_request` (base `main`) → `mcp__github__merge_pull_request` (`merge_method: "squash"`) → `git checkout main && git pull` → **delete the merged branch** (`git push origin --delete <branch>`; `git branch -D <branch>`).
- **Branch hygiene: every branch is deleted (remote and local) as soon as its PR merges.** Squash merges mean `git branch --merged` won't list them — verify the branch tip matches the merged PR's head SHA, then force-delete. Details in [docs/maintainer-standards.md](docs/maintainer-standards.md) → "Branch lifecycle (delete after merge)".
- `git` over HTTPS for push/pull/clone works fine and is authenticated; only the GitHub **API** actions (PRs, issues, comments) should go through the MCP tools rather than `gh`.

If the MCP GitHub server is genuinely unavailable in a given session (confirm with `ToolSearch` first), there is a working fallback: the Git Credential Manager credential used for HTTPS push also works as a bearer token for the GitHub REST API. Retrieve it with `printf "protocol=https\nhost=github.com\n\n" | git credential fill` and pass the `password` value as `Authorization: token …` to `https://api.github.com` (e.g. `POST /repos/OrangeBannana/memomatic-pinboard/pulls` to open a PR, `PUT /pulls/{n}/merge` to squash-merge, `POST /issues/{n}/comments` to comment). Never echo the token into the transcript or write it to a file.

## What this is

Memomatic Pinboard is a single-file FastAPI app that turns a Raspberry Pi Zero 2 W with a 3.5" GPIO TFT into a digital picture frame. The Pi runs the backend plus a fullscreen Chromium kiosk pointed at `/frame`. Phones/laptops on the same Wi-Fi hit `/admin` (owner) or `/guest/<token>` to upload images.

The entire backend is [app/app.py](app/app.py). The three UIs are static HTML served from [app/static/](app/static/) (`admin.html`, `frame.html`, `guest.html`) — there is no build step and no JS framework; each page is self-contained HTML/CSS/JS that calls the JSON API.

## Running and developing

There is no test suite, linter config, or package manager manifest beyond `requirements.txt`.

### Local dev (Linux / WSL2 / macOS) — recommended

```bash
pip install -r requirements.txt   # fastapi, uvicorn[standard], python-multipart, qrcode, Pillow

./run-local.sh   # starts on http://127.0.0.1:8080  (owner token: dev)
```

`run-local.sh` prepends `local/bin` to `PATH` so that the `sudo /usr/bin/nmcli` calls in `app.py` hit stub scripts that return canned Wi-Fi data instead of failing. Wi-Fi scan, connect, and save endpoints all work normally during local testing.

### Local dev (Windows — via WSL2)

```bat
run-local.bat
```

Double-click or run from a Command Prompt / PowerShell. Requires WSL2 with a Linux distro that has Python 3. Launches `run-local.sh` inside WSL automatically.

### Manual startup (without the helper script)

```bash
# Create required data dirs first (app.py mounts them at import time)
mkdir -p /tmp/pinboard/data /tmp/pinboard/images/originals /tmp/pinboard/images/display

PINBOARD_HOME=/tmp/pinboard PINBOARD_OWNER_TOKEN=dev \
  python3 -m uvicorn app:app --reload --port 8080 --app-dir app
```

Wi-Fi API endpoints (`/api/network/wifi`, `/api/network/connect`, `/api/network/save`) will fail without the stubs — add `local/bin` to the front of `PATH` to enable them.

### nmcli stubs

`local/bin/nmcli` and `local/bin/sudo` are lightweight shell scripts that intercept the `sudo /usr/bin/nmcli` calls made by `run_nmcli()` in `app.py`. They return canned responses:

| nmcli call | stub behaviour |
|---|---|
| `dev wifi list` | returns 4 fake networks (HomeNetwork, OfficeWifi, CafeGuest, NeighbourNet) |
| `dev wifi connect` | returns success message |
| `connection add` | returns success message |

These files must stay executable (`chmod +x local/bin/nmcli local/bin/sudo`).

### Validation

Before deploying, run: `python3 -m compileall app deploy.py`

Then open `http://127.0.0.1:8080/admin` and unlock with the token. The DB, image dirs, and default settings are created automatically on first request (`init_db` runs on FastAPI startup). On the Pi, install with `sudo PINBOARD_OWNER_TOKEN='...' ./install.sh`, which installs apt-packaged deps, lays out `/home/memomatic/pinboard`, and enables the `pinboard-app` and `pinboard-kiosk` systemd units.

Useful env vars (defaults in [app/app.py](app/app.py:18)): `PINBOARD_HOME` (data root), `PINBOARD_OWNER_TOKEN` (default `memes`), `PINBOARD_SLIDE_SECONDS`, `PINBOARD_MAX_UPLOAD_BYTES`.

## Architecture notes

**The slideshow is server-driven, not client-driven.** The frame is a dumb poller: `frame.html` calls `GET /api/slideshow/current` on an interval, and the server's `get_current_image()` ([app/app.py:203](app/app.py:203)) decides which image to show. All advance logic lives there and runs on each poll, in priority order: a queued `push_next_image_id` wins, else a stale/invalid `current_image_id` resets to the first image, else if `slide_seconds` have elapsed it advances via `choose_next` (wrap-around modulo over active images). State persists in the single-row `slideshow_state` table so it survives restarts. When changing slideshow behavior, edit `get_current_image`/`choose_next` — not the client.

**Image lifecycle.** Uploads go through `save_upload()`: the original bytes are written to `images/originals/<uuid><ext>`, then Pillow EXIF-transposes, thumbnails to `DISPLAY_MAX_SIZE` (960×960), converts to RGB, and writes a JPEG to `images/display/<uuid>.jpg`. Only the display JPEG is served (mounted at `/images`). Images have a `status` of `active` / `hidden` / `deleted` — **nothing is ever hard-deleted**; delete/hide are status updates, and only `status='active'` rows enter the slideshow. Deleting/hiding the current or push-next image clears slideshow state so it re-picks. Images also carry a `category` (`image` / `meme`) used by the slideshow-mode filter (see below); `PATCH /api/images/{id}` re-categorizes, and re-categorizing the current image clears slideshow state like hide/delete.

**Slideshow mode (memes vs photos).** The `slideshow_mode` setting (`all` / `images` / `memes`, default `all`) filters which categories the slideshow advances through. `get_current_image()` builds a `filtered` list by category and: (1) a queued `push_next_image_id` always wins, even off-category — a pushed/guest image is shown once regardless of mode; (2) if no image matches the mode it falls back to the last known image, else shows nothing; (3) an off-category current image (re-categorized or just-pushed) finishes its slide then moves on; (4) otherwise advances within the filtered list. Changing the mode (owner `PATCH /api/settings` or the localhost-dual-auth `POST /api/frame/mode`) clears slideshow state so the switch is immediate.

**Auth.** Every owner endpoint takes `x_pinboard_owner_token: str | None = Header(default=None)` and calls `require_owner()`, which does a `secrets.compare_digest` against `OWNER_TOKEN`. The admin page stores the token in browser localStorage and sends it as the `X-Pinboard-Owner-Token` header. Guest endpoints are gated instead by `guest_allowed()` (token match **and** the `guest_enabled` setting) plus `enforce_guest_rate_limit()` (per token+remote-addr, `GUEST_UPLOAD_LIMIT` per `GUEST_UPLOAD_WINDOW_SECONDS`). Guest uploads auto-queue themselves as push-next.

**Persistence.** SQLite at `$PINBOARD_HOME/data/pinboard.sqlite3` in WAL mode. Five tables: `images` (incl. a `category` column, migrated in via `ALTER TABLE` for pre-existing DBs), single-row `slideshow_state`, key/value `settings`, `guest_uploads` (rate-limit log), and `messages`. Settings (`slide_seconds`, `backdrop_blur_px`, `backdrop_brightness`, `guest_enabled`, `guest_token`, `message_display_seconds`, `clock_enabled`/`clock_corner`/`clock_size`, `slideshow_mode`, `color_schemes`, `active_color_scheme`) are all strings in the `settings` table; `PATCH /api/settings` clamps/validates before writing. Each request opens a fresh connection via the `db()` context manager — there is no shared connection or pool.

## Conventions

- Keep the backend in the single `app.py`; keep UIs as standalone static HTML (no bundler). If you add a static file, also add it to the `install -m 0644 ... static/...` lines in [install.sh](install.sh) so it deploys to the Pi.
- New images are stored as a fresh UUID; the user's original filename is kept only as `original_name` metadata, never used as a path.
- The `qrcode` and `Pillow` imports are deferred/optional in spots (`qr.svg` falls back to a text SVG if `qrcode` is missing) — preserve that graceful degradation for the Pi's apt-packaged environment.
- **All files in this repo use LF line endings (not CRLF).** The Pi is Linux; CRLF in shell scripts causes `\r: command not found`. If editing on Windows, ensure your editor/git is set to LF. Before any deploy, verify with `file install.sh app/kiosk.sh` — output should say "ASCII text executable", not "with CRLF line terminators". Fix with `sed -i 's/\r//' <file>`.

## WiFi / network features

`POST /api/network/connect`, `GET /api/network/wifi`, and `GET /api/network/ip` are the network endpoints. The first two use `nmcli` via `sudo` (sudoers must allow `memomatic` to run `/usr/bin/nmcli` without a password — this is set up by `install.sh`).

`GET /api/network/ip` requires no auth and returns `{"ip": "..."}` with the Pi's primary outbound IP (using a UDP trick against 8.8.8.8). Used by `frame.html` to display the IP in the menu.

The connect endpoint accepts **either** a valid owner token (for the admin page) **or** a request originating from localhost 127.0.0.1/::1 (for the on-device kiosk menu). This dual-auth is intentional — the frame page at `/frame` has no owner token but runs on the device itself.

## On-device touch system

The TFT uses an ADS7846 resistive touchscreen controller on SPI0.1 (CS=1, GPIO17=T_IRQ). The display driver (fbcp-ili9341, "safe" build) accesses the SPI hardware **directly via `/dev/mem`**, bypassing the kernel SPI subsystem. This makes the ADS7846 kernel driver incompatible: its `spi_sync()` calls time out waiting for hardware the fbcp DMA holds, corrupting SPI state and freezing the display.

**The solution** (`systemd/pinboard-touch.service`):
1. `ExecStartPre`: compile `app/spi_touch_read.c` → `app/spi_touch_read` binary
2. `ExecStartPre`: unbind `ads7846` driver (`/sys/bus/spi/drivers/ads7846/unbind`) so no kernel SPI transactions occur on touch
3. `ExecStartPre`: sleep 15 s to let Xorg/Chromium start first
4. `ExecStart`: run `app/touch_bridge.py`

**Touch detection** (`app/touch_bridge.py`): polls `/sys/class/gpio/gpio529/value` (BCM17, active-low T_IRQ) every 20 ms. On touch-down (1→0): calls `spi_touch_read` for coordinates, then `xdotool mousemove X Y mousedown 1`. On touch-up (0→1): `xdotool mouseup 1`. Sending real mousedown/mouseup (not a synthetic click) lets `frame.html` measure actual hold duration for long-press detection.

**Coordinate reading** (`app/spi_touch_read.c`): C program that maps `/dev/mem` SPI0 registers, busy-waits for fbcp's DMA to finish a frame (SPI TA: 1→0 transition), then reads 4 averaged ADS7846 samples in the ~2 ms inter-frame gap. Python cannot catch this window reliably due to GIL overhead; compiled C busy-wait can. Falls back to `"err"` if the read fails; `touch_bridge.py` falls back to position (240, 100) in that case.

**Calibration** (measured empirically via `app/raw_touch.py` 4-corner test on this device):
- `Calibration "1839 263 212 1857"` + `SwapAxes "1"` (in `/etc/X11/xorg.conf.d/99-calibration.conf`)
- Physical Y channel (ADS7846 cmd 0x90) → screen X: `(raw - 212) / (1857 - 212) * 480`
- Physical X channel (ADS7846 cmd 0xD0) → screen Y: `(raw - 1839) / (263 - 1839) * 320`
- X11 display is 480×320 (confirmed: xdotool screen centre = 240,160)
- Corner ADC values: TL ry=220 rx=1872 / TR ry=1841 rx=1806 / BR ry=1872 rx=260 / BL ry=203 rx=266
- If touch position is consistently off, re-run `sudo python3 app/raw_touch.py`, touch 4 corners, and update the four CAL_* constants in `spi_touch_read.c` and redeploy

**Frame menu interaction model** (`frame.html`):
- **Short tap anywhere** → show menu (if hidden) | immediately close menu (if visible)
- **Hold ≥ 2 s anywhere** → open WiFi panel directly (no need to tap a button)
- Menu note shows current device IP (fetched from `GET /api/network/ip` on each open)
- WiFi panel auto-hides after 60 s (vs 3.5 s for the base menu) to allow time for keyboard entry
- `user-select: none` on body prevents text-selection artefacts from long-press hold

Implementation notes:
- `pointerdown` records the gesture start and shows the menu immediately if hidden
- `pointerup` acts: short tap outside `.menu-panel` → `hideMenu()`; long press → `openNetworkPanel()`
- A `click` listener is a fallback for drivers that emit click without pointer events
- Do **not** add a separate `touchstart` capture listener — it causes duplicate events

## Deploying to the Pi

Pi credentials: user `memomatic`, password `memes`. **The IP changes between sessions** — verify with `hostname -I` on the Pi or check your router. Since `install.sh` sets the hostname to `memomatic` and runs `avahi-daemon`, you can usually set `PI_HOST = "memomatic.local"` in `deploy.py` and skip chasing the IP entirely; fall back to the raw IP if mDNS isn't resolving on your network.

**`deploy.py`** (in repo root) is a self-contained Python deployment script. It:
1. Verifies SHA256 checksums of all source files locally before uploading
2. Uploads via SFTP (paramiko)
3. Verifies checksums again on the Pi after upload to catch transfer corruption
4. Copies systemd units into place, reloads systemd, restarts all three services

Run it with `py deploy.py` (requires `pip install paramiko` first) or double-click `deploy.bat` which does both steps. The `spi_touch_read` binary is **not** deployed directly — it is compiled on the Pi by `ExecStartPre` in the touch service each time it starts.

**File corruption note:** previous deployments saw files corrupted during upload. The checksum verification in `deploy.py` catches this. If a checksum fails post-upload, re-run; do not proceed with a corrupt file on the Pi.

The `install.sh` script does a full install (apt packages including `build-essential` for gcc + file copy + systemd setup). Use it for first-time setup. For subsequent updates, `deploy.py` is faster.

## Known working state

Bugs found and fixed across development sessions:

1. **Duplicate `POST /api/network/connect` route** — merged into one handler with dual-auth (owner token OR localhost).
2. **ADS7846 driver SPI timeout** — root cause: fbcp holds SPI0 hardware via `/dev/mem`; kernel driver's `spi_sync()` times out waiting, corrupting SPI state → display freeze. Fixed by unbinding driver and using GPIO polling + C helper for coordinates.
3. **Python SPI reads corrupting display** — Python's GIL/interpreter overhead (~100 µs per operation) is too slow to catch the ~2 ms inter-frame gap. Accessing SPI registers mid-frame deasserts CS0, corrupting fbcp's DMA → "scanning" artefacts and white screen. Fixed by moving coordinate reads to a compiled C busy-wait helper.
4. **Touch events firing 3×** — `pointerdown`, `touchstart`, and `click` all captured, each triggering the handler. Fixed by using only `pointerdown` + `pointerup` + `click` (fallback) with a dedup timer.
5. **Menu auto-hide during WiFi connect** — the 3.5 s timer dismissed the menu before nmcli responded. Fixed with the `connecting` flag.
6. **Stale poll interval** — `setInterval` captured the initial `pollMs` and never updated. Fixed by storing the handle and recreating it when `pollMs` changes.
7. **Systemd ordering cycle** — `After=pinboard-kiosk.service` in touch service created a dependency cycle. Fixed by removing the kiosk dependency.
8. **spi_touch_read always returned "err" (all-zero ADC reads)** — root cause: when `pinboard-touch.service` unbinds the `ads7846` kernel driver, GPIO7 (SPI0_CE1_N / CS1) reverts from ALT0 to input mode. The SPI peripheral believes it is asserting CS1, but the physical pin stays HIGH → ADS7846 is never selected → MISO reads back 0x00. Fixed by mapping the GPIO peripheral in `spi_touch_read.c` and calling `gpio7_alt0()` to restore GPIO7 to ALT0 before every read. Also: all `fprintf`/stderr calls in `spi_touch_read.c` must come AFTER `wrs(SPI_CS, 0)` — a `write(2)` syscall in the inter-frame hot path introduces enough latency that fbcp starts the next frame before the ADS7846 read completes.

## Feature history (chronological)

Larger features delivered across sessions, newest last. Each shipped as its own PR to `main`.

- **Memes vs photos slideshow mode (#31)** — images carry a `category` (`image`/`meme`); the `slideshow_mode` setting (`all`/`images`/`memes`) filters the slideshow. Admin upload form + guest upload form have a category selector, admin image cards show a category pill and a re-categorize button, the Display Settings panel has a Display-mode control, and the frame touch menu has a **Mode** row that cycles All→Photos→Memes. Frame changes go through `POST /api/frame/mode` (localhost-or-owner dual-auth). Edge cases (push-next override, empty category, re-categorized current) handled in `get_current_image` — see "Slideshow mode" above.
- **Persistent clock overlay (#29)** — optional clock on the frame, settings `clock_enabled`/`clock_corner` (4 corners)/`clock_size` (3 sizes), off by default. `position: fixed` overlay above the image, below the menu; configurable from the admin Display Settings panel and a Clock sub-panel in the frame touch menu (`POST /api/frame/clock`, localhost-or-owner dual-auth). `GET /api/slideshow/current` returns a `clock` object so the tokenless frame can read it.
- **Unified dark-glassmorphism UI (#12)** — `admin.html`, `guest.html`, and `frame.html` share one design language (translucent panels, backdrop blur, warm off-white text, indigo accent). All colours are CSS custom properties on `:root`. Touch targets ≥ 48 px. Reference doc: [docs/design-guidelines.md](docs/design-guidelines.md).
- **User-configurable colour schemes (#17)** — 3 built-in schemes (`BUILTIN_SCHEMES` in `app.py`, never stored in DB) + custom schemes stored as JSON in the `settings` table. Public `GET /api/color-schemes` plus owner `POST`/`DELETE`/`activate` endpoints. Every page runs an IIFE on load that fetches the active scheme and overrides the `:root` tokens via `style.setProperty`. Admin "Appearance" panel derives the full 12-token set from 4 hex inputs via `deriveTokens()`.
- **mDNS hostname (#14)** — `install.sh` installs `avahi-daemon` + `libnss-mdns` and sets the hostname to `memomatic`, so the device is reachable at `http://memomatic.local:8080`. `GET /api/network/ip` also returns `hostname`/`mdns`. Frame menu and admin page both show the mDNS address with the raw IP as fallback.
- **Custom boot splash (#5)** — `app/boot_splash.png` (480×320, dark theme) written to `/dev/fb0` by `app/show_splash.py` via the `pinboard-splash.service` oneshot (`After=fbcp-ili9341`, `Before=pinboard-kiosk`). Regenerate the PNG with `python3 app/gen_boot_splash.py`. Handles 16-bit RGB565 and 32-bit framebuffers; non-fatal if Pillow/fb unavailable.
- **Boot-time reduction (#4)** — `pinboard-app.service`: `network-online.target` → `network.target`. `pinboard-kiosk.service`: dropped `multi-user.target` dependency, `sleep 5` → `sleep 2`. `install.sh`: writes `disable_splash=1`, `dtoverlay=disable-bt`, `gpu_mem=16` to `/boot/firmware/config.txt` and masks `bluetooth.service`/`hciuart.service`. **See the touch-regression caveat below — some of these may have side effects on the touch/display timing.**

Smaller fixes:

9. **Unreadable dropdown options (#22)** — admin `<select>` used a translucent background that composited to near-white on the native option popup, hiding the light option text. Fixed with a solid dark `background-color` on both `select` and `select option`.
10. **Frame opened menu + WiFi panel on boot (#23)** — a phantom boot-time `pointerdown` (no matching `pointerup`) showed the menu, then the 2 s long-press timer fired and opened the WiFi panel. Fixed with a 4 s startup grace period (`STARTUP_GRACE_MS` in `frame.html`) that ignores all pointer/click gestures right after load. **This is a client-side guard only; it does not fix the underlying touch behaviour (see #25). Verify on real hardware once touch works again.**

## Open investigations

- **Touchscreen regression (#25, OPEN — not yet fixed).** On-device touch reverted to an earlier broken state. **Prime suspect: the boot-time changes in #4.** Two concrete leads for the next agent:
  1. **`gpu_mem=16`** — the fbcp-ili9341 "safe build" drives the TFT via GPU DMA. Starving the GPU to 16 MB may change fbcp's inter-frame DMA timing, which `spi_touch_read.c` busy-waits on to read the ADS7846 in the ~2 ms gap. If that window shifts, reads corrupt or return `err`. Try reverting to `gpu_mem=64` first.
  2. **`dtoverlay=disable-bt`** — frees the PL011 UART and remaps it; confirm it doesn't disturb the SPI0/GPIO7/GPIO17 pins the touch system depends on.
  Also re-verify: `pinboard-touch.service` is active, the `ads7846` unbind succeeded, and `spi_touch_read` recompiled (it's built on the Pi by `ExecStartPre`, so a toolchain/source mismatch would surface here). Re-run `sudo python3 app/raw_touch.py` 4-corner test if coordinates are merely off rather than absent.
