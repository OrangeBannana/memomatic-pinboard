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
  chromium-browser

install -d -o "$APP_USER" -g "$APP_USER" "$PINBOARD_HOME/app"
install -d -o "$APP_USER" -g "$APP_USER" "$PINBOARD_HOME/app/static"
install -d -o "$APP_USER" -g "$APP_USER" "$PINBOARD_HOME/data"
install -d -o "$APP_USER" -g "$APP_USER" "$PINBOARD_HOME/images/originals"
install -d -o "$APP_USER" -g "$APP_USER" "$PINBOARD_HOME/images/display"
install -d -o "$APP_USER" -g "$APP_USER" "$PINBOARD_HOME/chromium-profile"

install -m 0644 -o "$APP_USER" -g "$APP_USER" app/app.py "$PINBOARD_HOME/app/app.py"
install -m 0755 -o "$APP_USER" -g "$APP_USER" app/kiosk.sh "$PINBOARD_HOME/app/kiosk.sh"
install -m 0644 -o "$APP_USER" -g "$APP_USER" app/static/admin.html "$PINBOARD_HOME/app/static/admin.html"
install -m 0644 -o "$APP_USER" -g "$APP_USER" app/static/frame.html "$PINBOARD_HOME/app/static/frame.html"
install -m 0644 -o "$APP_USER" -g "$APP_USER" app/static/guest.html "$PINBOARD_HOME/app/static/guest.html"

sed "s/^Environment=PINBOARD_OWNER_TOKEN=.*/Environment=PINBOARD_OWNER_TOKEN=$OWNER_TOKEN/" \
  systemd/pinboard-app.service > /etc/systemd/system/pinboard-app.service
install -m 0644 systemd/pinboard-kiosk.service /etc/systemd/system/pinboard-kiosk.service

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

systemctl daemon-reload
systemctl enable pinboard-app.service
systemctl enable pinboard-kiosk.service
systemctl restart pinboard-app.service
systemctl restart pinboard-kiosk.service

echo "Memomatic Pinboard installed."
echo "Admin: http://<pi-ip>:8080/admin"
echo "Owner token: $OWNER_TOKEN"
