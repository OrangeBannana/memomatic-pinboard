// Memomatic relay Worker — remote access backend (issue #87).
//
// Stage 1 (this file): project skeleton, device-secret auth, and the
// device-facing sync endpoints the Pi agent will poll. Guest (/api/g/*) and
// owner (/api/o/*) routes are stubbed and filled in stages 2 and 3.
//
// Bindings (wrangler.toml): DB (D1), UPLOADS (R2), ASSETS (static).
// Secret: DEVICE_SECRET — shared with the Pi's pinboard-cloudsync agent.

const PULL_LIMIT = 25;

function json(data, status = 200, extra = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json", ...extra },
  });
}

// Constant-time compare so the device secret can't be recovered via timing.
function safeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;
  const len = Math.max(a.length, b.length);
  let diff = a.length ^ b.length;
  for (let i = 0; i < len; i++) {
    diff |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  }
  return diff === 0;
}

function deviceAuthed(request, env) {
  const m = (request.headers.get("authorization") || "").match(/^Bearer\s+(.+)$/i);
  return m ? safeEqual(m[1], env.DEVICE_SECRET || "\0") : false;
}

const now = () => Date.now();

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const { pathname } = url;
    const method = request.method;

    try {
      if (pathname === "/api/health") {
        return json({ ok: true, ts: now() });
      }

      // Device sync endpoints — bearer DEVICE_SECRET, Pi connects outbound only.
      if (pathname.startsWith("/api/sync/")) {
        if (!deviceAuthed(request, env)) return json({ error: "unauthorized" }, 401);
        if (pathname === "/api/sync/pull" && method === "GET") return syncPull(env);
        if (pathname === "/api/sync/ack" && method === "POST") return syncAck(request, env);
        if (pathname === "/api/sync/state" && method === "POST") return syncState(request, env);
        if (pathname === "/api/sync/object" && method === "GET") return syncObject(url, env);
        return json({ error: "not found" }, 404);
      }

      // Guest upload/message API (stage 2).
      if (pathname.startsWith("/api/g/")) return json({ error: "not implemented (stage 2)" }, 501);

      // Owner settings API, fronted by Cloudflare Access (stage 3).
      if (pathname.startsWith("/api/o/")) return json({ error: "not implemented (stage 3)" }, 501);

      // Anything else: static assets (UIs land in later stages).
      if (env.ASSETS) return env.ASSETS.fetch(request);
      return json({ error: "not found" }, 404);
    } catch (err) {
      return json({ error: "server error", detail: String((err && err.message) || err) }, 500);
    }
  },
};

// Hand the Pi a batch of unclaimed submissions + unapplied settings commands,
// marking the submissions claimed so a retry/second puller won't double-deliver.
async function syncPull(env) {
  const subs = await env.DB.prepare(
    "SELECT id, kind, r2_key, content, category, created_at FROM submissions " +
    "WHERE status='pending' ORDER BY id ASC LIMIT ?"
  ).bind(PULL_LIMIT).all();
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
