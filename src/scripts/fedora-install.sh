#!/bin/bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
INSTALL_DIR="/opt/behavioral-auth-v2"
ETC_DIR="/etc/behavioral-auth"
DATA_DIR="/var/lib/behavioral-auth"
USER_NAME="${SUDO_USER:-$USER}"
USER_HOME="$(getent passwd "$USER_NAME" | cut -d: -f6)"
mkdir -p "$INSTALL_DIR" "$ETC_DIR" "$DATA_DIR" "$USER_HOME/.config/systemd/user"
cp -r "$ROOT_DIR/src" "$INSTALL_DIR/"
cp "$ROOT_DIR/requirements.txt" "$ROOT_DIR/requirements-dev.txt" "$ROOT_DIR/pyproject.toml" "$INSTALL_DIR/" || true
cp "$ROOT_DIR/config/config.yaml" "$ETC_DIR/"
cp "$ROOT_DIR/db/schema.sql" "$ETC_DIR/"
cp "$ROOT_DIR/systemd/user"/*.service "$USER_HOME/.config/systemd/user/"
chown -R "$USER_NAME:$USER_NAME" "$INSTALL_DIR" "$DATA_DIR" "$USER_HOME/.config/systemd/user"
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"
cat >/etc/udev/rules.d/99-behavioral-auth.rules <<'EOF'
KERNEL=="event*", GROUP="input", MODE="0660"
EOF
usermod -aG input "$USER_NAME" || true
udevadm control --reload-rules || true
udevadm trigger || true
if command -v semanage >/dev/null 2>&1; then
  semanage fcontext -a -t var_lib_t "$DATA_DIR(/.*)?" || true
  restorecon -Rv "$DATA_DIR" || true
fi
echo "Installed to $INSTALL_DIR"
