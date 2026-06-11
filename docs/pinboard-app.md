# Memomatic Pinboard App

Memomatic Pinboard is a local Wi-Fi digital picture frame app for a Raspberry Pi Zero 2 W driving an Inland 3.5 inch GPIO TFT through `fbcp-ili9341`.

## Runtime Layout

```text
/home/memomatic/pinboard/app/app.py
/home/memomatic/pinboard/app/static/admin.html
/home/memomatic/pinboard/app/static/frame.html
/home/memomatic/pinboard/app/static/guest.html
/home/memomatic/pinboard/data/pinboard.sqlite3
/home/memomatic/pinboard/images/originals
/home/memomatic/pinboard/images/display
```

## Services

```text
fbcp-ili9341.service     Existing TFT framebuffer mirror
pinboard-app.service     FastAPI backend on port 8080
pinboard-kiosk.service   Xorg + Chromium kiosk pointed at /frame
```

## Owner Auth

The admin page asks for the owner token and stores it in browser local storage. Admin API calls send it as:

```text
X-Pinboard-Owner-Token: <token>
```

The default token in this project is:

```text
memes
```

Change it by editing `systemd/pinboard-app.service` before install, or by installing with:

```bash
sudo PINBOARD_OWNER_TOKEN='new-token' ./install.sh
```

## Guest Uploads

Guest uploads are tokenized:

```text
http://memomatic.local:8080/guest/<guest-token>   (mDNS hostname)
http://<pi-ip>:8080/guest/<guest-token>           (raw IP fallback)
```

The owner admin page can:

- Enable or disable guest uploads
- Regenerate the guest link
- Display QR codes for admin and guest links

Guest uploads are rate-limited per client address and pushed live to the top of the slideshow queue.

## API Summary

Auth legend — **owner**: requires the `X-Pinboard-Owner-Token` header · **guest**: requires a valid guest token in the path plus `guest_enabled` (rate-limited) · **local/owner**: localhost (the on-device frame) or owner token · **public**: no auth.

```text
Pages
GET    /                                   public       redirect to /admin
GET    /admin                              public       owner web UI (token entered in page)
GET    /guest/{token}                      public       guest upload page
GET    /frame                              public       kiosk slideshow page

Images
GET    /api/images                         owner        list all images
POST   /api/images                         owner        upload (multipart file, optional category)
PATCH  /api/images/{id}                    owner        re-categorize (image/meme)
DELETE /api/images/{id}                    owner        soft-delete (status='deleted')
POST   /api/images/{id}/push-next          owner        queue to show next
POST   /api/images/{id}/hide               owner        hide (status='hidden')
POST   /api/images/{id}/restore            owner        restore to active (also approves pending)

Guest
POST   /api/guest/{token}/images           guest        upload (pending if review required)
POST   /api/guest/{token}/message          guest        post a message for the frame

Messages
GET    /api/messages                       owner        list pending + recent shown
DELETE /api/messages/{id}                  owner        delete / cancel a pending message
POST   /api/messages/{id}/shown            local/owner  frame marks a message displayed

Settings
GET    /api/settings                       owner        all settings + admin/guest URLs
PATCH  /api/settings                       owner        update settings (validated/clamped)
POST   /api/settings/guest-token           owner        rotate the guest token

Colour schemes
GET    /api/color-schemes                  public       built-in + custom schemes, active name
POST   /api/color-schemes                  owner        create/update a custom scheme
DELETE /api/color-schemes/{name}           owner        delete a custom scheme
POST   /api/color-schemes/{name}/activate  owner        set the active scheme

Slideshow & frame
GET    /api/slideshow/current              public       current image + display/clock/message state
POST   /api/frame/mode                     local/owner  set slideshow_mode (all/images/memes)
POST   /api/frame/clock                    local/owner  set clock settings

Network & misc
GET    /api/qr.svg?data=...                public       QR code SVG (text fallback without qrcode)
GET    /api/network/wifi                   owner        scan Wi-Fi networks (nmcli)
POST   /api/network/connect                local/owner  connect to a Wi-Fi network
POST   /api/network/save                   owner        save a network profile for later
GET    /api/network/ip                     public       device IP, hostname, mDNS name
```

## Messages

Guests can send short text messages (max 200 chars) from the guest page. The frame
shows each unshown message once as a toast for `message_display_seconds`, then marks
it shown. The owner can review pending and recent messages in the admin **Messages**
panel and delete a pending message before the frame displays it.

## Categories, modes, and order

Every image has a `category` (`image` or `meme`), set at upload and changeable from
the admin page. The `slideshow_mode` setting (`all` / `images` / `memes`) filters
which categories the slideshow shows; it can be changed from the admin Display
Settings panel or the frame touch menu. `slideshow_order` (`sequential` / `shuffle`)
controls advance order. Guest uploads can optionally require owner approval
(`guest_review_required`) before entering the slideshow.

## Clock

An optional clock overlay on the frame (`clock_enabled`, `clock_corner`,
`clock_size`) is configurable from the admin page or the frame touch menu.

## Network Access

After install, the Pi is accessible on the local network at:

```
http://memomatic.local:8080/admin   (mDNS — works on all modern OS)
http://<pi-ip>:8080/admin           (IP fallback)
```

`install.sh` sets the hostname to `memomatic` and starts `avahi-daemon`, which advertises `memomatic.local` via mDNS/Zeroconf. Windows 10+, macOS, and Linux with `avahi-daemon` installed resolve `.local` hostnames automatically. Android and older Windows may need the IP address instead.

The frame's on-screen menu shows `memomatic.local:8080` and the raw IP. The admin console shows both addresses below the QR codes.

---

## Troubleshooting

Check services:

```bash
systemctl status fbcp-ili9341.service --no-pager -l
systemctl status pinboard-app.service --no-pager -l
systemctl status pinboard-kiosk.service --no-pager -l
```

Check logs:

```bash
journalctl -u pinboard-app.service -n 120 --no-pager
journalctl -u pinboard-kiosk.service -n 120 --no-pager
```

If the guest page returns `500`, confirm `guest.html` exists at:

```text
/home/memomatic/pinboard/app/static/guest.html
```
