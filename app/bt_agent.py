#!/usr/bin/env python3
"""
Memomatic Bluetooth pairing agent + OBEX push inbox supervisor (issue #1).

Run as root by pinboard-bluetooth.service. Two jobs:

1. Pairing agent — registers a BlueZ "NoInputNoOutput" agent on the system
   bus so phones can pair with the frame using Just-Works pairing (no PIN,
   no keyboard — the frame has neither). Devices are marked Trusted on
   pairing so later sends reconnect without re-authorisation. Whether the
   adapter is *discoverable* (i.e. pairing mode) is NOT controlled here; the
   app toggles it via `bluetoothctl` when the owner presses "Start pairing"
   on the frame menu or admin page (POST /api/bluetooth/pairing).

2. obexd supervisor — runs BlueZ's obexd with --auto-accept rooted at the
   pinboard's bluetooth-inbox directory, so files shared from a paired phone
   ("Share via Bluetooth" on Android/macOS/Windows; iOS does not support
   OBEX push — see docs/bluetooth-guest-uploads.md) land in the inbox.
   app.py's bt_inbox_watcher ingests and removes them. obexd normally lives
   on a per-user session bus; since this is a headless root service we give
   it a private session bus via dbus-run-session.

Dependencies (installed by install.sh): bluez, bluez-obexd, python3-dbus,
python3-gi.
"""
import os
import pwd
import shutil
import signal
import subprocess
import sys
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s bt_agent: %(message)s")
log = logging.getLogger(__name__)

try:
    import dbus
    import dbus.service
    import dbus.mainloop.glib
    from gi.repository import GLib
except ImportError as exc:  # pragma: no cover - environment dependent
    log.error("missing dependency (%s); install python3-dbus and python3-gi", exc)
    sys.exit(1)

BLUEZ = "org.bluez"
AGENT_IFACE = "org.bluez.Agent1"
AGENT_PATH = "/com/memomatic/agent"
ADAPTER_ALIAS = os.environ.get("PINBOARD_BT_ALIAS", "Memomatic")
INBOX_DIR = os.environ.get("PINBOARD_BT_INBOX", "/home/memomatic/pinboard/bluetooth-inbox")
INBOX_OWNER = os.environ.get("PINBOARD_BT_INBOX_OWNER", "memomatic")
# obexd location varies across Debian/Raspbian releases.
OBEXD_CANDIDATES = (
    "/usr/libexec/bluez/obexd",   # Debian/Raspberry Pi OS Bookworm
    "/usr/lib/bluetooth/obexd",   # Buster/Bullseye
    "/usr/lib/bluez/obexd",
)
OBEXD_RESTART_MIN_SECONDS = 30

bus = None
mainloop = None
obexd_proc = None
obexd_last_start = 0.0


def set_trusted(device_path: str) -> None:
    try:
        props = dbus.Interface(bus.get_object(BLUEZ, device_path), "org.freedesktop.DBus.Properties")
        props.Set("org.bluez.Device1", "Trusted", dbus.Boolean(True))
        log.info("trusted device %s", device_path)
    except dbus.DBusException as exc:
        log.warning("could not trust %s: %s", device_path, exc)


class PairingAgent(dbus.service.Object):
    """Auto-accepting NoInputNoOutput agent: the frame has no input surface
    suited to PIN entry, and the physical pairing-mode button (frame menu)
    is the consent gate. Discoverability is off outside pairing mode."""

    @dbus.service.method(AGENT_IFACE, in_signature="", out_signature="")
    def Release(self):
        log.info("agent released")

    @dbus.service.method(AGENT_IFACE, in_signature="o", out_signature="s")
    def RequestPinCode(self, device):
        log.info("RequestPinCode %s -> 0000", device)
        set_trusted(str(device))
        return "0000"

    @dbus.service.method(AGENT_IFACE, in_signature="o", out_signature="u")
    def RequestPasskey(self, device):
        log.info("RequestPasskey %s -> 0", device)
        set_trusted(str(device))
        return dbus.UInt32(0)

    @dbus.service.method(AGENT_IFACE, in_signature="ouq", out_signature="")
    def DisplayPasskey(self, device, passkey, entered):
        log.info("DisplayPasskey %s %06u", device, passkey)

    @dbus.service.method(AGENT_IFACE, in_signature="os", out_signature="")
    def DisplayPinCode(self, device, pincode):
        log.info("DisplayPinCode %s %s", device, pincode)

    @dbus.service.method(AGENT_IFACE, in_signature="ou", out_signature="")
    def RequestConfirmation(self, device, passkey):
        log.info("RequestConfirmation %s %06u -> accept", device, passkey)
        set_trusted(str(device))

    @dbus.service.method(AGENT_IFACE, in_signature="o", out_signature="")
    def RequestAuthorization(self, device):
        log.info("RequestAuthorization %s -> accept", device)
        set_trusted(str(device))

    @dbus.service.method(AGENT_IFACE, in_signature="os", out_signature="")
    def AuthorizeService(self, device, uuid):
        log.info("AuthorizeService %s %s -> accept", device, uuid)
        set_trusted(str(device))

    @dbus.service.method(AGENT_IFACE, in_signature="", out_signature="")
    def Cancel(self):
        log.info("pairing cancelled by peer")


def find_adapter_path() -> str | None:
    om = dbus.Interface(bus.get_object(BLUEZ, "/"), "org.freedesktop.DBus.ObjectManager")
    for path, ifaces in om.GetManagedObjects().items():
        if "org.bluez.Adapter1" in ifaces:
            return str(path)
    return None


def setup_adapter(adapter_path: str) -> None:
    props = dbus.Interface(bus.get_object(BLUEZ, adapter_path), "org.freedesktop.DBus.Properties")
    props.Set("org.bluez.Adapter1", "Alias", ADAPTER_ALIAS)
    props.Set("org.bluez.Adapter1", "Powered", dbus.Boolean(True))
    # Not discoverable by default: pairing mode is explicitly enabled (with a
    # timeout) from the frame menu / admin page via the app.
    log.info("adapter %s ready (alias=%s, powered)", adapter_path, ADAPTER_ALIAS)


def ensure_inbox() -> None:
    os.makedirs(INBOX_DIR, exist_ok=True)
    try:
        user = pwd.getpwnam(INBOX_OWNER)
        os.chown(INBOX_DIR, user.pw_uid, user.pw_gid)
    except (KeyError, OSError) as exc:
        log.warning("could not chown %s to %s: %s", INBOX_DIR, INBOX_OWNER, exc)
    # The app user must be able to delete root-owned files obexd writes here,
    # which only requires write permission on the directory (owned above).
    os.chmod(INBOX_DIR, 0o775)


def find_obexd() -> str | None:
    for candidate in OBEXD_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return None


def start_obexd() -> None:
    global obexd_proc, obexd_last_start
    obexd = find_obexd()
    if obexd is None:
        log.error("obexd not found (tried %s) — install bluez-obexd; image push will not work",
                  ", ".join(OBEXD_CANDIDATES))
        return
    cmd = [obexd, "-n", "-a", "-r", INBOX_DIR]
    # obexd expects a session bus; running headless as root we give it a
    # private one. (Its client D-Bus API is unused — bluetoothd talks to it
    # over the system bus for the OPP profile, which is all we need.)
    if shutil.which("dbus-run-session"):
        cmd = ["dbus-run-session", "--"] + cmd
    elif "DBUS_SESSION_BUS_ADDRESS" not in os.environ:
        log.warning("dbus-run-session not found and no session bus; obexd may fail to start")
    obexd_last_start = time.time()
    obexd_proc = subprocess.Popen(cmd)
    log.info("started obexd (pid %s): %s", obexd_proc.pid, " ".join(cmd))


def supervise_obexd() -> bool:
    """GLib timer: restart obexd if it died, rate-limited."""
    if obexd_proc is not None and obexd_proc.poll() is not None:
        log.warning("obexd exited with code %s", obexd_proc.returncode)
        if time.time() - obexd_last_start >= OBEXD_RESTART_MIN_SECONDS:
            start_obexd()
    return True  # keep the timer


def shutdown(*_args) -> None:
    log.info("shutting down")
    if obexd_proc is not None and obexd_proc.poll() is None:
        obexd_proc.terminate()
        try:
            obexd_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            obexd_proc.kill()
    if mainloop is not None:
        mainloop.quit()


def main() -> int:
    global bus, mainloop
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()

    ensure_inbox()

    # Wait for bluetoothd + adapter (BT may still be initialising at boot).
    adapter_path = None
    while adapter_path is None:
        try:
            adapter_path = find_adapter_path()
        except dbus.DBusException as exc:
            log.warning("bluetoothd not reachable yet: %s", exc)
        if adapter_path is None:
            log.info("no Bluetooth adapter yet; retrying in 5 s")
            time.sleep(5)

    setup_adapter(adapter_path)

    PairingAgent(bus, AGENT_PATH)
    manager = dbus.Interface(bus.get_object(BLUEZ, "/org/bluez"), "org.bluez.AgentManager1")
    manager.RegisterAgent(AGENT_PATH, "NoInputNoOutput")
    manager.RequestDefaultAgent(AGENT_PATH)
    log.info("pairing agent registered (NoInputNoOutput)")

    start_obexd()
    GLib.timeout_add_seconds(5, supervise_obexd)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    mainloop = GLib.MainLoop()
    mainloop.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
