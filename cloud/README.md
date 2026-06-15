# Memomatic relay (Cloudflare Worker)

Serverless backend for the remote-access feature ([issue #87](https://github.com/OrangeBannana/memomatic-pinboard/issues/87)). Hosts the public guest + owner sites and a small store/queue that the Pi syncs against over **outbound-only** HTTPS, so the home network is never exposed. The Pi keeps serving `/admin`, `/guest`, `/frame` on the LAN unchanged.

> Built across staged branches off `remote-access`. **Stage 1 (this commit):** project skeleton, D1 schema, device-secret auth, and the `/api/sync/*` endpoints. Guest (`/api/g/*`) and owner (`/api/o/*`) APIs are stubbed (`501`) until stages 2–3.

## Layout

```
cloud/
  wrangler.toml      Worker config + D1/R2/Assets bindings
  schema.sql         D1 tables (submissions, settings_commands, device_state, event_codes)
  src/index.js       Worker entry: routing, auth, sync endpoints
  public/            Static assets (guest/owner UIs added later)
```

## Endpoints (stage 1)

| Route | Auth | Purpose |
|---|---|---|
| `GET /api/health` | none | liveness |
| `GET /api/sync/pull` | device secret | batch of pending submissions + unapplied settings commands (marks them claimed) |
| `POST /api/sync/ack` | device secret | `{submission_ids, settings_ids}` — delete acked submissions (+R2 objects), mark settings applied (idempotent) |
| `POST /api/sync/state` | device secret | `{settings}` — Pi pushes current settings up for the owner UI |
| `GET /api/sync/object?key=` | device secret | stream a pending upload's bytes to the Pi |
| `/api/g/*` | event code (stage 2) | guest upload/message — **stub** |
| `/api/o/*` | Cloudflare Access (stage 3) | owner settings + event codes — **stub** |

## Deploy (from your Cloudflare account)

```bash
cd cloud
npm install
npx wrangler login
npx wrangler d1 create memomatic            # paste database_id into wrangler.toml
npx wrangler r2 bucket create memomatic-uploads
npm run db:init                             # apply schema.sql
npx wrangler secret put DEVICE_SECRET       # same value goes on the Pi (PINBOARD_CLOUD_SECRET)
npm run deploy
```

Local dev: copy `.dev.vars.example` to `.dev.vars`, run `npm run db:init:local`, then `npm run dev`.

Full deploy/runbook (Access policy, custom hostname, Pi wiring) lands in `docs/remote-access.md` in stage 5.
