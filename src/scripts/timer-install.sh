#!/bin/bash
set -euo pipefail
cat >/etc/systemd/system/behavioral-feature.timer <<'EOF'
[Unit]
Description=Behavioral Auth Feature Builder Timer
[Timer]
OnBootSec=2min
OnUnitActiveSec=60
Persistent=true
[Install]
WantedBy=timers.target
EOF
cat >/etc/systemd/system/behavioral-feature.service <<'EOF'
[Unit]
Description=Behavioral Auth Feature Builder
[Service]
Type=oneshot
ExecStart=/opt/behavioral-auth-v2/.venv/bin/python -m behavioral_auth features
Environment=PYTHONPATH=/opt/behavioral-auth-v2/src
EOF
systemctl daemon-reload
systemctl enable --now behavioral-feature.timer
