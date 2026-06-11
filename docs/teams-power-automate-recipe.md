# Recipe: Teams images → Memomatic via Power Automate

This is the implementation recipe for the approach recommended in
[teams-integration-feasibility.md](teams-integration-feasibility.md) (§3.4 / §6):
forward images posted in a Microsoft Teams chat to the pinboard's **existing guest
upload endpoint** using a Power Automate flow that runs in *your* Microsoft
account. **No Microsoft credential ever touches the Pi.**

## What you need

- A Microsoft 365 account that can create Power Automate flows for the target
  Teams chat/channel. (The Teams triggers may require a premium connector
  depending on your licence — that is a licensing concern, not a security one.)
- The pinboard's guest link enabled: in `/admin` → **Links**, tick *Guest uploads
  enabled* and copy the guest URL. The token is the last path segment:
  `http://memomatic.local:8080/guest/<TOKEN>`.
- Network reachability from wherever the flow's HTTP action runs to the Pi
  (see [Reachability](#reachability) — this is the main constraint).

## The endpoint contract

The flow ultimately performs one HTTP request per image:

```text
POST http://memomatic.local:8080/api/guest/<TOKEN>/images
Content-Type: multipart/form-data

  file      = <the image binary>         (required; JPEG, PNG, or WebP)
  category  = image | meme               (optional; default image)
```

Equivalent curl, useful for testing the path before building the flow:

```bash
curl -X POST "http://memomatic.local:8080/api/guest/<TOKEN>/images" \
  -F "file=@photo.jpg" -F "category=meme"
```

Responses:

| Status | Meaning |
|---|---|
| 200 | Stored. Body includes `image.status`: `active` (queued to show next) or `pending` (awaiting owner approval, see below) |
| 400 | Not a readable JPEG/PNG/WebP, or too large |
| 404 | Guest uploads disabled or token rotated — the integration is revoked |
| 429 | Rate limit hit (5 uploads per 10 min per token+source address) |

## Flow outline (Power Automate)

1. **Trigger** — *"When a new channel message is added"* (Teams connector) for the
   chosen team/channel, or *"When a new chat message is added"* for a group chat.
2. **Condition** — only continue when the message has at least one attachment
   whose name ends in `.jpg` / `.jpeg` / `.png` / `.webp` (check
   `triggerOutputs()?['body/attachments']`).
3. **Get the attachment content** — Teams message attachments are stored in
   SharePoint/OneDrive; use *"Get file content using path"* (SharePoint/OneDrive
   connector) with the attachment's `contentUrl`.
4. **HTTP action** — `POST` to the guest endpoint above with
   `multipart/form-data`. In the HTTP action's *Body*, supply a multipart body
   with one part named `file` containing the file content from step 3 (set
   `$content-type` to the image MIME type), and optionally a `category` part.
5. (Optional) **Notification** — post a reply or send yourself a message on 429
   so a burst of images that exceeds the rate limit isn't silently dropped.

Zapier equivalent: *New Channel Message in Microsoft Teams* → filter for image
attachments → *Webhooks by Zapier* POST (multipart) to the same URL.

## Reachability

The Pi is LAN-only by design. The flow's HTTP action must be able to reach it:

- **Least risk (recommended):** run the HTTP step on the same network as the Pi —
  Power Automate *desktop* flows on a machine on the LAN, or a tiny relay script
  on a home server that the cloud flow calls indirectly.
- **On-premises data gateway:** Power Automate's gateway lets cloud flows reach
  LAN resources without exposing the Pi to the internet.
- **Tunnel (Cloudflare Tunnel etc.):** exposes the guest endpoint publicly.
  Only do this with HTTPS, keep the rest of the app unexposed, and accept that
  the guest token becomes the only gate. Rotate it immediately if leaked.

Do **not** port-forward the Pi directly to the internet.

## Moderation and revocation

- **Pre-moderation:** enable *Require approval before showing* in `/admin` →
  **Links** (`guest_review_required`). Ingested images then land as `pending`
  and only appear on the frame after you press **Approve** on the image card.
  Recommended for channels with external guests.
- **Revocation is local and instant:** untick *Guest uploads enabled* or press
  *New Guest Link* in `/admin`. The flow starts getting 404s and nothing more
  arrives. No Microsoft-side cleanup is required (you may also disable the flow).
- The per-token rate limit (5 images / 10 min) throttles a runaway flow
  automatically.

## Do nots (from the feasibility review)

- Do **not** register an OAuth app and store a Microsoft Graph refresh token on
  the Pi — `Chat.Read`-class scopes are tenant-wide per user and the token is a
  password-equivalent secret this device cannot protect.
- Do **not** request application permissions (`ChatMessage.Read.All`) — tenant-wide
  read access for a picture frame.
- Do **not** stand up a Graph change-notification webhook on the Pi — same
  credential risk plus a public inbound endpoint.
