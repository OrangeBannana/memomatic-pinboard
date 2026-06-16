#!/bin/sh
set -eu

APP_USER="${APP_USER:-memomatic}"
PINBOARD_HOME="${PINBOARD_HOME:-/home/$APP_USER/pinboard}"
OWNER_TOKEN="${PINBOARD_OWNER_TOKEN:-memes}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo: sudo PINBOARD_OWNER_TOKEN=your-token ./install.sh" >&2
  exit 1
fi

if ! id "$APP_USER" >/dev/null 2>&1; then
  echo "User $APP_USER does not exist." >&2
  exit 1
fi

apt update
apt install -y \
  python3-fastapi \
  python3-uvicorn \
  python3-multipart \
  python3-pil \
  python3-qrcode \
  xserver-xorg-video-fbdev \
  chromium-browser \
  xdotool \
  avahi-daemon \
  libnss-mdns \
  build-essential

install -d -o "$APP_USER" -g "$APP_USER" "$PINBOARD_HOME/app"
install -d -o "$APP_USER" -g "$APP_USER" "$PINBOARD_HOME/app/static"
install -d -o "$APP_USER" -g "$APP_USER" "$PINBOARD_HOME/data"
install -d -o "$APP_USER" -g "$APP_USER" "$PINBOARD_HOME/images/originals"
install -d -o "$APP_USER" -g "$APP_USER" "$PINBOARD_HOME/images/display"
install -d -o "$APP_USER" -g "$APP_USER" "$PINBOARD_HOME/chromium-profile"

install -m 0644 -o "$APP_USER" -g "$APP_USER" app/app.py "$PINBOARD_HOME/app/app.py"
install -m 0755 -o "$APP_USER" -g "$APP_USER" app/kiosk.sh "$PINBOARD_HOME/app/kiosk.sh"
install -m 0755 -o root -g root app/touch_bridge.py "$PINBOARD_HOME/app/touch_bridge.py"
install -m 0755 -o "$APP_USER" -g "$APP_USER" app/cloud_sync.py "$PINBOARD_HOME/app/cloud_sync.py"
# Source for the SPI coordinate helper; pinboard-touch.service compiles it on
# the Pi at every service start (requires gcc from build-essential above).
install -m 0644 -o root -g root app/spi_touch_read.c "$PINBOARD_HOME/app/spi_touch_read.c"
install -m 0755 -o root -g root app/touch_test.py "$PINBOARD_HOME/app/touch_test.py"
install -m 0755 -o root -g root app/touch_diag.sh "$PINBOARD_HOME/app/touch_diag.sh"
install -m 0755 -o root -g root app/raw_touch.py "$PINBOARD_HOME/app/raw_touch.py"
install -m 0755 -o root -g root app/show_splash.py "$PINBOARD_HOME/app/show_splash.py"
install -m 0644 -o root -g root app/boot_splash.png "$PINBOARD_HOME/app/boot_splash.png"
install -m 0644 -o "$APP_USER" -g "$APP_USER" app/static/admin.html "$PINBOARD_HOME/app/static/admin.html"
install -m 0644 -o "$APP_USER" -g "$APP_USER" app/static/frame.html "$PINBOARD_HOME/app/static/frame.html"
install -m 0644 -o "$APP_USER" -g "$APP_USER" app/static/guest.html "$PINBOARD_HOME/app/static/guest.html"

sed "s/^Environment=PINBOARD_OWNER_TOKEN=.*/Environment=PINBOARD_OWNER_TOKEN=$OWNER_TOKEN/" \
  systemd/pinboard-app.service > /etc/systemd/system/pinboard-app.service
install -m 0644 systemd/pinboard-kiosk.service /etc/systemd/system/pinboard-kiosk.service
install -m 0644 systemd/pinboard-touch.service /etc/systemd/system/pinboard-touch.service
install -m 0644 systemd/pinboard-splash.service /etc/systemd/system/pinboard-splash.service
install -m 0644 systemd/pinboard-cloudsync.service /etc/systemd/system/pinboard-cloudsync.service

install -d /etc/X11/xorg.conf.d
cat >/etc/X11/Xwrapper.config <<'EOF'
allowed_users=anybody
needs_root_rights=yes
EOF

cat >/etc/X11/xorg.conf.d/99-pinboard-fbdev.conf <<'EOF'
Section "Device"
    Identifier "PinboardFramebuffer"
    Driver "fbdev"
    Option "fbdev" "/dev/fb0"
EndSection

Section "Screen"
    Identifier "PinboardScreen"
    Device "PinboardFramebuffer"
EndSection
EOF

cat >/etc/X11/xorg.conf.d/99-calibration.conf <<'EOF'
Section "InputClass"
        Identifier      "calibration"
        MatchProduct    "ADS7846 Touchscreen"
        MatchDriver     "evdev"
        Option  "Calibration"   "1839 263 212 1857"
        Option  "SwapAxes"      "1"
        Option  "GrabDevice"    "off"
EndSection
EOF

hostnamectl set-hostname memomatic

# ── Boot-time optimisations ──────────────────────────────────────────────────
# Raspberry Pi OS Bookworm uses /boot/firmware/config.txt; older releases use
# /boot/config.txt. Apply to whichever exists.
for BOOTCFG in /boot/firmware/config.txt /boot/config.txt; do
  [ -f "$BOOTCFG" ] || continue

  # Disable rainbow splash (the multicolour square shown before the Linux kernel)
  grep -q "^disable_splash" "$BOOTCFG" || echo "disable_splash=1" >> "$BOOTCFG"

  # Disable on-board Bluetooth — frees up UART and eliminates hciuart.service delay
  grep -q "^dtoverlay=disable-bt" "$BOOTCFG" || echo "dtoverlay=disable-bt" >> "$BOOTCFG"

  # GPU memory: keep the firmware default of 64 MB. The boot-time work (#4)
  # set gpu_mem=16, but the fbcp-ili9341 "safe build" mirrors the framebuffer
  # through the GPU, and starving it changes the SPI frame cadence that
  # spi_touch_read.c busy-waits on to read the ADS7846 in the ~2 ms
  # inter-frame gap — prime suspect for the touchscreen regression (#25).
  if grep -q "^gpu_mem=16$" "$BOOTCFG"; then
    # Remediate devices provisioned while #4's gpu_mem=16 was in place.
    sed -i 's/^gpu_mem=16$/gpu_mem=64/' "$BOOTCFG"
  elif ! grep -q "^gpu_mem" "$BOOTCFG"; then
    echo "gpu_mem=64" >> "$BOOTCFG"
  fi
done

# Mask the Bluetooth modem service so hciuart.service doesn't add a boot delay
systemctl mask bluetooth.service hciuart.service 2>/dev/null || true

systemctl daemon-reload
systemctl enable avahi-daemon.service
systemctl restart avahi-daemon.service
systemctl enable pinboard-splash.service
systemctl enable pinboard-app.service
systemctl enable pinboard-kiosk.service
systemctl enable pinboard-touch.service
# Remote-access sync agent (issue #87). Idles unless /etc/memomatic/cloudsync.env
# provides PINBOARD_CLOUD_URL/SECRET, so it's safe to enable by default.
systemctl enable pinboard-cloudsync.service
systemctl restart pinboard-app.service
systemctl restart pinboard-kiosk.service
systemctl restart pinboard-touch.service
systemctl restart pinboard-cloudsync.service

echo "Memomatic Pinboard installed."
echo "Admin: http://memomatic.local:8080/admin"
echo "       http://<pi-ip>:8080/admin  (IP fallback)"
echo "Owner token: $OWNER_TOKEN"
