#!/bin/bash
# Installer for proxmox-nas-gui on a Proxmox VE host (or any Debian-based
# system, including LXC containers). Run as root from the repository root:
#   ./deploy/install.sh
set -euo pipefail

INSTALL_DIR=/opt/proxmox-nas-gui
CONF_DIR=/etc/proxmox-nas-gui
PORT=8481

if [[ $EUID -ne 0 ]]; then
    echo "This installer must run as root." >&2
    exit 1
fi

REPO_DIR=$(cd "$(dirname "$0")/.." && pwd)

echo "==> Installing packages (samba, python3-venv, openssl, e2fsprogs, xfsprogs, fdisk, udev, wsdd)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq samba mergerfs python3-venv openssl libpam0g e2fsprogs xfsprogs fdisk udev wsdd

echo "==> Copying application to ${INSTALL_DIR}"
mkdir -p "$INSTALL_DIR"
# cp -r merges into an existing tree, so files deleted or moved since the last
# install would survive and stay importable. Clear the code directories first;
# venv/ is deliberately kept so the install does not rebuild it every time.
rm -rf "$INSTALL_DIR/backend" "$INSTALL_DIR/frontend"
cp -r "$REPO_DIR/backend" "$REPO_DIR/frontend" "$INSTALL_DIR/"

echo "==> Creating Python virtualenv"
if [[ ! -d "$INSTALL_DIR/venv" ]]; then
    python3 -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install -q --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/backend/requirements.txt"

echo "==> Preparing ${CONF_DIR}"
mkdir -p "$CONF_DIR"
chmod 700 "$CONF_DIR"

if [[ ! -f "$CONF_DIR/cert.pem" ]]; then
    echo "==> Generating self-signed TLS certificate"
    openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
        -subj "/CN=$(hostname -f 2>/dev/null || hostname)" \
        -keyout "$CONF_DIR/key.pem" -out "$CONF_DIR/cert.pem" 2>/dev/null
    chmod 600 "$CONF_DIR/key.pem"
fi

echo "==> Installing systemd service"
cp "$REPO_DIR/deploy/proxmox-nas-gui.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now smbd
# nmbd (NetBIOS name/browsing) and wsdd (WS-Discovery) aren't required for
# the shares themselves to work over SMB, but without them the host never
# shows up in Windows' Network view - only a direct \\host\share UNC path
# works. Both together cover older and newer Windows versions.
systemctl enable --now nmbd
systemctl enable --now wsdd
# "enable --now" only starts a stopped unit, so re-running the installer would
# leave the previous process serving the old code. Restart explicitly.
systemctl enable proxmox-nas-gui
systemctl restart proxmox-nas-gui

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo
echo "Done. Open https://${IP:-<host>}:${PORT}/ and sign in as root"
echo "with the host's root password (the certificate is self-signed,"
echo "so the browser will show a warning once)."
