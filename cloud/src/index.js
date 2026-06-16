// Memomatic relay Worker — remote access backend (issue #87).
//
// Stages: 1 = skeleton + device sync; 2 = guest site (event code + upload/
// message); 3 = owner settings (behind Access). Bindings (wrangler.toml):
// DB (D1), UPLOADS (R2), ASSETS (static). Secret: DEVICE_SECRET (shared with
// the Pi); optional SESSION_SECRET (falls back to DEVICE_SECRET for cookies).

const PULL_LIMIT = 25;
const CLAIM_STALE_MS = 2 * 60 * 1000;     // re-deliver claimed-but-unacked after 2 min
const SESSION_TTL_MS = 6 * 60 * 60 * 1000; // guest session lifetime
const RATE_WINDOW_MS = 10 * 60 * 1000;
const UPLOAD_LIMIT = 20;                   // per code+IP per window
const MSG_LIMIT = 20;
const VERIFY_LIMIT = 10;                   // code-guess attempts per IP per window
const MAX_MESSAGE_LEN = 200;

const now = () => Date.now();

function json(data, status = 200, extra = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json", ...extra },
  });
}

// Constant-time compare so secrets can't be recovered via timing.
function safeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;
  const len = Math.max(a.length, b.length);
  let diff = a.length ^ b.length;
  for (let i = 0; i < len; i++) diff |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  return diff === 0;
}

function deviceAuthed(request, env) {
  const m = (request.headers.get("authorization") || "").match(/^Bearer\s+(.+)$/i);
  return m ? safeEqual(m[1], env.DEVICE_SECRET || "\0") : false;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const { pathname } = url;
    const method = request.method;

    try {
      if (pathname === "/api/health") return json({ ok: true, ts: now() });

      // Device sync endpoints — bearer DEVICE_SECRET, Pi connects outbound only.
      if (pathname.startsWith("/api/sync/")) {
        if (!deviceAuthed(request, env)) return json({ error: "unauthorized" }, 401);
        if (pathname === "/api/sync/pull" && method === "GET") return syncPull(env);
        if (pathname === "/api/sync/ack" && method === "POST") return syncAck(request, env);
        if (pathname === "/api/sync/state" && method === "POST") return syncState(request, env);
        if (pathname === "/api/sync/object" && method === "GET") return syncObject(url, env);
        return json({ error: "not found" }, 404);
      }

      // Guest API — gated by a short per-event code exchanged for a signed cookie.
      if (pathname.startsWith("/api/g/")) {
        if (pathname === "/api/g/verify" && method === "POST") return guestVerify(request, env);
        if (pathname === "/api/g/upload" && method === "POST") return guestUpload(request, env);
        if (pathname === "/api/g/message" && method === "POST") return guestMessage(request, env);
        return json({ error: "not found" }, 404);
      }

      // Owner settings API, fronted by Cloudflare Access (stage 3).
      if (pathname.startsWith("/api/o/")) return json({ error: "not implemented (stage 3)" }, 501);

      // Anything else: static assets (guest UI is public/index.html).
      if (env.ASSETS) return env.ASSETS.fetch(request);
      return json({ error: "not found" }, 404);
    } catch (err) {
      return json({ error: "server error", detail: String((err && err.message) || err) }, 500);
    }
  },
};

// ── Device sync ──────────────────────────────────────────────────────────────

// Hand the Pi a batch of pending (or stale-claimed) submissions + unapplied
// settings commands, re-stamping them claimed. Stale re-delivery makes pull
// at-least-once: if the Pi crashes after pull but before ack, the items come
// back after CLAIM_STALE_MS instead of being stranded.
async function syncPull(env) {
  const cutoff = now() - CLAIM_STALE_MS;
  const subs = await env.DB.prepare(
    "SELECT id, kind, r2_key, content, category, created_at FROM submissions " +
    "WHERE status='pending' OR (status='claimed' AND claimed_at < ?) ORDER BY id ASC LIMIT ?"
  ).bind(cutoff, PULL_LIMIT).all();
  const rows = subs.results || [];

  if (rows.length) {
    const ids = rows.map((r) => r.id);
    const ph = ids.map(() => "?").join(",");
    await env.DB.prepare(
      `UPDATE submissions SET status='claimed', claimed_at=? WHERE id IN (${ph})`
    ).bind(now(), ...ids).run();
  }

  const cmds = await env.DB.prepare(
    "SELECT id, key, value FROM settings_commands WHERE applied_at IS NULL ORDER BY id ASC"
  ).all();

  return json({
    submissions: rows.map((r) => ({
      id: r.id,
      kind: r.kind,
      category: r.category,
      content: r.kind === "message" ? r.content : null,
      object_key: r.kind === "image" ? r.r2_key : null,
      created_at: r.created_at,
    })),
    settings: (cmds.results || []).map((c) => ({ id: c.id, key: c.key, value: c.value })),
  });
}

// Idempotent: drop acked submissions (+ their R2 objects) and mark settings
// applied. Re-acking an already-deleted id is a harmless no-op.
async function syncAck(request, env) {
  const body = await request.json().catch(() => ({}));
  const subIds = Array.isArray(body.submission_ids) ? body.submission_ids : [];
  const setIds = Array.isArray(body.settings_ids) ? body.settings_ids : [];

  for (const id of subIds) {
    const row = await env.DB.prepare("SELECT r2_key FROM submissions WHERE id=?").bind(id).first();
    if (row && row.r2_key) await env.UPLOADS.delete(row.r2_key).catch(() => {});
    await env.DB.prepare("DELETE FROM submissions WHERE id=?").bind(id).run();
  }
  if (setIds.length) {
    const ph = setIds.map(() => "?").join(",");
    await env.DB.prepare(
      `UPDATE settings_commands SET applied_at=? WHERE id IN (${ph})`
    ).bind(now(), ...setIds).run();
  }
  return json({ ok: true, acked: subIds.length, settings_applied: setIds.length });
}

// The Pi pushes its current settings up so the owner UI shows live values.
async function syncState(request, env) {
  const body = await request.json().catch(() => ({}));
  const settings = body && typeof body.settings === "object" && body.settings ? body.settings : {};
  await env.DB.prepare(
    "UPDATE device_state SET settings_json=?, last_seen_at=? WHERE id=1"
  ).bind(JSON.stringify(settings), now()).run();
  return json({ ok: true });
}

// Stream a pending upload's bytes to the Pi (device auth already checked).
async function syncObject(url, env) {
  const key = url.searchParams.get("key");
  if (!key) return json({ error: "key required" }, 400);
  const obj = await env.UPLOADS.get(key);
  if (!obj) return json({ error: "not found" }, 404);
  return new Response(obj.body, {
    headers: { "content-type": obj.httpMetadata?.contentType || "application/octet-stream" },
  });
}

// ── Guest session helpers ────────────────────────────────────────────────────

function sessionSecret(env) {
  return env.SESSION_SECRET || env.DEVICE_SECRET || "\0";
}

function b64urlEncode(str) {
  return btoa(str).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
function b64urlDecode(str) {
  const s = str.replace(/-/g, "+").replace(/_/g, "/");
  return atob(s);
}

async function hmacHex(secret, msg) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]
  );
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(msg));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function makeSession(env, code) {
  const payload = b64urlEncode(JSON.stringify({ code, exp: now() + SESSION_TTL_MS }));
  const sig = await hmacHex(sessionSecret(env), payload);
  return `${payload}.${sig}`;
}

async function readSession(env, token) {
  if (!token) return null;
  const [payload, sig] = token.split(".");
  if (!payload || !sig) return null;
  const expect = await hmacHex(sessionSecret(env), payload);
  if (!safeEqual(sig, expect)) return null;
  let data;
  try { data = JSON.parse(b64urlDecode(payload)); } catch { return null; }
  if (!data || typeof data.exp !== "number" || data.exp < now()) return null;
  return data;
}

function getCookie(request, name) {
  const m = (request.headers.get("cookie") || "").match(new RegExp(`(?:^|; )${name}=([^;]+)`));
  return m ? decodeURIComponent(m[1]) : null;
}

function clientIp(request) {
  return request.headers.get("cf-connecting-ip") || "0";
}

async function codeValid(env, code) {
  if (!code) return false;
  const row = await env.DB.prepare(
    "SELECT expires_at, active FROM event_codes WHERE code=?"
  ).bind(code).first();
  if (!row || !row.active) return false;
  if (row.expires_at && row.expires_at < now()) return false;
  return true;
}

// Sliding-window rate limit backed by the rate_log table.
async function rateOk(env, scope, limit) {
  const cutoff = now() - RATE_WINDOW_MS;
  await env.DB.prepare("DELETE FROM rate_log WHERE at < ?").bind(cutoff).run();
  const row = await env.DB.prepare(
    "SELECT COUNT(*) AS n FROM rate_log WHERE scope=? AND at >= ?"
  ).bind(scope, cutoff).first();
  if ((row?.n || 0) >= limit) return false;
  await env.DB.prepare("INSERT INTO rate_log (scope, at) VALUES (?, ?)").bind(scope, now()).run();
  return true;
}

// ── Guest endpoints ──────────────────────────────────────────────────────────

async function guestVerify(request, env) {
  if (!(await rateOk(env, `verify:${clientIp(request)}`, VERIFY_LIMIT)))
    return json({ error: "too many attempts, try again later" }, 429);
  const body = await request.json().catch(() => ({}));
  const code = String(body.code || "").trim().toUpperCase();
  if (!(await codeValid(env, code))) return json({ error: "invalid or expired code" }, 403);
  const token = await makeSession(env, code);
  const cookie = `ms=${encodeURIComponent(token)}; HttpOnly; Secure; SameSite=Strict; ` +
    `Path=/; Max-Age=${Math.floor(SESSION_TTL_MS / 1000)}`;
  return json({ ok: true }, 200, { "set-cookie": cookie });
}

async function requireSession(request, env) {
  return readSession(env, getCookie(request, "ms"));
}

async function guestUpload(request, env) {
  const sess = await requireSession(request, env);
  if (!sess) return json({ error: "enter the event code first" }, 401);
  if (!(await rateOk(env, `up:${sess.code}:${clientIp(request)}`, UPLOAD_LIMIT)))
    return json({ error: "slow down — too many uploads" }, 429);

  const form = await request.formData();
  const file = form.get("file");
  if (!file || typeof file === "string") return json({ error: "no file" }, 400);
  const category = form.get("category") === "meme" ? "meme" : "image";

  const name = file.name || "";
  const dot = name.lastIndexOf(".");
  const ext = dot >= 0 ? name.slice(dot).toLowerCase() : "";
  const allowed = (env.ALLOWED_EXT || ".jpg,.jpeg,.png,.webp").split(",");
  if (!allowed.includes(ext)) return json({ error: "only JPEG, PNG, or WebP" }, 400);

  const buf = await file.arrayBuffer();
  const max = parseInt(env.MAX_UPLOAD_BYTES || "15728640", 10);
  if (buf.byteLength === 0) return json({ error: "empty file" }, 400);
  if (buf.byteLength > max) return json({ error: "file too large" }, 400);

  const key = `u/${crypto.randomUUID()}${ext}`;
  await env.UPLOADS.put(key, buf, {
    httpMetadata: { contentType: file.type || "application/octet-stream" },
  });
  await env.DB.prepare(
    "INSERT INTO submissions (kind, r2_key, category, status, created_at) " +
    "VALUES ('image', ?, ?, 'pending', ?)"
  ).bind(key, category, now()).run();
  return json({ ok: true });
}

async function guestMessage(request, env) {
  const sess = await requireSession(request, env);
  if (!sess) return json({ error: "enter the event code first" }, 401);
  if (!(await rateOk(env, `msg:${sess.code}:${clientIp(request)}`, MSG_LIMIT)))
    return json({ error: "slow down — too many messages" }, 429);

  const body = await request.json().catch(() => ({}));
  const content = String(body.content || "").trim();
  if (!content) return json({ error: "message cannot be empty" }, 400);
  if (content.length > MAX_MESSAGE_LEN) return json({ error: `max ${MAX_MESSAGE_LEN} characters` }, 400);

  await env.DB.prepare(
    "INSERT INTO submissions (kind, content, status, created_at) VALUES ('message', ?, 'pending', ?)"
  ).bind(content, now()).run();
  return json({ ok: true });
}
