"""
Memomatic deploy script.
Copies app files to the Pi, verifies checksums, then restarts the services.
Run via deploy.bat (which installs paramiko first).
"""
import hashlib
import io
import os
import sys

PI_HOST = "172.19.253.202"
PI_USER = "memomatic"
PI_PASS = "memes"
PI_HOME = "/home/memomatic/pinboard"

REPO = os.path.dirname(os.path.abspath(__file__))

# (local relative path, remote absolute path)
FILES = [
    ("app/app.py",                    f"{PI_HOME}/app/app.py"),
    ("app/kiosk.sh",                  f"{PI_HOME}/app/kiosk.sh"),
    ("app/static/admin.html",         f"{PI_HOME}/app/static/admin.html"),
    ("app/static/frame.html",         f"{PI_HOME}/app/static/frame.html"),
    ("app/static/guest.html",         f"{PI_HOME}/app/static/guest.html"),
    ("app/spi_touch_read.c",          f"{PI_HOME}/app/spi_touch_read.c"),
    ("app/touch_bridge.py",           f"{PI_HOME}/app/touch_bridge.py"),
    ("app/touch_test.py",             f"{PI_HOME}/app/touch_test.py"),
    ("app/raw_touch.py",              f"{PI_HOME}/app/raw_touch.py"),
    ("systemd/pinboard-app.service",  "/tmp/pinboard-app.service"),
    ("systemd/pinboard-kiosk.service","/tmp/pinboard-kiosk.service"),
    ("systemd/pinboard-touch.service","/tmp/pinboard-touch.service"),
    ("install.sh",                    "/tmp/memomatic-install.sh"),
]


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def main():
    try:
        import paramiko
    except ImportError:
        print("ERROR: paramiko not installed. Run deploy.bat instead of deploy.py directly.")
        sys.exit(1)

    # Read all local files up front
    print("Reading local files...")
    local_data = {}
    for local_rel, _ in FILES:
        local_path = os.path.join(REPO, local_rel)
        with open(local_path, "rb") as f:
            local_data[local_rel] = f.read()
        print(f"  OK        {local_rel}")
    print()

    # Connect
    print(f"Connecting to {PI_USER}@{PI_HOST}...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(PI_HOST, username=PI_USER, password=PI_PASS, timeout=15)
    print("Connected.\n")

    sftp = ssh.open_sftp()

    # Upload every file and verify the remote checksum matches local
    print("Uploading files...")
    upload_ok = True
    for local_rel, remote_path in FILES:
        data = local_data[local_rel]
        sftp.putfo(io.BytesIO(data), remote_path)

        with sftp.open(remote_path, "rb") as rf:
            remote_data = rf.read()

        if sha256_bytes(remote_data) != sha256_bytes(data):
            print(f"  CORRUPT   {remote_path}")
            upload_ok = False
        else:
            print(f"  OK        {remote_path}")

    sftp.close()

    if not upload_ok:
        print("\nOne or more files were corrupted during upload. Aborting.")
        ssh.close()
        sys.exit(1)

    print()

    # Move systemd units into place and restart services
    commands = [
        "sudo cp /tmp/pinboard-app.service /etc/systemd/system/pinboard-app.service",
        "sudo cp /tmp/pinboard-kiosk.service /etc/systemd/system/pinboard-kiosk.service",
        "sudo cp /tmp/pinboard-touch.service /etc/systemd/system/pinboard-touch.service",
        "sudo chmod +x /tmp/memomatic-install.sh",
        "sudo chmod +x /home/memomatic/pinboard/app/touch_bridge.py",
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
