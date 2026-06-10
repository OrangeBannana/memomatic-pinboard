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
http://<pi-ip>:8080/guest/<guest-token>
```

The owner admin page can:

- Enable or disable guest uploads
- Regenerate the guest link
- Display QR codes for admin and guest links

Guest uploads are rate-limited per client address and pushed live to the top of the slideshow queue.

## API Summary

```text
GET    /admin
GET    /guest/{token}
GET    /frame
GET    /api/images
POST   /api/images
DELETE /api/images/{id}
POST   /api/images/{id}/push-next
POST   /api/images/{id}/hide
POST   /api/images/{id}/restore
POST   /api/guest/{token}/images
GET    /api/settings
PATCH  /api/settings
POST   /api/settings/guest-token
GET    /api/qr.svg
GET    /api/slideshow/current
```

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
