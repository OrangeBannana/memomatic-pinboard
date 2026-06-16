-- Memomatic relay D1 schema (remote access feature, issue #87).
-- Apply with:  npm run db:init   (or db:init:local for `wrangler dev`)

-- Guest submissions awaiting the Pi's pull. Images keep their bytes in R2
-- (r2_key); messages keep inline text (content). Rows are deleted on ack.
CREATE TABLE IF NOT EXISTS submissions (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  kind       TEXT NOT NULL CHECK (kind IN ('image','message')),
  r2_key     TEXT,
  content    TEXT,
  category   TEXT NOT NULL DEFAULT 'image' CHECK (category IN ('image','meme')),
  status     TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','claimed')),
  created_at INTEGER NOT NULL,
  claimed_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_submissions_pending ON submissions(status, id);

-- Basic-settings changes queued by the owner UI for the Pi to apply.
CREATE TABLE IF NOT EXISTS settings_commands (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  key        TEXT NOT NULL,
  value      TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  applied_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_settings_unapplied ON settings_commands(applied_at);

-- Single row: the Pi pushes its current settings here so the owner UI can show
-- live values without touching the Pi directly.
CREATE TABLE IF NOT EXISTS device_state (
  id            INTEGER PRIMARY KEY CHECK (id = 1),
  settings_json TEXT NOT NULL DEFAULT '{}',
  last_seen_at  INTEGER
);
INSERT OR IGNORE INTO device_state (id, settings_json, last_seen_at) VALUES (1, '{}', NULL);

-- Short per-event guest codes. Guests enter one to unlock the upload page.
CREATE TABLE IF NOT EXISTS event_codes (
  code       TEXT PRIMARY KEY,
  label      TEXT,
  created_at INTEGER NOT NULL,
  expires_at INTEGER,
  active     INTEGER NOT NULL DEFAULT 1
);

-- Sliding-window rate-limit log (per code+IP for uploads/messages, per IP for
-- code-verify attempts). Pruned opportunistically on each check.
CREATE TABLE IF NOT EXISTS rate_log (
  id    INTEGER PRIMARY KEY AUTOINCREMENT,
  scope TEXT NOT NULL,
  at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rate_scope ON rate_log(scope, at);
