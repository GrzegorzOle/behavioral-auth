#!/bin/bash
# Ubuntu / Debian installation script for behavioral-auth
# Run as root:  sudo bash src/scripts/ubuntu-install.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
INSTALL_DIR="/opt/behavioral-auth-v2"
ETC_DIR="/etc/behavioral-auth"
DATA_DIR="/var/lib/behavioral-auth"
USER_NAME="${SUDO_USER:-$USER}"
USER_HOME="$(getent passwd "$USER_NAME" | cut -d: -f6)"

# ── 1. System dependencies ─────────────────────────────────────────────────
echo "[1/6] Installing system dependencies..."
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
    python3-pip \
    build-essential \
    libgtk-3-dev \
    libglib2.0-dev \
    libopencv-dev \
    libv4l-dev \
    v4l-utils \
    evtest \
    input-utils

# ── 2. Directory layout ────────────────────────────────────────────────────
echo "[2/6] Creating directories..."
mkdir -p "$INSTALL_DIR" "$ETC_DIR" "$DATA_DIR" "$USER_HOME/.config/systemd/user"

# ── 3. Copy project files ──────────────────────────────────────────────────
echo "[3/6] Copying project files..."
cp -r "$ROOT_DIR/src" "$INSTALL_DIR/"
cp "$ROOT_DIR/requirements.txt" \
   "$ROOT_DIR/requirements-dev.txt" \
   "$ROOT_DIR/pyproject.toml" \
   "$INSTALL_DIR/" 2>/dev/null || true
cp "$ROOT_DIR/config/config.yaml" "$ETC_DIR/"
cp "$ROOT_DIR/db/schema.sql" "$ETC_DIR/"
cp "$ROOT_DIR/systemd/user"/*.service "$USER_HOME/.config/systemd/user/"

chown -R "$USER_NAME:$USER_NAME" \
    "$INSTALL_DIR" "$DATA_DIR" \
    "$USER_HOME/.config/systemd/user"

# ── 4. Python virtual environment ─────────────────────────────────────────
echo "[4/6] Setting up Python virtual environment..."
python3.11 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip --quiet
"$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt" --quiet

# Install the package itself in editable mode
"$INSTALL_DIR/.venv/bin/pip" install -e "$INSTALL_DIR" --quiet 2>/dev/null || \
"$INSTALL_DIR/.venv/bin/pip" install "$INSTALL_DIR" --quiet

# ── 5. udev rules for /dev/input/event* ───────────────────────────────────
echo "[5/6] Configuring udev rules..."
cat > /etc/udev/rules.d/99-behavioral-auth.rules <<'EOF'
KERNEL=="event*", GROUP="input", MODE="0660"
EOF
usermod -aG input "$USER_NAME" || true
udevadm control --reload-rules || true
udevadm trigger || true

# ── 6. Initialise database ─────────────────────────────────────────────────
echo "[6/6] Bootstrapping database..."
BEHAVIORAL_DB_PATH="$DATA_DIR/behavioral.db" \
    "$INSTALL_DIR/.venv/bin/python" \
    "$INSTALL_DIR/src/scripts/bootstrap_db.py" || true

echo ""
echo "✅  behavioral-auth installed to $INSTALL_DIR"
echo ""
echo "Next steps:"
echo "  1. Log out and back in (so the 'input' group takes effect)"
echo "     OR run:  newgrp input"
echo "  2. Activate the venv:"
echo "       source $INSTALL_DIR/.venv/bin/activate"
echo "  3. Collect training data (at least 15 min of normal use):"
echo "       sg input -c 'behavioral-collector'"
echo "  4. Extract features and train:"
echo "       behavioral-features && CUDA_VISIBLE_DEVICES='' behavioral-train"
echo "  5. Live verification:"
echo "       behavioral-verify --duration 120 --no-face"
echo ""
echo "To enable systemd user services:"
echo "  systemctl --user daemon-reload"
echo "  systemctl --user enable --now behavioral-collector.service"
echo "  systemctl --user enable --now behavioral-inference.service"
echo "  sudo bash $ROOT_DIR/src/scripts/timer-install.sh"

