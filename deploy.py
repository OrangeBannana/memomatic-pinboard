"""
Memomatic deploy script.
Copies app files to the Pi, verifies checksums, then restarts the services.
Run via deploy.bat (which installs paramiko first).
"""
import hashlib
import os
import sys

PI_HOST = "172.19.253.202"
PI_USER = "memomatic"
PI_PASS = "memes"
PI_HOME = "/home/memomatic/pinboard"

REPO = os.path.dirname(os.path.abspath(__file__))

# Files: (local relative path, remote absolute path, expected sha256)
FILES = [
    ("app/app.py",                 f"{PI_HOME}/app/app.py",               "e77af0ff9e697fbb176af6654b58e61b9ebfe38fd4a091da1fb92f632f079043"),
    ("app/kiosk.sh",               f"{PI_HOME}/app/kiosk.sh",             "9bbd31af92066e1fe25a7c92e13096f592935da6fa55205e123396f1775354af"),
    ("app/static/admin.html",      f"{PI_HOME}/app/static/admin.html",    "275ed8e2661f817133ac96bdbce069bc4b80db5ec8bbb052d29344aa4297decf"),
    ("app/static/frame.html",      f"{PI_HOME}/app/static/frame.html",    "6bd5b16a4f85a136f0b95950c720fc1b5ec9df53925920ad61287aa2dfc03f85"),
    ("app/static/guest.html",      f"{PI_HOME}/app/static/guest.html",    "9d67007cb0ea20b24b5d9fabc44fc6bc3861886f8e3c37e5204a5b19161e74c8"),
    ("app/spi_touch_read.c",        f"{PI_HOME}/app/spi_touch_read.c",     "e9ec465c48340c01eff5c7735c000d317e106e590bfa716f1a1345566df0380b"),
    ("app/touch_bridge.py",        f"{PI_HOME}/app/touch_bridge.py",      "506ffa99f40e4dd91d08d1c5ccd03428decb255af07042691217d534e0e020aa"),
    ("systemd/pinboard-app.service",  "/tmp/pinboard-app.service",        "945ca6dec21af7c4ef2a9f5b99612e3f2e4f719a6da65ca5a4757147e1ec97f7"),
    ("systemd/pinboard-kiosk.service","/tmp/pinboard-kiosk.service",      "e022fe7d7c2962760ade8ace9b018c31375ff668468130b91464b19340fea325"),
    ("systemd/pinboard-touch.service", "/tmp/pinboard-touch.service",     "5f1e999337b7f9aa71fe75a76305c9524e2aab999c3c33d7e68b9bbe0aed2d2a"),
    ("install.sh",                 "/tmp/memomatic-install.sh",           "9517bdae7501aac448af5b0f38b5045941050b4a1c4102c8aff29ddbf5ec3bcd"),
]


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def main():
    try:
        import paramiko
    except ImportError:
        print("ERROR: paramiko not installed. Run deploy.bat instead of deploy.py directly.")
        sys.exit(1)

    # Verify all local files match expected checksums before we even connect
    print("Verifying local file checksums...")
    ok = True
    for local_rel, _, expected in FILES:
        local_path = os.path.join(REPO, local_rel)
        actual = sha256_file(local_path)
        if actual != expected:
            print(f"  MISMATCH  {local_rel}")
            print(f"    expected: {expected}")
            print(f"    actual:   {actual}")
            ok = False
        else:
            print(f"  OK        {local_rel}")
    if not ok:
        print("\nLocal checksum failures — aborting. Re-run the bug fixes and try again.")
        sys.exit(1)
    print()

    # Connect
    print(f"Connecting to {PI_USER}@{PI_HOST}...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(PI_HOST, username=PI_USER, password=PI_PASS, timeout=15)
    print("Connected.\n")

    sftp = ssh.open_sftp()

    # Upload every file and verify the remote checksum immediately
    print("Uploading files...")
    upload_ok = True
    for local_rel, remote_path, expected in FILES:
        local_path = os.path.join(REPO, local_rel)
        with open(local_path, "rb") as f:
            data = f.read()

        sftp.putfo(__import__("io").BytesIO(data), remote_path)

        # Read back and verify
        with sftp.open(remote_path, "rb") as rf:
            remote_data = rf.read()
        remote_hash = sha256_bytes(remote_data)

        if remote_hash != expected:
            print(f"  CORRUPT   {remote_path}")
            print(f"    expected: {expected}")
            print(f"    got:      {remote_hash}")
            upload_ok = False
        else:
            print(f"  OK        {remote_path}")

    sftp.close()

    if not upload_ok:
        print("\nOne or more files were corrupted during upload. Aborting.")
        ssh.close()
        sys.exit(1)

    print()

    # Move systemd units into place and run install.sh
    commands = [
        "sudo cp /tmp/pinboard-app.service /etc/systemd/system/pinboard-app.service",
        "sudo cp /tmp/pinboard-kiosk.service /etc/systemd/system/pinboard-kiosk.service",
        "sudo cp /tmp/pinboard-touch.service /etc/systemd/system/pinboard-touch.service",
        "sudo chmod +x /tmp/memomatic-install.sh",
        "sudo chmod +x /home/memomatic/pinboard/app/touch_bridge.py",
        # Run only the file-copy + systemd portions (skip apt since it's slow & likely done)
        "sudo systemctl daemon-reload",
        "sudo systemctl enable pinboard-app.service pinboard-kiosk.service pinboard-touch.service",
        "sudo systemctl restart pinboard-app.service",
        "sudo systemctl restart pinboard-kiosk.service",
        "sudo systemctl restart pinboard-touch.service",
        "echo DONE",
    ]

    print("Running remote commands...")
    for cmd in commands:
        print(f"  $ {cmd}")
        stdin, stdout, stderr = ssh.exec_command(cmd)
        stdin.write(PI_PASS + "\n")
        stdin.flush()
        out = stdout.read().decode().strip()
        err = stderr.read().decode().strip()
        if out:
            print(f"    {out}")
        if err:
            print(f"    [stderr] {err}")

    ssh.close()
    print("\nDeploy complete. App restarted on the Pi.")
    print(f"Admin page: http://{PI_HOST}:8080/admin")


if __name__ == "__main__":
    main()
