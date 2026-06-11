from __future__ import annotations

import json
import os
import socket
import subprocess
import secrets
import sqlite3
import time
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from PIL import Image, ImageOps, UnidentifiedImageError


BASE_DIR = Path(os.environ.get("PINBOARD_HOME", "/home/memomatic/pinboard"))
DATA_DIR = BASE_DIR / "data"
ORIGINALS_DIR = BASE_DIR / "images" / "originals"
DISPLAY_DIR = BASE_DIR / "images" / "display"
STATIC_DIR = Path(__file__).parent / "static"
DB_PATH = DATA_DIR / "pinboard.sqlite3"
DEFAULT_SLIDE_SECONDS = int(os.environ.get("PINBOARD_SLIDE_SECONDS", "15"))
OWNER_TOKEN = os.environ.get("PINBOARD_OWNER_TOKEN", "memes")
MAX_UPLOAD_BYTES = int(os.environ.get("PINBOARD_MAX_UPLOAD_BYTES", str(15 * 1024 * 1024)))
DISPLAY_MAX_SIZE = (960, 960)
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
GUEST_UPLOAD_LIMIT = 5
GUEST_UPLOAD_WINDOW_SECONDS = 10 * 60
CLOCK_CORNERS = {"top-left", "top-right", "bottom-left", "bottom-right"}
CLOCK_SIZES = {"small", "medium", "large"}
IMAGE_CATEGORIES = {"image", "meme"}
SLIDESHOW_MODES = {"all", "images", "memes"}
# Maps a non-"all" slideshow mode to the image category it shows.
MODE_TO_CATEGORY = {"images": "image", "memes": "meme"}

BUILTIN_SCHEMES: list[dict] = [
    {
        "name": "Dark Violet",
        "builtin": True,
        "tokens": {
            "--accent":      "#7c6af5",
            "--accent-dim":  "rgba(124, 106, 245, 0.16)",
            "--bg":          "#0b0b0e",
            "--glass-bg":    "rgba(20, 20, 28, 0.82)",
            "--glass-border":"rgba(255, 255, 255, 0.10)",
            "--text":        "#ede9e1",
            "--text-muted":  "rgba(237, 233, 225, 0.58)",
            "--text-subtle": "rgba(237, 233, 225, 0.30)",
            "--line":        "rgba(255, 255, 255, 0.08)",
            "--danger":      "#e85454",
            "--danger-dim":  "rgba(232, 84, 84, 0.14)",
            "--success":     "#52c47a",
        },
    },
    {
        "name": "Dark Amber",
        "builtin": True,
        "tokens": {
            "--accent":      "#f0a030",
            "--accent-dim":  "rgba(240, 160, 48, 0.16)",
            "--bg":          "#0d0b08",
            "--glass-bg":    "rgba(26, 22, 16, 0.82)",
            "--glass-border":"rgba(255, 255, 255, 0.10)",
            "--text":        "#f0ebe0",
            "--text-muted":  "rgba(240, 235, 224, 0.58)",
            "--text-subtle": "rgba(240, 235, 224, 0.30)",
            "--line":        "rgba(255, 255, 255, 0.08)",
            "--danger":      "#e85454",
            "--danger-dim":  "rgba(232, 84, 84, 0.14)",
            "--success":     "#52c47a",
        },
    },
    {
        "name": "Dark Teal",
        "builtin": True,
        "tokens": {
            "--accent":      "#2dd4bf",
            "--accent-dim":  "rgba(45, 212, 191, 0.16)",
            "--bg":          "#080d0d",
            "--glass-bg":    "rgba(12, 26, 24, 0.82)",
            "--glass-border":"rgba(255, 255, 255, 0.10)",
            "--text":        "#e8f0ef",
            "--text-muted":  "rgba(232, 240, 239, 0.58)",
            "--text-subtle": "rgba(232, 240, 239, 0.30)",
            "--line":        "rgba(255, 255, 255, 0.08)",
            "--danger":      "#e85454",
            "--danger-dim":  "rgba(232, 84, 84, 0.14)",
            "--success":     "#52c47a",
        },
    },
]
BUILTIN_SCHEME_NAMES: set[str] = {s["name"] for s in BUILTIN_SCHEMES}


app = FastAPI(title="Memomatic Pinboard")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/images", StaticFiles(directory=DISPLAY_DIR), name="images")


def now() -> float:
    return time.time()


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
    DISPLAY_DIR.mkdir(parents=True, exist_ok=True)


def db() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_name TEXT NOT NULL,
                stored_name TEXT NOT NULL,
                display_name TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'wifi-owner',
                status TEXT NOT NULL DEFAULT 'active',
                category TEXT NOT NULL DEFAULT 'image',
                created_at REAL NOT NULL
            )
            """
        )
        # Migration: add category to pre-existing DBs that lack it.
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(images)")}
        if "category" not in columns:
            conn.execute("ALTER TABLE images ADD COLUMN category TEXT NOT NULL DEFAULT 'image'")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS slideshow_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                current_image_id INTEGER,
                last_changed_at REAL NOT NULL DEFAULT 0,
                push_next_image_id INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS guest_uploads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT NOT NULL,
                remote_addr TEXT NOT NULL,
                uploaded_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                created_at REAL NOT NULL,
                shown_at REAL
            )
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO slideshow_state
                (id, current_image_id, last_changed_at, push_next_image_id)
            VALUES (1, NULL, 0, NULL)
            """
        )
        defaults = {
            "slide_seconds": str(DEFAULT_SLIDE_SECONDS),
            "backdrop_blur_px": "8",
            "backdrop_brightness": "0.68",
            "guest_enabled": "0",
            "guest_token": secrets.token_urlsafe(16),
            "message_display_seconds": "8",
            "color_schemes": "[]",
            "active_color_scheme": "Dark Violet",
            "clock_enabled": "0",
            "clock_corner": "bottom-right",
            "clock_size": "medium",
            "slideshow_mode": "all",
        }
        for key, value in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )


@app.on_event("startup")
def startup() -> None:
    init_db()


def row_to_image(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "original_name": row["original_name"],
        "source": row["source"],
        "status": row["status"],
        "category": row["category"],
        "created_at": row["created_at"],
        "url": f"/images/{row['display_name']}",
    }


def require_owner(x_pinboard_owner_token: str | None) -> None:
    if not OWNER_TOKEN:
        return
    if not secrets.compare_digest(x_pinboard_owner_token or "", OWNER_TOKEN):
        raise HTTPException(status_code=401, detail="Owner token required.")


async def read_json_object(request: Request) -> dict[str, Any]:
    """Parse the request body as a JSON object, mapping malformed input to a
    400 instead of an unhandled 500."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="JSON body must be an object.")
    return body


def parse_int(value: Any, name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{name} must be an integer.")


def parse_float(value: Any, name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{name} must be a number.")


def require_local_request(request: Request) -> None:
    remote_addr = request.client.host if request.client else ""
    if remote_addr not in {"127.0.0.1", "::1"}:
        raise HTTPException(status_code=403, detail="This action is only available on the device screen.")


def run_nmcli(args: list[str]) -> subprocess.CompletedProcess[str]:
    command = ["sudo", "/usr/bin/nmcli", *args]
    return subprocess.run(command, capture_output=True, text=True)


def parse_wifi_scan(output: str) -> list[dict[str, str]]:
    networks: dict[str, dict[str, str]] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(":", 3)
        if len(parts) != 4:
            continue
        active, ssid, signal, security = parts
        if not ssid:
            continue
        existing = networks.get(ssid)
        try:
            score = int(signal or "0")
        except ValueError:
            score = 0
        if existing is None or score > int(existing["signal"]):
            networks[ssid] = {
                "ssid": ssid,
                "signal": str(score),
                "security": security or "--",
                "active": "1" if active == "*" else "0",
            }
    return sorted(networks.values(), key=lambda item: (-int(item["signal"]), item["ssid"].lower()))


def get_setting(conn: sqlite3.Connection, key: str, default: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO settings (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def get_custom_schemes(conn: sqlite3.Connection) -> list[dict]:
    raw = get_setting(conn, "color_schemes", "[]")
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []


def get_all_schemes(conn: sqlite3.Connection) -> list[dict]:
    custom = [s for s in get_custom_schemes(conn) if s.get("name") not in BUILTIN_SCHEME_NAMES]
    return BUILTIN_SCHEMES + custom


def get_active_scheme(conn: sqlite3.Connection) -> dict:
    active_name = get_setting(conn, "active_color_scheme", "Dark Violet")
    for scheme in get_all_schemes(conn):
        if scheme["name"] == active_name:
            return scheme
    return BUILTIN_SCHEMES[0]


def clock_payload(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "enabled": get_setting(conn, "clock_enabled", "0") == "1",
        "corner": get_setting(conn, "clock_corner", "bottom-right"),
        "size": get_setting(conn, "clock_size", "medium"),
    }


def settings_payload(conn: sqlite3.Connection, request: Request) -> dict[str, Any]:
    guest_token = get_setting(conn, "guest_token", "")
    base_url = str(request.base_url).rstrip("/")
    admin_url = f"{base_url}/admin"
    guest_url = f"{base_url}/guest/{guest_token}" if guest_token else ""
    return {
        "slide_seconds": int(get_setting(conn, "slide_seconds", str(DEFAULT_SLIDE_SECONDS))),
        "backdrop_blur_px": int(get_setting(conn, "backdrop_blur_px", "8")),
        "backdrop_brightness": float(get_setting(conn, "backdrop_brightness", "0.68")),
        "guest_enabled": get_setting(conn, "guest_enabled", "0") == "1",
        "guest_token": guest_token,
        "admin_url": admin_url,
        "guest_url": guest_url,
        "message_display_seconds": int(get_setting(conn, "message_display_seconds", "8")),
        "clock": clock_payload(conn),
        "slideshow_mode": get_setting(conn, "slideshow_mode", "all"),
    }


def active_images(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT * FROM images
            WHERE status = 'active'
            ORDER BY created_at ASC, id ASC
            """
        )
    )


def choose_next(active: list[sqlite3.Row], current_id: int | None) -> sqlite3.Row:
    if current_id is None:
        return active[0]

    ids = [row["id"] for row in active]
    if current_id not in ids:
        return active[0]

    if len(active) == 1:
        return active[0]

    next_index = (ids.index(current_id) + 1) % len(active)
    return active[next_index]


def get_current_image(conn: sqlite3.Connection) -> dict[str, Any] | None:
    active = active_images(conn)
    if not active:
        conn.execute(
            """
            UPDATE slideshow_state
            SET current_image_id = NULL, push_next_image_id = NULL, last_changed_at = ?
            WHERE id = 1
            """,
            (now(),),
        )
        return None

    state = conn.execute("SELECT * FROM slideshow_state WHERE id = 1").fetchone()
    current_id = state["current_image_id"]
    push_next_id = state["push_next_image_id"]
    slide_seconds = int(get_setting(conn, "slide_seconds", str(DEFAULT_SLIDE_SECONDS)))
    elapsed = now() - float(state["last_changed_at"])

    # Apply the slideshow mode filter. The slideshow only advances through
    # images whose category matches the active mode ("all" matches everything).
    mode = get_setting(conn, "slideshow_mode", "all")
    target_category = MODE_TO_CATEGORY.get(mode)
    filtered = (
        active if target_category is None
        else [row for row in active if row["category"] == target_category]
    )

    all_by_id = {row["id"]: row for row in active}

    def advance_to(row: sqlite3.Row) -> dict[str, Any]:
        conn.execute(
            """
            UPDATE slideshow_state
            SET current_image_id = ?, push_next_image_id = NULL, last_changed_at = ?
            WHERE id = 1
            """,
            (row["id"], now()),
        )
        return row_to_image(row)

    # 1. A queued push-next always wins, even if its category does not match the
    #    current mode — a pushed (e.g. guest) image is shown once regardless.
    if push_next_id in all_by_id and push_next_id != current_id:
        return advance_to(all_by_id[push_next_id])

    # 2. No image matches the current mode: empty-category fallback. Keep showing
    #    the last known image if it still exists; otherwise show nothing.
    if not filtered:
        if current_id in all_by_id:
            return row_to_image(all_by_id[current_id])
        return None

    filtered_by_id = {row["id"]: row for row in filtered}

    # 3. The current image is not in the active category (re-categorized, or a
    #    just-pushed off-category image). Let it finish its slide, then move on
    #    to the active category.
    if current_id not in filtered_by_id:
        if current_id in all_by_id and elapsed < slide_seconds:
            return row_to_image(all_by_id[current_id])
        return advance_to(filtered[0])

    # 4. Normal advance within the active category.
    if elapsed >= slide_seconds:
        return advance_to(choose_next(filtered, current_id))

    return row_to_image(filtered_by_id[current_id])


def normalize_category(value: Any, default: str = "image") -> str:
    category = str(value or default).strip().lower()
    return category if category in IMAGE_CATEGORIES else default


def save_upload(
    file: UploadFile, content: bytes, source: str = "wifi-owner", category: str = "image"
) -> dict[str, Any]:
    category = normalize_category(category)
    original_suffix = Path(file.filename or "").suffix.lower()
    if original_suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Only JPEG, PNG, and WebP images are supported.")
    if file.content_type and file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Only JPEG, PNG, and WebP images are supported.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Image is too large.")

    image_id = uuid.uuid4().hex
    original_name = f"{image_id}{original_suffix}"
    display_name = f"{image_id}.jpg"
    original_path = ORIGINALS_DIR / original_name
    display_path = DISPLAY_DIR / display_name

    try:
        original_path.write_bytes(content)
        with Image.open(original_path) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail(DISPLAY_MAX_SIZE, Image.Resampling.LANCZOS)
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.save(display_path, "JPEG", quality=88, optimize=True)
    except UnidentifiedImageError as exc:
        original_path.unlink(missing_ok=True)
        display_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Uploaded file is not a readable image.") from exc
    except Exception:
        original_path.unlink(missing_ok=True)
        display_path.unlink(missing_ok=True)
        raise

    with db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO images
                (original_name, stored_name, display_name, source, status, category, created_at)
            VALUES (?, ?, ?, ?, 'active', ?, ?)
            """,
            (file.filename or original_name, original_name, display_name, source, category, now()),
        )
        row = conn.execute("SELECT * FROM images WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return row_to_image(row)


def clear_slideshow_state_if_referenced(conn: sqlite3.Connection, image_id: int) -> None:
    """Reset slideshow state when the given image is the current or queued
    push-next image, so the next poll re-picks instead of using stale ids."""
    state = conn.execute("SELECT * FROM slideshow_state WHERE id = 1").fetchone()
    if state["current_image_id"] == image_id or state["push_next_image_id"] == image_id:
        conn.execute(
            """
            UPDATE slideshow_state
            SET current_image_id = NULL, push_next_image_id = NULL, last_changed_at = 0
            WHERE id = 1
            """
        )


def guest_allowed(conn: sqlite3.Connection, token: str) -> bool:
    return (
        get_setting(conn, "guest_enabled", "0") == "1"
        and secrets.compare_digest(token, get_setting(conn, "guest_token", ""))
    )


def enforce_guest_rate_limit(conn: sqlite3.Connection, token: str, remote_addr: str) -> None:
    cutoff = now() - GUEST_UPLOAD_WINDOW_SECONDS
    conn.execute("DELETE FROM guest_uploads WHERE uploaded_at < ?", (cutoff,))
    count = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM guest_uploads
        WHERE token = ? AND remote_addr = ? AND uploaded_at >= ?
        """,
        (token, remote_addr, cutoff),
    ).fetchone()["count"]
    if count >= GUEST_UPLOAD_LIMIT:
        raise HTTPException(status_code=429, detail="Too many guest uploads. Try again later.")
    conn.execute(
        "INSERT INTO guest_uploads (token, remote_addr, uploaded_at) VALUES (?, ?, ?)",
        (token, remote_addr, now()),
    )


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    return '<meta http-equiv="refresh" content="0; url=/admin">'


@app.get("/admin", response_class=HTMLResponse)
def admin() -> str:
    return (STATIC_DIR / "admin.html").read_text()


@app.get("/guest/{token}", response_class=HTMLResponse)
def guest(token: str) -> str:
    return (STATIC_DIR / "guest.html").read_text().replace("__GUEST_TOKEN__", token)


@app.get("/frame", response_class=HTMLResponse)
def frame() -> str:
    return (STATIC_DIR / "frame.html").read_text()


@app.get("/api/images")
def list_images(x_pinboard_owner_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_owner(x_pinboard_owner_token)
    with db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM images
            ORDER BY created_at DESC, id DESC
            """
        ).fetchall()
        return {"images": [row_to_image(row) for row in rows]}


@app.post("/api/images")
async def upload_image(
    file: UploadFile = File(...),
    category: str = Form(default="image"),
    x_pinboard_owner_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_owner(x_pinboard_owner_token)
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Upload was empty.")
    return {"image": save_upload(file, content, category=category)}


@app.delete("/api/images/{image_id}")
def delete_image(
    image_id: int,
    x_pinboard_owner_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_owner(x_pinboard_owner_token)
    with db() as conn:
        row = conn.execute("SELECT * FROM images WHERE id = ?", (image_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Image not found.")
        conn.execute("UPDATE images SET status = 'deleted' WHERE id = ?", (image_id,))
        clear_slideshow_state_if_referenced(conn, image_id)
        return {"ok": True}


@app.post("/api/images/{image_id}/push-next")
def push_next(
    image_id: int,
    x_pinboard_owner_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_owner(x_pinboard_owner_token)
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM images WHERE id = ? AND status = 'active'",
            (image_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Image not found.")
        conn.execute(
            "UPDATE slideshow_state SET push_next_image_id = ? WHERE id = 1",
            (image_id,),
        )
        return {"ok": True, "image": row_to_image(row)}


@app.post("/api/images/{image_id}/hide")
def hide_image(
    image_id: int,
    x_pinboard_owner_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_owner(x_pinboard_owner_token)
    with db() as conn:
        row = conn.execute("SELECT * FROM images WHERE id = ?", (image_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Image not found.")
        conn.execute("UPDATE images SET status = 'hidden' WHERE id = ?", (image_id,))
        clear_slideshow_state_if_referenced(conn, image_id)
        return {"ok": True}


@app.post("/api/images/{image_id}/restore")
def restore_image(
    image_id: int,
    x_pinboard_owner_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_owner(x_pinboard_owner_token)
    with db() as conn:
        row = conn.execute("SELECT * FROM images WHERE id = ?", (image_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Image not found.")
        conn.execute("UPDATE images SET status = 'active' WHERE id = ?", (image_id,))
        return {"ok": True}


@app.patch("/api/images/{image_id}")
async def update_image(
    image_id: int,
    request: Request,
    x_pinboard_owner_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_owner(x_pinboard_owner_token)
    body = await read_json_object(request)
    with db() as conn:
        row = conn.execute("SELECT * FROM images WHERE id = ?", (image_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Image not found.")
        if "category" in body:
            category = normalize_category(body["category"], default=row["category"])
            conn.execute("UPDATE images SET category = ? WHERE id = ?", (category, image_id))
            # If the currently displayed image was re-categorized, clear state so
            # the slideshow re-picks against the active mode (like hide/delete).
            state = conn.execute("SELECT * FROM slideshow_state WHERE id = 1").fetchone()
            if state["current_image_id"] == image_id and category != row["category"]:
                conn.execute(
                    """
                    UPDATE slideshow_state
                    SET current_image_id = NULL, push_next_image_id = NULL, last_changed_at = 0
                    WHERE id = 1
                    """
                )
        updated = conn.execute("SELECT * FROM images WHERE id = ?", (image_id,)).fetchone()
        return {"ok": True, "image": row_to_image(updated)}


@app.post("/api/guest/{token}/images")
async def guest_upload(
    token: str,
    request: Request,
    file: UploadFile = File(...),
    category: str = Form(default="image"),
) -> dict[str, Any]:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Upload was empty.")
    remote_addr = request.client.host if request.client else "unknown"
    with db() as conn:
        if not guest_allowed(conn, token):
            raise HTTPException(status_code=404, detail="Guest upload link is disabled.")
        enforce_guest_rate_limit(conn, token, remote_addr)
    image = save_upload(file, content, source="guest-link", category=category)
    with db() as conn:
        conn.execute("UPDATE slideshow_state SET push_next_image_id = ? WHERE id = 1", (image["id"],))
    return {"ok": True, "image": image}


@app.post("/api/guest/{token}/message")
async def guest_message(token: str, request: Request) -> dict[str, Any]:
    body = await read_json_object(request)
    content = str(body.get("content", "")).strip()
    if not content:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")
    if len(content) > 200:
        raise HTTPException(status_code=400, detail="Message is too long (max 200 characters).")
    remote_addr = request.client.host if request.client else "unknown"
    with db() as conn:
        if not guest_allowed(conn, token):
            raise HTTPException(status_code=404, detail="Guest link is disabled.")
        enforce_guest_rate_limit(conn, token, remote_addr)
        conn.execute(
            "INSERT INTO messages (content, created_at) VALUES (?, ?)",
            (content, now()),
        )
    return {"ok": True}


@app.post("/api/messages/{message_id}/shown")
def mark_message_shown(message_id: int) -> dict[str, Any]:
    with db() as conn:
        conn.execute(
            "UPDATE messages SET shown_at = ? WHERE id = ? AND shown_at IS NULL",
            (now(), message_id),
        )
    return {"ok": True}


@app.get("/api/settings")
def get_settings(
    request: Request,
    x_pinboard_owner_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_owner(x_pinboard_owner_token)
    with db() as conn:
        return {"settings": settings_payload(conn, request)}


def apply_slideshow_mode(conn: sqlite3.Connection, value: Any) -> None:
    mode = str(value)
    if mode not in SLIDESHOW_MODES:
        raise HTTPException(status_code=400, detail="Invalid slideshow mode.")
    previous = get_setting(conn, "slideshow_mode", "all")
    set_setting(conn, "slideshow_mode", mode)
    # Changing the mode takes effect immediately: clear the current image so the
    # next poll re-picks from the start of the newly active category rather than
    # finishing the current slide.
    if mode != previous:
        conn.execute(
            """
            UPDATE slideshow_state
            SET current_image_id = NULL, push_next_image_id = NULL, last_changed_at = 0
            WHERE id = 1
            """
        )


def apply_clock_settings(conn: sqlite3.Connection, body: dict[str, Any]) -> None:
    """Validate and persist any clock_* keys present in body. Shared by the
    owner settings PATCH and the localhost-permitted frame clock endpoint."""
    if "clock_enabled" in body:
        set_setting(conn, "clock_enabled", "1" if bool(body["clock_enabled"]) else "0")
    if "clock_corner" in body:
        corner = str(body["clock_corner"])
        if corner not in CLOCK_CORNERS:
            raise HTTPException(status_code=400, detail="Invalid clock corner.")
        set_setting(conn, "clock_corner", corner)
    if "clock_size" in body:
        size = str(body["clock_size"])
        if size not in CLOCK_SIZES:
            raise HTTPException(status_code=400, detail="Invalid clock size.")
        set_setting(conn, "clock_size", size)


@app.patch("/api/settings")
async def update_settings(
    request: Request,
    x_pinboard_owner_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_owner(x_pinboard_owner_token)
    body = await read_json_object(request)
    with db() as conn:
        if "slide_seconds" in body:
            value = max(5, min(120, parse_int(body["slide_seconds"], "slide_seconds")))
            set_setting(conn, "slide_seconds", str(value))
        if "backdrop_blur_px" in body:
            value = max(0, min(24, parse_int(body["backdrop_blur_px"], "backdrop_blur_px")))
            set_setting(conn, "backdrop_blur_px", str(value))
        if "backdrop_brightness" in body:
            value = max(0.25, min(1.0, parse_float(body["backdrop_brightness"], "backdrop_brightness")))
            set_setting(conn, "backdrop_brightness", str(value))
        if "guest_enabled" in body:
            set_setting(conn, "guest_enabled", "1" if bool(body["guest_enabled"]) else "0")
        if "message_display_seconds" in body:
            value = max(3, min(30, parse_int(body["message_display_seconds"], "message_display_seconds")))
            set_setting(conn, "message_display_seconds", str(value))
        if "slideshow_mode" in body:
            apply_slideshow_mode(conn, body["slideshow_mode"])
        apply_clock_settings(conn, body)
        return {"settings": settings_payload(conn, request)}


@app.post("/api/settings/guest-token")
def regenerate_guest_token(
    request: Request,
    x_pinboard_owner_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_owner(x_pinboard_owner_token)
    with db() as conn:
        set_setting(conn, "guest_token", secrets.token_urlsafe(16))
        return {"settings": settings_payload(conn, request)}


@app.get("/api/color-schemes")
def list_color_schemes() -> dict[str, Any]:
    with db() as conn:
        return {
            "schemes": get_all_schemes(conn),
            "active_name": get_setting(conn, "active_color_scheme", "Dark Violet"),
        }


@app.post("/api/color-schemes")
async def save_color_scheme(
    request: Request,
    x_pinboard_owner_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_owner(x_pinboard_owner_token)
    body = await read_json_object(request)
    name = str(body.get("name", "")).strip()
    tokens = body.get("tokens", {})
    if not name:
        raise HTTPException(status_code=400, detail="Scheme name is required.")
    if name in BUILTIN_SCHEME_NAMES:
        raise HTTPException(status_code=400, detail="Cannot modify built-in schemes.")
    if not isinstance(tokens, dict) or not tokens:
        raise HTTPException(status_code=400, detail="Tokens dict is required.")
    with db() as conn:
        custom = get_custom_schemes(conn)
        for i, s in enumerate(custom):
            if s.get("name") == name:
                custom[i] = {"name": name, "builtin": False, "tokens": tokens}
                break
        else:
            custom.append({"name": name, "builtin": False, "tokens": tokens})
        set_setting(conn, "color_schemes", json.dumps(custom))
        return {"ok": True, "scheme": {"name": name, "builtin": False, "tokens": tokens}}


@app.delete("/api/color-schemes/{name}")
def delete_color_scheme(
    name: str,
    x_pinboard_owner_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_owner(x_pinboard_owner_token)
    if name in BUILTIN_SCHEME_NAMES:
        raise HTTPException(status_code=400, detail="Cannot delete built-in schemes.")
    with db() as conn:
        custom = [s for s in get_custom_schemes(conn) if s.get("name") != name]
        set_setting(conn, "color_schemes", json.dumps(custom))
        if get_setting(conn, "active_color_scheme", "") == name:
            set_setting(conn, "active_color_scheme", "Dark Violet")
        return {"ok": True}


@app.post("/api/color-schemes/{name}/activate")
def activate_color_scheme(
    name: str,
    x_pinboard_owner_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_owner(x_pinboard_owner_token)
    with db() as conn:
        if not any(s["name"] == name for s in get_all_schemes(conn)):
            raise HTTPException(status_code=404, detail="Scheme not found.")
        set_setting(conn, "active_color_scheme", name)
        return {"ok": True, "scheme": get_active_scheme(conn)}


@app.get("/api/qr.svg")
def qr_svg(data: str) -> Response:
    if not data:
        raise HTTPException(status_code=400, detail="Missing QR data.")
    try:
        import qrcode
        import qrcode.image.svg

        factory = qrcode.image.svg.SvgPathImage
        image = qrcode.make(data, image_factory=factory, border=2)
        buffer = BytesIO()
        image.save(buffer)
        svg = buffer.getvalue()
    except Exception:
        escaped = data.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="320">'
            '<rect width="100%" height="100%" fill="white"/>'
            f'<text x="16" y="32" font-size="14" fill="black">{escaped}</text>'
            "</svg>"
        ).encode()
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/api/slideshow/current")
def slideshow_current() -> dict[str, Any]:
    with db() as conn:
        image = get_current_image(conn)
        slide_seconds = int(get_setting(conn, "slide_seconds", str(DEFAULT_SLIDE_SECONDS)))
        backdrop_blur_px = int(get_setting(conn, "backdrop_blur_px", "8"))
        backdrop_brightness = float(get_setting(conn, "backdrop_brightness", "0.68"))
        message_display_seconds = int(get_setting(conn, "message_display_seconds", "8"))
        msg_row = conn.execute(
            "SELECT id, content FROM messages WHERE shown_at IS NULL ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
        message = {"id": msg_row["id"], "content": msg_row["content"]} if msg_row else None
        return {
            "image": image,
            "slide_seconds": slide_seconds,
            "display": {
                "backdrop_blur_px": backdrop_blur_px,
                "backdrop_brightness": backdrop_brightness,
            },
            "message": message,
            "message_display_seconds": message_display_seconds,
            "clock": clock_payload(conn),
            "slideshow_mode": get_setting(conn, "slideshow_mode", "all"),
            "server_time": now(),
        }


@app.post("/api/frame/mode")
async def frame_mode(
    request: Request,
    x_pinboard_owner_token: str | None = Header(default=None),
) -> dict[str, Any]:
    # Localhost-or-owner dual-auth, same as the frame clock endpoint, so the
    # on-device frame menu (which has no owner token) can change the mode.
    remote_addr = request.client.host if request.client else ""
    if remote_addr not in {"127.0.0.1", "::1"}:
        require_owner(x_pinboard_owner_token)
    body = await read_json_object(request)
    with db() as conn:
        apply_slideshow_mode(conn, body.get("slideshow_mode"))
        return {"ok": True, "slideshow_mode": get_setting(conn, "slideshow_mode", "all")}


@app.post("/api/frame/clock")
async def frame_clock(
    request: Request,
    x_pinboard_owner_token: str | None = Header(default=None),
) -> dict[str, Any]:
    # Allow either a valid owner token or a request from the device itself
    # (the kiosk frame menu, which has no owner token). Mirrors the dual-auth
    # used by POST /api/network/connect.
    remote_addr = request.client.host if request.client else ""
    if remote_addr not in {"127.0.0.1", "::1"}:
        require_owner(x_pinboard_owner_token)
    body = await read_json_object(request)
    with db() as conn:
        apply_clock_settings(conn, body)
        return {"ok": True, "clock": clock_payload(conn)}


@app.post("/api/network/save")
async def save_network(
    request: Request,
    x_pinboard_owner_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_owner(x_pinboard_owner_token)
    body = await read_json_object(request)
    ssid = str(body.get("ssid", "")).strip()
    password = str(body.get("password", "")).strip()
    hidden = bool(body.get("hidden", False))

    if not ssid:
        raise HTTPException(status_code=400, detail="SSID is required.")
    if len(ssid) > 32:
        raise HTTPException(status_code=400, detail="SSID is too long.")
    if password and len(password) < 8:
        raise HTTPException(status_code=400, detail="Wi-Fi passwords must be at least 8 characters.")

    args = ["connection", "add", "type", "wifi", "con-name", ssid, "ssid", ssid]
    if hidden:
        args += ["802-11-wireless.hidden", "yes"]
    if password:
        args += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password]

    result = await run_in_threadpool(run_nmcli, args)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Unable to save the network.").strip()
        raise HTTPException(status_code=400, detail=detail)

    return {"ok": True, "message": f"Network '{ssid}' saved. The Pi will connect automatically when in range."}


@app.get("/api/network/wifi")
def wifi_scan(x_pinboard_owner_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_owner(x_pinboard_owner_token)
    result = run_nmcli(["-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "dev", "wifi", "list", "--rescan", "yes"])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Unable to scan Wi-Fi networks.").strip()
        raise HTTPException(status_code=400, detail=detail)
    return {"networks": parse_wifi_scan(result.stdout)}


@app.post("/api/network/connect")
async def connect_network(
    request: Request,
    x_pinboard_owner_token: str | None = Header(default=None),
) -> dict[str, Any]:
    # Allow either a valid owner token or a request from the device itself (kiosk/frame).
    remote_addr = request.client.host if request.client else ""
    is_local = remote_addr in {"127.0.0.1", "::1"}
    if not is_local:
        require_owner(x_pinboard_owner_token)

    body = await read_json_object(request)
    ssid = str(body.get("ssid", "")).strip()
    password = str(body.get("password", "")).strip()
    hidden = bool(body.get("hidden", False))

    if not ssid:
        raise HTTPException(status_code=400, detail="SSID is required.")
    if len(ssid) > 32:
        raise HTTPException(status_code=400, detail="SSID is too long.")
    if password and len(password) < 8:
        raise HTTPException(status_code=400, detail="Wi-Fi passwords must be at least 8 characters.")

    args = ["dev", "wifi", "connect", ssid]
    if hidden:
        args += ["hidden", "yes"]
    if password:
        args += ["password", password]

    result = await run_in_threadpool(run_nmcli, args)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Unable to add the network.").strip()
        raise HTTPException(status_code=400, detail=detail)

    message = result.stdout.strip() or f"Connected to {ssid}."
    return {"ok": True, "message": message}


@app.get("/api/network/ip")
async def get_device_ip() -> dict[str, Any]:
    ip = "unknown"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    hostname = "unknown"
    try:
        hostname = socket.gethostname()
    except Exception:
        pass
    return {"ip": ip, "hostname": hostname, "mdns": f"{hostname}.local" if hostname != "unknown" else "unknown"}
