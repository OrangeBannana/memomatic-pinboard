# Memomatic Pinboard

Local digital picture frame and joke pinboard app for a Raspberry Pi Zero 2 W with an Inland 3.5 inch GPIO TFT.

The app runs a FastAPI backend and a fullscreen Chromium kiosk. Images can be uploaded from the owner admin page or from a tokenized guest link. The frame displays a continuous slideshow and loops back to the start instead of running out.

## Features

- Owner upload/admin page at `http://<pi-ip>:8080/admin`
- Owner-token protection for admin/API actions
- Guest upload link with enable/disable and regeneration
- QR codes for admin and guest URLs
- JPEG, PNG, and WebP uploads
- Local filesystem image storage with SQLite metadata
- Continuous slideshow loop
- Push-next, hide, restore, and delete image controls
- Configurable slide duration, background blur, and background brightness
- Fullscreen kiosk display at `http://127.0.0.1:8080/frame`

## Hardware Target

- Raspberry Pi Zero 2 W
- Inland 3.5 inch GPIO TFT touchscreen
- `fbcp-ili9341` mirroring the Pi framebuffer to the TFT

See [docs/hardware-tft-setup.md](docs/hardware-tft-setup.md) for the display setup notes.

## Install

Copy this repo to the Pi, then run:

```bash
sudo PINBOARD_OWNER_TOKEN='your-token' ./install.sh
```

For the current Memomatic build, the owner token is:

```text
memes
```

## Use

Open the admin page from another device on the same Wi-Fi:

```text
http://<pi-ip>:8080/admin
```

Enter the owner token to unlock. From there you can upload images, manage the image list, change display settings, and enable/share the guest upload link.

## More Docs

- [Pinboard app notes](docs/pinboard-app.md)
- [TFT hardware setup](docs/hardware-tft-setup.md)
- [Maintainer standards](docs/maintainer-standards.md)
