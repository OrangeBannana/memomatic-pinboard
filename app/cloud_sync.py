#!/usr/bin/env python3
"""
Memomatic cloud sync agent (remote access feature, issue #87, stage 4).

Runs as its OWN service (pinboard-cloudsync.service), deliberately NOT as a
FastAPI lifespan/startup task: the Pi installs FastAPI from apt (0.92 on
Bookworm), which silently ignores the lifespan= kwarg, so a background task
registered there would never start (the same trap as init_db, issue #65).

Outbound-only loop against the Cloudflare relay (the Pi never accepts inbound
connections; the home network stays closed):

  1. PULL   new guest submissions (images/messages) + queued settings changes.
  2. APPLY  images via save_upload() with guest semantics (queued push-next, or
            pending review when guest_review_required is on); messages into the
            messages table; settings via the local owner API so the Pi's own
            validation + side effects (e.g. mode change clears slideshow state)
            run unchanged.
  3. PUSH   the current basic settings up so the owner page shows live values.
  4. ACK    processed ids so the relay deletes them (+ their R2 objects).

LAN access (/admin, /guest, /frame) is untouched — this only adds a remote path.

Config (environment, set by the systemd unit / EnvironmentFile):
  PINBOARD_CLOUD_URL      e.g. https://memomatic-relay.<you>.workers.dev
  PINBOARD_CLOUD_SECRET   shared device secret (matches the Worker DEVICE_SECRET)
  PINBOARD_OWNER_TOKEN    owner token (same as pinboard-app) for the local PATCH
  PINBOARD_LOCAL_API      local app base URL (default http://127.0.0.1:8080)
  PINBOARD_CLOUD_POLL_SECONDS   poll interval (default 10)

If PINBOARD_CLOUD_URL/SECRET are unset the agent idles harmlessly, so the
service can be installed before remote access is configured.
"""
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# Import the main app to reuse its image pipeline + DB helpers. Importing runs
# ensure_dirs()/init_db() at module scope, which are idempotent.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app  # noqa: E402
from fastapi import HTTPException  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s cloud_sync: %(message)s")
log = logging.getLogger(__name__)

CLOUD_URL = os.environ.get("PINBOARD_CLOUD_URL", "").rstrip("/")
CLOUD_SECRET = os.environ.get("PINBOARD_CLOUD_SECRET", "")
LOCAL_API = os.environ.get("PINBOARD_LOCAL_API", "http://127.0.0.1:8080").rstrip("/")
OWNER_TOKEN = os.environ.get("PINBOARD_OWNER_TOKEN", getattr(app, "OWNER_TOKEN", ""))
POLL_SECONDS = float(os.environ.get("PINBOARD_CLOUD_POLL_SECONDS", "10"))
HTTP_TIMEOUT = 30

# Settings key types must match the Pi's PATCH /api/settings handler: the
# toggles use bool(body[...]) (a string "0" would be truthy!), the durations
# are ints. Everything else passes through as a string.
BOOL_KEYS = {"guest_enabled", "guest_review_required", "clock_enabled"}
INT_KEYS = {"slide_seconds", "message_display_seconds"}


class _Upload:
    """Minimal stand-in for FastAPI's UploadFile: save_upload() only reads
    .filename (for the extension) and .content_type."""

    def __init__(self, filename, content_type=None):
        self.filename = filename
        self.content_type = content_type


def configured():
    return bool(CLOUD_URL and CLOUD_SECRET)


def _open(req):
    return urllib.request.urlopen(req, timeout=HTTP_TIMEOUT)


def cloud_get(path):
    req = urllib.request.Request(
        CLOUD_URL + path, method="GET",
        headers={"Authorization": "Bearer " + CLOUD_SECRET},
    )
    with _open(req) as r:
        return json.loads(r.read().decode())


def cloud_post(path, payload):
    req = urllib.request.Request(
        CLOUD_URL + path, data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": "Bearer " + CLOUD_SECRET, "content-type": "application/json"},
    )
    with _open(req) as r:
        return json.loads(r.read().decode())


def cloud_object(key):
    req = urllib.request.Request(
        CLOUD_URL + "/api/sync/object?key=" + urllib.parse.quote(key),
        method="GET", headers={"Authorization": "Bearer " + CLOUD_SECRET},
    )
    with _open(req) as r:
        return r.read()


def local_patch_settings(patch):
    req = urllib.request.Request(
        LOCAL_API + "/api/settings", data=json.dumps(patch).encode(), method="PATCH",
        headers={"content-type": "application/json", "X-Pinboard-Owner-Token": OWNER_TOKEN},
    )
    with _open(req) as r:
        r.read()


def process_submission(sub):
    """Apply one pulled submission. Raises HTTPException for bad data (caller
    drops it) and other exceptions for transient failures (caller retries)."""
    if sub.get("kind") == "message":
        content = (sub.get("content") or "").strip()
        if content:
            with app.db() as conn:
                conn.execute(
                    "INSERT INTO messages (content, created_at) VALUES (?, ?)",
                    (content[:200], time.time()),
                )
        return

    key = sub.get("object_key")
    if not key:
        raise HTTPException(status_code=400, detail="image submission missing object_key")
    content = cloud_object(key)
    name = key.rsplit("/", 1)[-1]  # "<uuid>.jpg" -> save_upload reads the suffix
    category = sub.get("category") or "image"
    with app.db() as conn:
        review = app.get_setting(conn, "guest_review_required", "0") == "1"
    status = "pending" if review else "active"
    image = app.save_upload(_Upload(name), content, "cloud-guest", category, status)
    if not review:
        # Mirror guest_upload: a remote upload flashes next regardless of mode.
        with app.db() as conn:
            conn.execute(
                "UPDATE slideshow_state SET push_next_image_id = ? WHERE id = 1", (image["id"],)
            )


def apply_settings(cmds):
    """Apply all queued settings in one local PATCH so the Pi's own validation
    and side effects run. Returns the list of command ids applied (empty on
    failure, so they retry next loop)."""
    if not cmds:
        return []
    patch = {}
    for c in cmds:
        key, val = c["key"], c["value"]
        if key in BOOL_KEYS:
            patch[key] = (val == "1")
        elif key in INT_KEYS:
            try:
                patch[key] = int(val)
            except (TypeError, ValueError):
                patch[key] = val
        else:
            patch[key] = val
    try:
        local_patch_settings(patch)
        return [c["id"] for c in cmds]
    except Exception as exc:  # noqa: BLE001 - transient; retry next loop
        log.warning("settings apply failed (will retry): %s", exc)
        return []


def push_state():
    keys = {
        "slideshow_mode": "all",
        "slideshow_order": "sequential",
        "slide_seconds": str(getattr(app, "DEFAULT_SLIDE_SECONDS", 15)),
        "message_display_seconds": "8",
        "guest_enabled": "0",
        "guest_review_required": "0",
        "clock_enabled": "0",
    }
    with app.db() as conn:
        settings = {k: app.get_setting(conn, k, default) for k, default in keys.items()}
    cloud_post("/api/sync/state", {"settings": settings})


def sync_once():
    data = cloud_get("/api/sync/pull")
    subs = data.get("submissions", [])
    cmds = data.get("settings", [])

    ack_subs = []
    for sub in subs:
        try:
            process_submission(sub)
            ack_subs.append(sub["id"])
            log.info("processed %s submission %s", sub.get("kind"), sub["id"])
        except HTTPException as exc:
            # Bad data — drop it (ack) so it doesn't redeliver forever.
            log.warning("dropping submission %s: %s", sub.get("id"), exc.detail)
            ack_subs.append(sub["id"])
        except Exception as exc:  # noqa: BLE001 - transient; leave for retry
            log.warning("submission %s failed (will retry): %s", sub.get("id"), exc)

    applied_ids = apply_settings(cmds)

    if ack_subs or applied_ids:
        cloud_post("/api/sync/ack", {"submission_ids": ack_subs, "settings_ids": applied_ids})

    # Always refresh the owner UI's view of current settings.
    push_state()


def main():
    if not configured():
        log.info("PINBOARD_CLOUD_URL/SECRET not set — cloud sync idle")
    log.info("starting (poll every %.0fs, cloud=%s)", POLL_SECONDS, CLOUD_URL or "<unset>")
    while True:
        if configured():
            try:
                sync_once()
            except urllib.error.URLError as exc:
                log.warning("cloud unreachable: %s", exc)
            except Exception:  # noqa: BLE001 - never let the loop die
                log.exception("sync iteration failed")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
