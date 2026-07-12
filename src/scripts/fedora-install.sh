#!/bin/bash
# Fedora / RHEL installer.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
INSTALL_DIR="/opt/behavioral-auth"
ETC_DIR="/etc/behavioral-auth"
DATA_DIR="/var/lib/behavioral-auth"
USER_NAME="${SUDO_USER:-$USER}"
USER_HOME="$(getent passwd "$USER_NAME" | cut -d: -f6)"

mkdir -p "$INSTALL_DIR" "$ETC_DIR" "$DATA_DIR" "$USER_HOME/.config/systemd/user"

cp -r "$ROOT_DIR/src" "$INSTALL_DIR/"
cp "$ROOT_DIR/requirements.txt" "$ROOT_DIR/pyproject.toml" "$ROOT_DIR/README.md" "$INSTALL_DIR/"
cp "$ROOT_DIR/config"/config*.yaml "$ETC_DIR/"
cp "$ROOT_DIR/systemd/user/behavioral-authd.service" "$USER_HOME/.config/systemd/user/"
chown -R "$USER_NAME:$USER_NAME" "$INSTALL_DIR" "$DATA_DIR" "$USER_HOME/.config/systemd/user"

python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"
"$INSTALL_DIR/.venv/bin/pip" install -e "$INSTALL_DIR"

cat >/etc/udev/rules.d/99-behavioral-auth.rules <<'EOF'
KERNEL=="event*", GROUP="input", MODE="0660"
EOF

# input: read the keyboard and mouse.  video: the camera, for face enrolment.
usermod -aG input,video "$USER_NAME" || true
udevadm control --reload-rules || true
udevadm trigger || true

if command -v semanage >/dev/null 2>&1; then
  semanage fcontext -a -t var_lib_t "$DATA_DIR(/.*)?" || true
  restorecon -Rv "$DATA_DIR" || true
fi

# The database is created and migrated by the daemon on first start — there is
# no schema step to run here.

cat <<EOF

Installed to $INSTALL_DIR

Log out and back in (for the input/video groups), then start it:

    systemctl --user enable --now behavioral-authd
    journalctl --user -fu behavioral-authd

Or run it in a terminal to watch it learn:

    $INSTALL_DIR/.venv/bin/behavioral-authd

It will collect behaviour for a few hours, converge on a pattern, freeze it, and
then warn you — never lock you out — if someone else starts using the machine.

    behavioral-auth status       what it is doing right now
    behavioral-auth reset        somebody else will be using this machine
EOF
