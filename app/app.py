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

from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
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
                created_at REAL NOT NULL
            )
            """
        )
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
        "created_at": row["created_at"],
        "url": f"/images/{row['display_name']}",
    }


def require_owner(x_pinboard_owner_token: str | None) -> None:
    if not OWNER_TOKEN:
        return
    if not secrets.compare_digest(x_pinboard_owner_token or "", OWNER_TOKEN):
        raise HTTPException(status_code=401, detail="Owner token required.")


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
        score = int(signal or "0")
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

    active_by_id = {row["id"]: row for row in active}
    selected: sqlite3.Row | None = None

    if push_next_id in active_by_id and push_next_id != current_id:
        selected = active_by_id[push_next_id]
        conn.execute(
            """
            UPDATE slideshow_state
            SET current_image_id = ?, push_next_image_id = NULL, last_changed_at = ?
            WHERE id = 1
            """,
            (selected["id"], now()),
        )
    elif current_id not in active_by_id:
        selected = active[0]
        conn.execute(
            """
            UPDATE slideshow_state
            SET current_image_id = ?, push_next_image_id = NULL, last_changed_at = ?
            WHERE id = 1
            """,
            (selected["id"], now()),
        )
    elif elapsed >= slide_seconds:
        selected = choose_next(active, current_id)
        conn.execute(
            """
            UPDATE slideshow_state
            SET current_image_id = ?, push_next_image_id = NULL, last_changed_at = ?
            WHERE id = 1
            """,
            (selected["id"], now()),
        )
    else:
        selected = active_by_id[current_id]

    return row_to_image(selected)


def save_upload(file: UploadFile, content: bytes, source: str = "wifi-owner") -> dict[str, Any]:
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
                (original_name, stored_name, display_name, source, status, created_at)
            VALUES (?, ?, ?, ?, 'active', ?)
            """,
            (file.filename or original_name, original_name, display_name, source, now()),
        )
        row = conn.execute("SELECT * FROM images WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return row_to_image(row)


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
    x_pinboard_owner_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_owner(x_pinboard_owner_token)
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Upload was empty.")
    return {"image": save_upload(file, content)}


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
        state = conn.execute("SELECT * FROM slideshow_state WHERE id = 1").fetchone()
        if state["current_image_id"] == image_id or state["push_next_image_id"] == image_id:
            conn.execute(
                """
                UPDATE slideshow_state
                SET current_image_id = NULL, push_next_image_id = NULL, last_changed_at = 0
                WHERE id = 1
                """
            )
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


@app.post("/api/guest/{token}/images")
async def guest_upload(token: str, request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Upload was empty.")
    remote_addr = request.client.host if request.client else "unknown"
    with db() as conn:
        if not guest_allowed(conn, token):
            raise HTTPException(status_code=404, detail="Guest upload link is disabled.")
        enforce_guest_rate_limit(conn, token, remote_addr)
    image = save_upload(file, content, source="guest-link")
    with db() as conn:
        conn.execute("UPDATE slideshow_state SET push_next_image_id = ? WHERE id = 1", (image["id"],))
    return {"ok": True, "image": image}


@app.post("/api/guest/{token}/message")
async def guest_message(token: str, request: Request) -> dict[str, Any]:
    body = await request.json()
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


@app.patch("/api/settings")
async def update_settings(
    request: Request,
    x_pinboard_owner_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_owner(x_pinboard_owner_token)
    body = await request.json()
    with db() as conn:
        if "slide_seconds" in body:
            value = max(5, min(120, int(body["slide_seconds"])))
            set_setting(conn, "slide_seconds", str(value))
        if "backdrop_blur_px" in body:
            value = max(0, min(24, int(body["backdrop_blur_px"])))
            set_setting(conn, "backdrop_blur_px", str(value))
        if "backdrop_brightness" in body:
            value = max(0.25, min(1.0, float(body["backdrop_brightness"])))
            set_setting(conn, "backdrop_brightness", str(value))
        if "guest_enabled" in body:
            set_setting(conn, "guest_enabled", "1" if bool(body["guest_enabled"]) else "0")
        if "message_display_seconds" in body:
            value = max(3, min(30, int(body["message_display_seconds"])))
            set_setting(conn, "message_display_seconds", str(value))
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
    body = await request.json()
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
            "server_time": now(),
        }


@app.post("/api/network/save")
async def save_network(
    request: Request,
    x_pinboard_owner_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_owner(x_pinboard_owner_token)
    body = await request.json()
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

    body = await request.json()
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
    return {"ip": ip}
