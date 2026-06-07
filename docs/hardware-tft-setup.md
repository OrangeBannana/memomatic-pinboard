# Raspberry Pi Zero 2 W + Inland 3.5 Inch TFT Touchscreen Setup

This note documents the working setup for an Inland 3.5 inch TFT LCD touchscreen on a Raspberry Pi Zero 2 W.

Hardware:

- Raspberry Pi Zero 2 W
- Inland 3.5 inch TFT LCD touchscreen monitor from Micro Center
- 40-pin GPIO header soldered to the Pi Zero 2 W
- 5V Raspberry Pi power supply

Display software used:

- Raspberry Pi OS 32-bit Bookworm
- `fbcp-ili9341`
- `systemd` service to start display mirroring at boot

Important project links:

- Inland display product page: https://www.microcenter.com/product/632693/inland-35-inch-tft-lcd-touch-screen-monitor
- `fbcp-ili9341`: https://github.com/juj/fbcp-ili9341
- GoodTFT LCD-show, tried first but not used as the final display path: https://github.com/goodtft/LCD-show

## Summary

The Inland 3.5 inch TFT is a GPIO/SPI display, not an HDMI display. It attaches directly to the Raspberry Pi 40-pin GPIO header. The screen backlight may turn on even when the display controller is not initialized, which appears as a blank white screen.

The working approach for this setup was to use `fbcp-ili9341` to mirror the Pi's framebuffer to the SPI TFT using the Waveshare 3.5B ILI9486 profile.

The final working build uses:

```bash
cmake \
  -DSPI_BUS_CLOCK_DIVISOR=30 \
  -DWAVESHARE35B_ILI9486=ON \
  -DSTATISTICS=0 \
  -DUSE_DMA_TRANSFERS=OFF \
  -DDISPLAY_ROTATE_180_DEGREES=ON \
  ..
```

The final running binary path is:

```bash
/home/memomatic/fbcp-ili9341/build-safe-rot180/fbcp-ili9341
```

## Physical Connection

1. Power off the Pi completely.
2. Make sure the Raspberry Pi Zero 2 W has a soldered 40-pin male GPIO header.
3. Seat the Inland TFT directly onto the GPIO header.
4. Confirm the display is not offset by one pin or one row.
5. Power the Pi through its normal USB power input.

A white screen usually means the display has power, but the LCD controller is not receiving a valid initialization/data stream.

## OS Choice

The first recommendation was Raspberry Pi OS Legacy 32-bit because older GPIO TFT drivers are often more reliable there.

The final working system was:

```text
Raspbian GNU/Linux 12 (bookworm)
32-bit
```

Verified with:

```bash
getconf LONG_BIT
cat /etc/os-release
```

Expected relevant output:

```text
32
PRETTY_NAME="Raspbian GNU/Linux 12 (bookworm)"
```

## Boot Config

On Bookworm, edit:

```bash
sudo nano /boot/firmware/config.txt
```

The old `LCD-show` overlay was disabled because it conflicted with `fbcp-ili9341` taking direct control of the SPI display:

```text
#dtoverlay=tft35a:rotate=90
```

SPI remained enabled:

```text
dtparam=spi=on
```

HDMI framebuffer settings were set to the TFT resolution:

```text
hdmi_force_hotplug=1
hdmi_group=2
hdmi_mode=87
hdmi_cvt=480 320 60 1 0 0 0
```

Note: On older Raspberry Pi OS versions, the path may be `/boot/config.txt` instead of `/boot/firmware/config.txt`.

## Install Dependencies

```bash
sudo apt update
sudo apt install -y git cmake build-essential
```

## Build fbcp-ili9341

Clone the repo:

```bash
cd ~
git clone https://github.com/juj/fbcp-ili9341.git
cd fbcp-ili9341
```

Create the final rotated build:

```bash
mkdir -p build-safe-rot180
cd build-safe-rot180
cmake \
  -DSPI_BUS_CLOCK_DIVISOR=30 \
  -DWAVESHARE35B_ILI9486=ON \
  -DSTATISTICS=0 \
  -DUSE_DMA_TRANSFERS=OFF \
  -DDISPLAY_ROTATE_180_DEGREES=ON \
  ..
make -j
```

Run manually:

```bash
sudo /home/memomatic/fbcp-ili9341/build-safe-rot180/fbcp-ili9341
```

Expected output includes:

```text
Targeting WaveShare 3.5 inch (B) display with ILI9486
Rotating display output by 180 degrees
All initialized, now running main loop...
```

## systemd Service

The final service starts the rotated display driver automatically at boot.

Create:

```bash
sudo nano /etc/systemd/system/fbcp-ili9341.service
```

Service contents:

```ini
[Unit]
Description=SPI TFT display mirror via fbcp-ili9341
After=multi-user.target

[Service]
Type=simple
ExecStart=/home/memomatic/fbcp-ili9341/build-safe-rot180/fbcp-ili9341
WorkingDirectory=/home/memomatic/fbcp-ili9341/build-safe-rot180
User=root
Restart=always
RestartSec=2
KillSignal=SIGKILL
TimeoutStopSec=2

[Install]
WantedBy=multi-user.target
```

The `KillSignal=SIGKILL` and `TimeoutStopSec=2` lines are intentional. During testing, the default systemd stop behavior left `fbcp-ili9341` stuck during reboot. This service definition prevents the display driver from hanging shutdown or reboot.

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable fbcp-ili9341.service
sudo systemctl start fbcp-ili9341.service
```

Check status:

```bash
systemctl status fbcp-ili9341.service --no-pager -l
systemctl is-active fbcp-ili9341.service
systemctl is-enabled fbcp-ili9341.service
pgrep -af fbcp-ili9341
```

Expected final state:

```text
active
enabled
/home/memomatic/fbcp-ili9341/build-safe-rot180/fbcp-ili9341
```

## Reboot Test

Reboot:

```bash
sudo reboot
```

After the Pi comes back, verify:

```bash
systemctl is-active fbcp-ili9341.service
systemctl is-enabled fbcp-ili9341.service
pgrep -af fbcp-ili9341
```

Final verified result:

```text
fbcp-ili9341.service: active
enabled: yes
process: /home/memomatic/fbcp-ili9341/build-safe-rot180/fbcp-ili9341
```

## What Did Not Work

### LCD-show alone

The GoodTFT/LCD-show `LCD35-show` setup created `/dev/fb1` and detected the touchscreen, but the TFT stayed white.

Observed:

```text
/dev/fb0
/dev/fb1
dtoverlay=tft35a:rotate=90
ADS7846 Touchscreen detected
fb_ili9486 frame buffer detected
```

That meant the kernel had created a framebuffer for the TFT, but the display was not actually showing the image correctly.

### Generic ILI9486 fbcp profile

The generic `ILI9486` profile caused the display to go white again after it had briefly been working. Avoid this profile for this specific setup.

Do not use:

```bash
-DILI9486=ON
```

Use the Waveshare profile instead:

```bash
-DWAVESHARE35B_ILI9486=ON
```

### Too-fast SPI / DMA

The first `fbcp-ili9341` test used:

```bash
-DSPI_BUS_CLOCK_DIVISOR=12
-DUSE_DMA_TRANSFERS=ON
```

The display flickered but returned to white. The stable configuration used a slower SPI clock and disabled DMA:

```bash
-DSPI_BUS_CLOCK_DIVISOR=30
-DUSE_DMA_TRANSFERS=OFF
```

## Troubleshooting

### Display is solid white

Likely causes:

- Display has power but no valid SPI initialization
- Driver profile mismatch
- Old `tft35a` overlay still enabled
- Multiple `fbcp-ili9341` processes fighting over the display
- GPIO header seating or soldering issue
- LCD controller latched into a bad state

Useful checks:

```bash
ls -l /dev/fb* /dev/spidev* 2>/dev/null
pgrep -af fbcp-ili9341
grep -nE "dtoverlay|dtparam|hdmi_|spi|tft|ili|xpt|ads" /boot/firmware/config.txt
systemctl status fbcp-ili9341.service --no-pager -l
```

If the display remains white after a bad driver test, do a full power removal:

```bash
sudo poweroff
```

Then unplug power for 15-20 seconds and plug it back in.

### Reboot hangs

If reboot hangs while stopping `fbcp-ili9341`, make sure the service includes:

```ini
KillSignal=SIGKILL
TimeoutStopSec=2
```

Then reload systemd:

```bash
sudo systemctl daemon-reload
```

### Multiple fbcp processes

Only one copy should be running.

Check:

```bash
pgrep -af fbcp-ili9341
```

Stop extras:

```bash
sudo pkill -x fbcp-ili9341
sudo systemctl restart fbcp-ili9341.service
```

## Touchscreen Notes

The touchscreen was detected by the earlier `LCD-show` path as an ADS7846-compatible resistive touchscreen:

```text
ADS7846 Touchscreen
```

The final work here focused on getting display output stable through `fbcp-ili9341`. Touch may require separate calibration and X/input configuration depending on the final UI stack.

Useful future checks:

```bash
cat /proc/bus/input/devices
ls -l /dev/input/event*
xinput list
```

If touch is needed in a desktop/X11 UI, install and use calibration tools appropriate for ADS7846/XPT2046-style resistive touch.

## GitHub Repo Entry

Suggested repository name:

```text
rpi-zero-2w-inland-35-tft
```

Suggested description:

```text
Working Raspberry Pi Zero 2 W setup for the Inland 3.5 inch GPIO TFT touchscreen using fbcp-ili9341, Waveshare35B ILI9486 settings, 180-degree rotation, and a systemd boot service.
```

Suggested README intro:

```markdown
# Raspberry Pi Zero 2 W + Inland 3.5 Inch TFT

This repo documents a working setup for the Micro Center Inland 3.5 inch GPIO TFT touchscreen on a Raspberry Pi Zero 2 W. The final display path uses `fbcp-ili9341` with the Waveshare 3.5B ILI9486 profile, conservative SPI settings, 180-degree rotation, and a `systemd` service for boot startup.

The important working flags are:

    cmake \
      -DSPI_BUS_CLOCK_DIVISOR=30 \
      -DWAVESHARE35B_ILI9486=ON \
      -DSTATISTICS=0 \
      -DUSE_DMA_TRANSFERS=OFF \
      -DDISPLAY_ROTATE_180_DEGREES=ON \
      ..

Avoid the generic `ILI9486` profile for this hardware; it caused a white-screen state during testing.
```

Suggested repo files:

```text
README.md
systemd/fbcp-ili9341.service
docs/troubleshooting.md
```

Suggested `.gitignore`:

```gitignore
build/
build-*/
*.o
*.log
```
