# Feasibility: Microsoft Teams integration for automatic image ingestion

**Issue:** #30
**Status:** Investigation complete — see [Recommendation](#recommendation).
**Verdict:** **Proceed with caveats** — implement the push-based path (Power Automate / webhook) only. Do **not** store Microsoft Graph OAuth refresh tokens on the Pi.

---

## 1. Question

Can Memomatic Pinboard pull images shared in a Microsoft Teams group chat onto
the frame automatically, without exposing user credentials or creating an
ongoing security risk on a low-security home device?

## 2. Threat model for this device

Any design has to be judged against what the Pi actually is, not an idealized
server:

- **Default, shared credentials.** `install.sh` sets user `memomatic` / password
  `memes`, SSH is enabled, and the device lives on a shared home/office Wi-Fi.
  Anyone on the LAN who knows the documented default can log in.
- **No secret storage.** There is no TPM, no OS keyring in use, no disk
  encryption. The SQLite DB and the whole filesystem are readable by anyone with
  shell access or physical possession of the SD card.
- **Physical exposure.** It is a picture frame on a shelf. The SD card can be
  pulled and read on any laptop in under a minute.
- **Long uptime, rare patching.** It is meant to run untouched for months.

**Conclusion:** the Pi cannot be trusted to hold a long-lived, high-value
secret. A Microsoft Graph **refresh token is exactly that** — it is
password-equivalent and, depending on scope, grants read access to *all* of a
user's Teams chats. Storing one on this device is the central risk, and no
amount of at-rest "encryption" on the same disk meaningfully mitigates it
(the key has to live next to the ciphertext).

## 3. Approaches evaluated

### 3.1 OAuth delegated token (Graph API, polling)

The app registers as an Azure AD application, the user signs in via browser, the
app stores a **refresh token** and polls `GET /chats/{id}/messages` for
attachments.

**Graph scopes required (delegated):**

| Scope | Grants |
|---|---|
| `Chat.Read` | Read **all** 1:1 and group chat messages the signed-in user is in |
| `ChatMessage.Read` | Read messages in chats the user is in (message-level) |
| `Chat.ReadBasic` | Chat metadata only — **cannot** read message bodies/attachments, so insufficient here |
| `User.Read` | Basic sign-in / profile (needed for the sign-in itself) |
| `offline_access` | Required to obtain the **refresh token** for background polling |

There is **no per-chat delegated scope.** `Chat.Read` is the narrowest scope
that can read message content, and it is tenant-wide across the user's chats —
you cannot consent to "just this one group chat." This is scope creep baked into
the API.

**Risks:**
- Refresh token (via `offline_access`) is a long-lived, password-equivalent
  secret stored on a device that cannot protect it (§2).
- If the SD card / token leaks, an attacker can read **every** chat the user
  belongs to until the token is manually revoked.
- "Encrypted at rest in SQLite" is illusory here — the decryption key must also
  live on the Pi, so anyone with the disk has both.

**Verdict: unacceptable for this device.**

### 3.2 Application (service) permission

Register the app with the **application** permission `ChatMessage.Read.All`; no
user sign-in, runs unattended.

**Risk:** requires **tenant admin consent** and grants read access to
*all chats in the entire tenant*, not one group. This is wildly disproportionate
for a personal picture frame and will (rightly) never be approved by any
competent tenant admin. Also stores an app secret/cert on the Pi.

**Verdict: do not pursue.**

### 3.3 Graph change notifications (webhook / subscription)

Create a Graph **subscription** so Microsoft pushes a notification when a new
chat message arrives, instead of polling.

- Still requires a **registered OAuth app and the same `Chat.Read`-class
  scopes** — it does not reduce the credential footprint at all.
- Requires a **public HTTPS endpoint** that Microsoft can reach to deliver
  notifications and answer the validation handshake. The Pi is behind home NAT,
  so this needs a tunnel (Cloudflare Tunnel, ngrok, etc.) — another always-on
  inbound path into a low-security device.
- Subscriptions for chat messages also require resource data encryption keys to
  be managed.

**Verdict: worst of both worlds** — same credential risk as 3.1 *plus* an
inbound public endpoint. Do not pursue.

### 3.4 External push (Power Automate / Zapier) — **recommended**

The user builds a **Power Automate** flow (or Zapier zap) in their own Microsoft
account: trigger *"When a new message is posted in a chat/channel"* → *"if it
has an image attachment"* → **HTTP POST the image to the pinboard's existing
guest upload endpoint.**

- **No Microsoft credential is ever stored on the Pi.** The OAuth connection
  lives inside Power Automate (Microsoft's own infrastructure), governed by the
  user's normal account controls and revocable from the Microsoft 365 admin/My
  Account page.
- **Uses the existing API as-is.** Power Automate calls
  `POST /api/guest/{token}/images` (multipart `file`, optional
  `category=meme`) — the same endpoint guests already use. No new server code,
  no new attack surface on the device.
- **The pinboard's existing controls apply automatically:** the guest token can
  be disabled or rotated (`POST /api/settings/guest-token`), and the per-token
  rate limit (`GUEST_UPLOAD_LIMIT` / `GUEST_UPLOAD_WINDOW_SECONDS`) throttles a
  runaway flow.
- **Revocation is trivial and local:** turn off guest uploads or rotate the
  token in `/admin`, and the integration stops instantly — no dependency on
  reaching Microsoft to revoke anything on the device.

**Caveats / requirements:**
- Needs **inbound reachability** from Power Automate to the pinboard. On a home
  LAN that means either (a) running the flow on a machine on the same network,
  or (b) exposing the guest endpoint through a tunnel — which re-introduces an
  inbound path and should be gated behind the guest token + HTTPS. The
  least-risk option keeps it LAN-only.
- Premium Power Automate connectors may be required for some triggers (a
  licensing, not security, concern).
- The image hits the slideshow via the normal guest path, so it auto-queues as
  push-next like any guest upload.

## 4. Cross-cutting security concerns (from the issue)

1. **Token storage** — addressed by *not storing one*: the push approach keeps
   all OAuth material inside Microsoft's infrastructure. The only secret on the
   Pi is the guest token, which is already low-value, rotatable, and disableable.
2. **Scope creep** — `Chat.Read` is tenant-wide across the user's chats with no
   per-chat narrowing; the push approach sidesteps this because the *user's*
   flow decides which single chat to forward from.
3. **Revocation** — push approach: rotate/disable the guest token in `/admin`
   (instant, local, no Microsoft round-trip). Direct OAuth: must reach Azure AD
   to revoke, and the user must *know* to do it if the Pi is lost.
4. **Guest / external members** — Teams channels often include external guests.
   Auto-ingesting their images should **not** bypass moderation. The push path
   already flows through the guest pipeline; if content review is desired, see
   the optional `pending`-status enhancement below.
5. **Content moderation** — automatic ingestion can put unwanted content on the
   frame before the owner sees it. Today the owner can hide/delete after the
   fact. If pre-moderation is wanted, add an opt-in `pending` status (below).

## 5. Optional hardening (only if the push path is adopted)

These are **not required** for the recommendation but reduce moderation risk:

- **Pending-review queue.** Add an opt-in setting so externally-ingested uploads
  land with `status='pending'` instead of `active`, surfaced in `/admin` with
  Approve/Reject. Reuses the existing status machinery (`active`/`hidden`/
  `deleted` → add `pending`); only `active` enters the slideshow already, so the
  slideshow code needs no change.
- **Dedicated ingest token.** Issue a separate token for the push integration
  (distinct from the human guest token) so it can be rate-limited and revoked
  independently. Could reuse the `guest_uploads` table keyed by token.
- **Source tagging.** Set `source='teams-push'` (the upload pipeline already
  records `source`) so admin can filter/audit ingested images.

## 6. Recommendation

**Proceed with caveats: implement the push-based path (§3.4) only.**

- ✅ **Do**: document a Power Automate / Zapier recipe that POSTs Teams image
  attachments to `POST /api/guest/{token}/images`. Optionally add the
  `pending`-review queue (§5) for pre-moderation. No Microsoft credential touches
  the Pi. → Done: see [teams-power-automate-recipe.md](teams-power-automate-recipe.md);
  the pending-review queue shipped as the `guest_review_required` setting (#50).
- ❌ **Do not**: implement direct OAuth (delegated *or* application) on the
  device, store Graph refresh tokens in SQLite, or stand up a Graph
  change-notification webhook. The required `Chat.Read`-class scopes are
  tenant-wide-per-user with no per-chat narrowing, and a refresh token is a
  password-equivalent secret this device demonstrably cannot protect (§2).

Rationale: the push approach satisfies the user's goal (Teams images appear on
the frame automatically) while keeping every high-value secret off the
low-security device, reusing the existing guest pipeline and its rotate/disable/
rate-limit controls, and making revocation a one-tap local action.

## 7. Acceptance criteria status

- [x] Document which Graph API scopes are required for each approach — §3
- [x] Assess token storage risk given the Pi's threat model — §2, §4.1
- [x] Evaluate the Power Automate / webhook push alternative as a lower-risk path — §3.4, §3.3
- [x] Produce a written recommendation with rationale — §6
