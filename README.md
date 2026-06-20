# behavioral-auth structured runnable

Runnable structured version of the local-only behavioral authentication stack.

## Quick start

```bash
make venv
sudo bash src/scripts/fedora-install.sh
.venv/bin/python src/scripts/bootstrap_db.py
systemctl --user daemon-reload
systemctl --user enable --now behavioral-collector.service
systemctl --user enable --now behavioral-inference.service
sudo bash src/scripts/timer-install.sh
```

## CLI

```bash
.venv/bin/python -m behavioral_auth collector
.venv/bin/python -m behavioral_auth features
.venv/bin/python -m behavioral_auth train
.venv/bin/python -m behavioral_auth infer
.venv/bin/python -m behavioral_auth report
```
