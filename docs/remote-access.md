# Remote access (hosted guest + owner site)

Lets guests upload photos/messages and the owner change a few basic settings **from anywhere**, via a Cloudflare-hosted site, while the Pi keeps serving `/admin`, `/guest`, and `/frame` on the LAN unchanged. Tracked in [#87](https://github.com/OrangeBannana/memomatic-pinboard/issues/87).

## Architecture

```
        guest phone / owner browser (anywhere)
                     │  HTTPS
                     ▼
        Cloudflare Worker  ──  R2 (pending upload bytes)
        (cloud/ relay)     ──  D1 (submissions, settings_commands,
                     ▲             device_state, event_codes, rate_log)
                     │  outbound-only HTTPS (Bearer DEVICE_SECRET)
                     │  poll every ~10s
        Raspberry Pi  ── pinboard-cloudsync.service (app/cloud_sync.py)
                     └─ pulls uploads/messages/settings → existing pipeline
                        pushes current settings up → owner UI
```

- The Pi **only makes outbound connections** — no inbound ports, the home network stays closed.
- The cloud is a rendezvous point; it stores pending uploads transiently (deleted once the Pi acks).
- There is **no remote library management** in V1, so the Pi never mirrors its image library — only a handful of settings round-trip.

## Pieces

| Where | What |
|---|---|
| `cloud/src/index.js` | Worker: device sync API, guest API (`/api/g/*`), owner API (`/api/o/*`) |
| `cloud/public/index.html` | Guest site: event-code gate → photo/message upload |
| `cloud/public/admin/index.html` | Owner site: basic settings + event-code management (behind Access) |
| `cloud/schema.sql` | D1 tables |
| `app/cloud_sync.py` | Pi agent (pull/apply/push/ack loop) |
| `systemd/pinboard-cloudsync.service` | runs the agent, outbound-only |

## Deploy the cloud relay (your Cloudflare account)

```bash
cd cloud
npm install
npx wrangler login
npx wrangler d1 create memomatic              # paste the database_id into wrangler.toml
npx wrangler r2 bucket create memomatic-uploads
npm run db:init                               # apply schema.sql to the remote D1
npx wrangler secret put DEVICE_SECRET         # a long random string; reuse on the Pi
npm run deploy                                # prints your https://memomatic-relay.<you>.workers.dev URL
```

### Protect the owner site with Cloudflare Access

In the Cloudflare dashboard → **Zero Trust → Access → Applications**, add a **self-hosted** app covering the owner surface:

- Application domain / paths: your Worker hostname, paths `/admin*` and `/api/o*`.
- Policy: **Allow** your email (one-time PIN) or your identity provider.

The Worker also rejects `/api/o*` requests lacking the `Cf-Access-Authenticated-User-Email` header that Access injects — defense in depth, so the owner API isn't open if the policy is missing or misconfigured.

The guest site (`/` and `/api/g/*`) stays public but is gated by the per-event code.

### Optional: custom hostname

Add a route/custom domain in the Worker settings if you'd rather hand guests `memomatic.example.com` than the `workers.dev` URL.

## Configure the Pi

Create `/etc/memomatic/cloudsync.env` (root-owned, `chmod 600`):

```ini
PINBOARD_CLOUD_URL=https://memomatic-relay.<you>.workers.dev
PINBOARD_CLOUD_SECRET=<the same value you set for DEVICE_SECRET>
PINBOARD_OWNER_TOKEN=<the Pi's owner token, same as pinboard-app>
```

Then:

```bash
sudo mkdir -p /etc/memomatic
sudo nano /etc/memomatic/cloudsync.env
sudo systemctl restart pinboard-cloudsync.service
journalctl -u pinboard-cloudsync.service -f      # watch it sync
```

`pinboard-cloudsync.service` is enabled by `install.sh`/`deploy.py` and **idles harmlessly** until that env file is present, so deploying the code before configuring the cloud is safe.

## How the sync works

Each ~10s the agent (`app/cloud_sync.py`):

1. `GET /api/sync/pull` — a batch of pending submissions + unapplied settings (the Worker marks them claimed; stale-claimed rows redeliver after 2 min, making pull at-least-once).
2. Applies them: images via `save_upload()` with guest semantics (queued **push-next**, or **pending** when *Require approval* is on); messages into the `messages` table; settings via the **local** `PATCH /api/settings` (owner token) so the Pi's own validation + side effects run.
3. `POST /api/sync/state` — pushes current basic settings up so the owner page shows live values.
4. `POST /api/sync/ack` — the Worker deletes acked submissions and their R2 objects.

Basic settings exposed remotely: `slideshow_mode`, `slideshow_order`, `slide_seconds`, `message_display_seconds`, `guest_enabled`, `guest_review_required`, `clock_enabled`.

## Security

- **Pi ↔ relay:** rotatable `DEVICE_SECRET` (bearer), constant-time compared; Pi outbound only.
- **Owner site:** Cloudflare Access (SSO/OTP) + the header guard above.
- **Guest site:** per-event code → HMAC-signed `HttpOnly; Secure; SameSite=Strict` cookie; sliding-window rate limits per code+IP (uploads/messages) and per IP (code-verify).
- **Uploads:** type/size validated at the edge **and** re-validated on the Pi (Pillow decode in `save_upload`); raw bytes are transient in R2 and deleted on ack.
- Rotate `DEVICE_SECRET` by `wrangler secret put DEVICE_SECRET` + updating `/etc/memomatic/cloudsync.env`. Rotate guest access by deactivating event codes in the owner UI.

## End-to-end test plan

After deploying and configuring:

1. **Health:** `curl https://<worker>/api/health` → `{"ok":true,...}`.
2. **Owner auth:** visit `/admin` → Access prompts for login; after auth the settings + codes load.
3. **Event code:** create a code in the owner UI; open `/` in a private window, enter the code → upload UI appears.
4. **Guest image:** upload a photo → within ~10s it appears on the frame (flashes next). Check `journalctl -u pinboard-cloudsync` shows `processed image …`.
5. **Guest meme in Photos mode:** with mode = Photos, upload a *meme* → it flashes once then photos resume (existing push-next behavior).
6. **Message:** send a message → appears on the frame for `message_display_seconds`.
7. **Remote setting:** flip a setting in the owner UI → applies on the frame within ~10s; the owner page's "last synced" updates.
8. **Review mode:** turn on *Require approval*, upload → lands as pending (Approve/Reject from the LAN `/admin`).
9. **Cleanup:** R2 bucket and `submissions` table return to empty after acks.
10. **Rate limit:** rapid repeated uploads eventually return 429.

## Troubleshooting

- **Agent idle / "PINBOARD_CLOUD_URL/SECRET not set":** the env file is missing or empty.
- **401 from the relay:** `PINBOARD_CLOUD_SECRET` ≠ Worker `DEVICE_SECRET`.
- **Settings don't apply:** `PINBOARD_OWNER_TOKEN` in the env file must match the running app's; check `journalctl -u pinboard-cloudsync` for "settings apply failed".
- **Owner API 403:** the Access application/policy on `/admin*` + `/api/o*` is missing.
- **Image rejected on Pi:** unsupported type/corrupt file — dropped (acked) with a warning so it doesn't loop.
