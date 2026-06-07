# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Memomatic Pinboard is a single-file FastAPI app that turns a Raspberry Pi Zero 2 W with a 3.5" GPIO TFT into a digital picture frame. The Pi runs the backend plus a fullscreen Chromium kiosk pointed at `/frame`. Phones/laptops on the same Wi-Fi hit `/admin` (owner) or `/guest/<token>` to upload images.

The entire backend is [app/app.py](app/app.py). The three UIs are static HTML served from [app/static/](app/static/) (`admin.html`, `frame.html`, `guest.html`) — there is no build step and no JS framework; each page is self-contained HTML/CSS/JS that calls the JSON API.

## Running and developing

There is no test suite, linter config, or package manager manifest beyond `requirements.txt`. To run locally:

```bash
pip install -r requirements.txt   # fastapi, uvicorn[standard], python-multipart, qrcode (Pillow also required)
PINBOARD_HOME=/tmp/pinboard PINBOARD_OWNER_TOKEN=dev \
  python3 -m uvicorn app:app --reload --port 8080 --app-dir app
```

Then open `http://127.0.0.1:8080/admin` and unlock with the token. The DB, image dirs, and default settings are created automatically on first request (`init_db` runs on FastAPI startup). On the Pi, install with `sudo PINBOARD_OWNER_TOKEN='...' ./install.sh`, which installs apt-packaged deps, lays out `/home/memomatic/pinboard`, and enables the `pinboard-app` and `pinboard-kiosk` systemd units.

Useful env vars (defaults in [app/app.py](app/app.py:18)): `PINBOARD_HOME` (data root), `PINBOARD_OWNER_TOKEN` (default `memes`), `PINBOARD_SLIDE_SECONDS`, `PINBOARD_MAX_UPLOAD_BYTES`.

## Architecture notes

**The slideshow is server-driven, not client-driven.** The frame is a dumb poller: `frame.html` calls `GET /api/slideshow/current` on an interval, and the server's `get_current_image()` ([app/app.py:203](app/app.py:203)) decides which image to show. All advance logic lives there and runs on each poll, in priority order: a queued `push_next_image_id` wins, else a stale/invalid `current_image_id` resets to the first image, else if `slide_seconds` have elapsed it advances via `choose_next` (wrap-around modulo over active images). State persists in the single-row `slideshow_state` table so it survives restarts. When changing slideshow behavior, edit `get_current_image`/`choose_next` — not the client.

**Image lifecycle.** Uploads go through `save_upload()`: the original bytes are written to `images/originals/<uuid><ext>`, then Pillow EXIF-transposes, thumbnails to `DISPLAY_MAX_SIZE` (960×960), converts to RGB, and writes a JPEG to `images/display/<uuid>.jpg`. Only the display JPEG is served (mounted at `/images`). Images have a `status` of `active` / `hidden` / `deleted` — **nothing is ever hard-deleted**; delete/hide are status updates, and only `status='active'` rows enter the slideshow. Deleting/hiding the current or push-next image clears slideshow state so it re-picks.

**Auth.** Every owner endpoint takes `x_pinboard_owner_token: str | None = Header(default=None)` and calls `require_owner()`, which does a `secrets.compare_digest` against `OWNER_TOKEN`. The admin page stores the token in browser localStorage and sends it as the `X-Pinboard-Owner-Token` header. Guest endpoints are gated instead by `guest_allowed()` (token match **and** the `guest_enabled` setting) plus `enforce_guest_rate_limit()` (per token+remote-addr, `GUEST_UPLOAD_LIMIT` per `GUEST_UPLOAD_WINDOW_SECONDS`). Guest uploads auto-queue themselves as push-next.

**Persistence.** SQLite at `$PINBOARD_HOME/data/pinboard.sqlite3` in WAL mode. Four tables: `images`, single-row `slideshow_state`, key/value `settings`, and `guest_uploads` (rate-limit log). Settings (`slide_seconds`, `backdrop_blur_px`, `backdrop_brightness`, `guest_enabled`, `guest_token`) are all strings in the `settings` table; `PATCH /api/settings` clamps numeric ranges before writing. Each request opens a fresh connection via the `db()` context manager — there is no shared connection or pool.

## Conventions

- Keep the backend in the single `app.py`; keep UIs as standalone static HTML (no bundler). If you add a static file, also add it to the `install -m 0644 ... static/...` lines in [install.sh](install.sh) so it deploys to the Pi.
- New images are stored as a fresh UUID; the user's original filename is kept only as `original_name` metadata, never used as a path.
- The `qrcode` and `Pillow` imports are deferred/optional in spots (`qr.svg` falls back to a text SVG if `qrcode` is missing) — preserve that graceful degradation for the Pi's apt-packaged environment.
