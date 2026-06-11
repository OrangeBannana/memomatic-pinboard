# Bluetooth guest uploads (#1) — design, status, hardware test plan

**Branch:** `issue-1-bluetooth-guest-uploads`
**Status:** implemented and fully exercised locally with stubs — **needs on-device verification before merging.** Nothing here has touched a real Bluetooth adapter yet.

## What guests get

1. Owner opens the frame's touch menu → **Bluetooth pairing** → *Start pairing (3 min)* (also available on the admin page). The frame becomes discoverable as **"Memomatic"** for 180 s.
2. Guest pairs from their phone's Bluetooth settings — no PIN (Just-Works pairing; the agent auto-accepts and trusts the device).
3. Guest shares a photo from their gallery via **Share → Bluetooth → Memomatic**. The file lands in the inbox, is ingested through the normal guest pipeline, and is pushed to the front of the slideshow (or to the pending-approval queue when *Require approval before showing* is on).
4. Sharing a `.txt` file posts its contents (truncated to 200 chars) as a frame message, same as the guest-page message box.

**Platform support is inherent to OBEX Object Push:** Android, Windows, macOS, and Linux can all "send file via Bluetooth". **iOS cannot** — Apple ships no OPP support. See "Why not a web page / BLE" below for why no webpage fixes that; iPhone guests use the existing Wi-Fi guest link.

## Architecture

```
phone ──OBEX push──▶ obexd (-a -r …/bluetooth-inbox)   ← pinboard-bluetooth.service
                          │ writes file                   (app/bt_agent.py, root:
                          ▼                                pairing agent + obexd)
        ~/pinboard/bluetooth-inbox/photo.jpg
                          │ polled every 2 s
                          ▼
        app.py bt_inbox_watcher (asyncio task in lifespan)
                          │ ingest_image_bytes(... source="bluetooth")
                          ▼
        guest pipeline: push-next / pending review / messages table
```

- **`app/bt_agent.py`** (new, root, `pinboard-bluetooth.service`): registers a BlueZ **NoInputNoOutput** agent (auto-accept pairing + service authorization, marks devices Trusted) and supervises **obexd** (`-n -a -r <inbox>` under a private session bus via `dbus-run-session`). It does **not** make the adapter discoverable — that is pairing mode, gated behind the UI button.
- **`app.py`**: `bt_inbox_watcher()` background task polls the inbox; files newer than 1.5 s are skipped (obexd may still be writing). Images go through `ingest_image_bytes()` (extracted from `save_upload()` — identical validation: extension allow-list, 15 MB cap, Pillow decode). The file is always deleted after processing so the inbox can't grow.
- **Endpoints**: `GET /api/bluetooth/status` (public, like `/api/network/ip` — the tokenless frame needs it) and `POST /api/bluetooth/pairing` (localhost-or-owner dual auth, like `/api/frame/*`). Both shell out to one-shot `bluetoothctl` commands; the app user is in the `bluetooth` group so no sudo is involved.
- **Settings**: `bluetooth_enabled` (default **off**, mirroring `guest_enabled`). When off, inbox files are discarded with a log line. `guest_review_required` applies to Bluetooth images exactly as it does to guest-link images.
- **Consent/abuse model**: physical access to the frame (or the owner token) is required to enter pairing mode; outside the 3-minute window the adapter is not discoverable. Already-paired devices stay trusted — revoke by `bluetoothctl devices` → `bluetoothctl remove <MAC>` (a paired-device management UI is a possible follow-up issue). There is no per-upload rate limit on the BT path (pairing is the gate); size/type validation is identical to guest uploads.

### Boot config / interplay with #4 and #25

`install.sh` now **removes** `dtoverlay=disable-bt`, **unmasks** `bluetooth.service`/`hciuart.service`, and installs `bluez`, `bluez-obexd`, `python3-dbus`, `python3-gi`. This deliberately undoes part of the #4 boot-time work (costs a few seconds of boot) — required for this feature.

`disable-bt` is also **suspect #2 in the touchscreen regression (#25)**. Testing this branch doubles as that experiment: if touch behaves differently with BT re-enabled, record it in #25. Test the #25 branch (gpu_mem) **first** so only one variable changes at a time. Both branches edit adjacent lines of `install.sh`'s boot-config block — whichever merges second will need a trivial conflict resolution.

### Why not a GitHub Pages web page / Web Bluetooth?

The issue suggested hosting a helper page on GitHub Pages if needed. Investigated and rejected:

- **Web Bluetooth** (the only browser→BT path) is Chrome/Edge-only and **not supported in iOS Safari at all** — it cannot cover the one platform OPP misses.
- It speaks **BLE GATT only**, so the Pi would need a custom GATT server (BlueZ peripheral mode) and a chunked file protocol; realistic BLE throughput on a Pi Zero 2 W is ~10–50 kB/s → a 3 MB photo takes 1–5 minutes.
- Platforms where Web Bluetooth works (Android/desktop Chrome) already have native OPP "share via Bluetooth", which is faster and needs no page.

So: no webpage required for the chosen approach; iOS guests use the Wi-Fi guest link. If BLE is ever wanted (e.g. for *messages only*, which are tiny), that's a separate issue.

## Local testing performed (no hardware)

All on `run-local.sh`-style setup with the new `local/bin/bluetoothctl` stub:

- `GET /api/bluetooth/status` returns stub adapter; `POST /api/bluetooth/pairing` flips discoverable on/off and survives a follow-up status read.
- Real PNG dropped in the inbox → ingested as `source=bluetooth`, queued push-next, served by `GET /api/slideshow/current`, inbox emptied.
- `.txt` in inbox → message created, truncated to 200 chars, delivered to the frame poll.
- Unsupported extension (`.docx`), corrupt `.jpg` (random bytes), and empty text → discarded with log lines, no crash, inbox emptied.
- `guest_review_required=1` → BT image lands as `pending` (admin Approve/Reject works on it like any pending image).
- `bluetooth_enabled=0` → inbox file discarded, count unchanged.
- Without `bluetoothctl` on PATH → status reports `available:false`, pairing returns 400 "Bluetooth is not available on this device."
- Owner upload, guest-link upload, wrong content-type rejection re-tested after the `save_upload` refactor — unchanged (verified identical behaviour against `main` for the octet-stream case).
- `python3 -m compileall`, `sh -n install.sh`, `node --check` on all three pages' inline JS: clean.

**Not tested (impossible without hardware):** real pairing, real OBEX transfer, obexd flags/path on the Pi's bluez build, bt_agent.py against a live BlueZ, adapter behaviour after unmasking, boot timing impact, and any effect on touch (#25).

## On-device test plan (human)

1. Check out this branch on the Pi and run `sudo PINBOARD_OWNER_TOKEN='memes' ./install.sh` (apt installs + unmask + boot-config edit), then **reboot** (the `disable-bt` overlay removal needs it).
2. `systemctl status bluetooth pinboard-bluetooth` — both active. `journalctl -u pinboard-bluetooth -n 30` should show "pairing agent registered" and "started obexd".
3. `bluetoothctl show` — controller present, `Alias: Memomatic`, `Powered: yes`, `Discoverable: no`.
4. On the frame: menu → **Bluetooth pairing** → status line should say "Ready…"; press **Start pairing** → "Pairing is ON…". Confirm `bluetoothctl show` now says `Discoverable: yes` and that it reverts after ~3 min.
5. Enable **Accept Bluetooth uploads** in `/admin` → Bluetooth panel.
6. From an Android phone (or Windows/macOS laptop): pair with "Memomatic" while pairing is on — should complete without a PIN prompt beyond a confirm tap.
7. Share a photo via Bluetooth to Memomatic. Expect: transfer completes, image appears on the frame within ~5 s (push-next), card in `/admin` shows `source: bluetooth`. `ls /home/memomatic/pinboard/bluetooth-inbox/` should be empty afterwards.
8. Share a small `.txt` file → toast message on the frame.
9. Turn *Accept Bluetooth uploads* off, send another photo → transfer succeeds at the phone, but nothing appears and `journalctl -u pinboard-app` logs the discard. (Rejecting at the OBEX layer instead is a possible refinement.)
10. **Re-test the touchscreen** and note results in #25 (disable-bt was a suspect there).

## Problems to expect on hardware, and what to do

| Symptom | Likely cause | Fix |
|---|---|---|
| `journalctl -u pinboard-bluetooth`: "obexd not found" | bluez-obexd not installed or path differs from the three candidates in `bt_agent.py` | `apt install bluez-obexd`; `dpkg -L bluez-obexd \| grep obexd$` and add the path to `OBEXD_CANDIDATES` |
| obexd starts then exits immediately | session-bus trouble under `dbus-run-session`, or stale obexd already running | `pgrep -af obexd`; try running the ExecStart command manually with `-d` added for debug. Fallback plan: run obexd as the `memomatic` user with a systemd user session instead of root+private bus |
| Phone pairs but "send file" fails / device rejects transfer | OPP profile not registered (obexd ↔ bluetoothd handshake), or phone requires authorization the agent didn't get | check `busctl tree org.bluez`; verify `bt_agent.py` logged `AuthorizeService … -> accept`; some stacks need the device Trusted *before* the push — it is set during pairing, confirm with `bluetoothctl info <MAC>` |
| Pairing prompt appears on phone but times out | agent not registered (service started before bluetoothd was ready) | `systemctl restart pinboard-bluetooth`; the script waits/retries for the adapter, check journal |
| `POST /api/bluetooth/pairing` → 400 "No agent"/dbus errors | app user lacks `bluetooth` group (group change needs re-login/service restart) | `id memomatic`; `sudo systemctl restart pinboard-app` after install added the group |
| Files appear in inbox but are never ingested | `bluetooth_enabled` off, or app can't delete root-owned files | check `/admin` toggle; `ls -la` the inbox — dir must be `memomatic:memomatic` mode 0775 (bt_agent enforces this at start) |
| `bluetoothctl` one-shot commands hang | very old bluez without one-shot CLI support | check `bluetoothctl --version` (Bookworm ships 5.66, fine); fallback is piping commands via stdin — change `run_btctl` accordingly |
| Boot noticeably slower / hciuart errors | expected few-second cost of re-enabling BT; hciuart flapping would indicate UART contention | if hciuart fails repeatedly, check nothing else claims the PL011; `dmesg \| grep -i 'hci\|uart'` |
| Touch breaks (or fixes!) after this branch | `disable-bt` interplay — see #25 | record in #25; run `sudo sh app/touch_diag.sh` (from the #25 branch) |

## Follow-up ideas (open as new issues if wanted)

- Paired-device list + "forget device" in the admin UI (`bluetoothctl devices`/`remove`).
- Reject transfers at the OBEX layer when uploads are disabled (stop obexd instead of discarding files).
- Show a "pairing active" countdown overlay on the frame itself.
- BLE GATT + Web Bluetooth page for *messages* from Chrome/Android (images impractical over BLE).
