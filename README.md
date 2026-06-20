# behavioral-auth

A local-only **behavioural authentication** stack for Linux (and Windows via OpenCV).
It continuously profiles your typing and mouse patterns, detects anomalies with an
ONNX autoencoder, and optionally adds face verification via OpenCV LBPH or Linux howdy.

---

## Platform Support

| Feature | Fedora / RHEL | Ubuntu / Debian | Windows 10/11 |
|---------|:---:|:---:|:---:|
| `behavioral-collector` (evdev) | ✅ | ✅ | ❌ evdev not available |
| `behavioral-features` | ✅ | ✅ | ✅ (with existing DB) |
| `behavioral-train` | ✅ | ✅ | ✅ |
| `behavioral-infer` | ✅ | ✅ | ✅ |
| `behavioral-verify` (live dashboard) | ✅ | ✅ | ❌ requires collector |
| `behavioral-face` (OpenCV LBPH) | ✅ | ✅ | ✅ |
| howdy backend (IR camera) | ✅ | ✅ | ❌ Linux only |
| systemd services / timers | ✅ | ✅ | ❌ use Task Scheduler |

> **Windows users:** only face enrolment/verification and inference on
> pre-collected data are supported.  Use a Linux machine or WSL2 for the
> collection step, then copy the DuckDB file over.

---

## Architecture

```
keyboard / mouse
      │
      ▼
 behavioral-collector          ← evdev event capture (Linux only)
      │ raw_events (DuckDB)
      ▼
 behavioral-features           ← 21-feature windows + sliding sequences
      │ fused_sequences (DuckDB)
      ▼
 behavioral-train              ← Conv1D autoencoder → ONNX export
      │ model.onnx + scaler.json
      ▼
 behavioral-infer              ← reconstruction error → anomaly score
      │                           fused with face score
      ▼
 behavioral-verify             ← live dashboard (collection + scoring)
```

---

## Requirements

| Dependency | Version | Notes |
|------------|---------|-------|
| Python | 3.11+ | 3.11 recommended (torch/onnxruntime) |
| PyTorch | ≥ 2.4 | CPU-only build works fine |
| ONNX Runtime | ≥ 1.19 | |
| OpenCV (contrib) | ≥ 4.10 | `opencv-contrib-python` on pip |
| DuckDB | ≥ 0.10 | embedded, no server needed |
| evdev | ≥ 1.7 | **Linux only** – omitted on Windows |

---

## Installation

### Fedora / RHEL

```bash
# 1. Clone and enter project directory
git clone <repo-url> behavioral-auth && cd behavioral-auth

# 2. Run the Fedora installer (installs to /opt/behavioral-auth-v2)
sudo bash src/scripts/fedora-install.sh

# 3. Or – developer install inside a local venv:
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
python src/scripts/bootstrap_db.py
```

### Ubuntu / Debian

```bash
# 1. Clone and enter project directory
git clone <repo-url> behavioral-auth && cd behavioral-auth

# 2. Run the Ubuntu installer (requires sudo, installs to /opt/behavioral-auth-v2)
sudo bash src/scripts/ubuntu-install.sh

# 3. Or – developer install inside a local venv:
# Install system packages first
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv python3.11-dev \
     build-essential libgtk-3-dev libv4l-dev

python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
python src/scripts/bootstrap_db.py

# Add current user to the input group (required for evdev)
sudo usermod -aG input "$USER"
# Then log out/in, or use: newgrp input
```

### Windows 10 / 11

```powershell
# 1. Clone and enter project directory
git clone <repo-url> behavioral-auth
cd behavioral-auth

# 2. Allow script execution (current session only)
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

# 3. Run the Windows installer (requires Administrator)
.\src\scripts\windows-install.ps1

# 4. Or – manual install:
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-windows.txt   # evdev excluded
pip install -e .
python src\scripts\bootstrap_db.py
```

> **Note:** Python 3.11 must be installed separately from
> [python.org](https://www.python.org/downloads/).

---

## Quick Start

### Linux (full pipeline)

```bash
# Step 1 – Collect data (minimum 15 minutes of normal use)
sg input -c "behavioral-collector"
# Press Ctrl+C to stop

# Step 2 – Extract features
behavioral-features

# Step 3 – Train the model (force CPU if GPU is unsupported)
CUDA_VISIBLE_DEVICES="" behavioral-train

# Step 4 – Live verification dashboard
behavioral-verify --duration 120 --no-face

# Step 5 – Enroll face (optional)
behavioral-face enroll
behavioral-verify --duration 120
```

### Windows (face + inference only)

```powershell
# Activate venv
.\.venv\Scripts\Activate.ps1

# Enroll face
behavioral-face enroll --samples 40

# Verify face
behavioral-face verify --preview

# If you have a DuckDB file from Linux, run inference
behavioral-infer
```

---

## CLI Reference

| Command | Platform | Description |
|---------|----------|-------------|
| `behavioral-collector` | Linux | Capture keyboard/mouse events to DuckDB |
| `behavioral-features` | All | Extract feature windows and sequences |
| `behavioral-train` | All | Train the ONNX autoencoder |
| `behavioral-infer` | All | Run one inference cycle |
| `behavioral-infer --loop` | All | Continuous inference loop |
| `behavioral-verify` | Linux | Live dashboard: collect + score |
| `behavioral-face enroll` | All | Capture face samples and train LBPH model |
| `behavioral-face verify` | All | One-shot face verification |
| `behavioral-face info` | All | Show face model status |
| `behavioral-face delete` | All | Remove trained face model |
| `behavioral-status` | All | Full pipeline status report |
| `behavioral-report` | All | Print decision metrics (FAR/FRR) |

---

## Face Verification

Two backends are supported:

### OpenCV LBPH (cross-platform)

Works on Linux and Windows with any USB/built-in camera.

```bash
# Enroll (live camera window, collect 40 samples)
behavioral-face enroll --samples 40

# Verify
behavioral-face verify --preview

# Incremental update (add more samples without retraining from scratch)
behavioral-face enroll --update --samples 20
```

**LBPH confidence scale** (lower = better match):

| Confidence | Quality |
|------------|---------|
| 0 – 40 | Excellent match |
| 40 – 80 | Good match ✅ |
| 80 – 100 | Borderline |
| > 100 | No match / unknown person |

### howdy (Linux only)

Uses the IR camera-based howdy daemon.

```yaml
# config/config.yaml
face:
  enabled: true
  backend: "howdy"
```

---

## Configuration

Main config: `config/config.yaml`
Dev overrides: `config/config.dev.yaml` (merged automatically in `mode: dev`)

Key sections:

```yaml
general:
  mode: dev          # dev | enforce

features:
  window_sec: 30     # sliding window duration
  stride_sec: 5      # window stride

model:
  seq_len: 24        # sequence length fed to autoencoder
  epochs: 25

fusion:
  behavioral_weight: 0.7
  howdy_weight: 0.3
  challenge_threshold: 0.55   # fused score >= this -> CHALLENGE
  lock_threshold: 0.78        # fused score >= this -> LOCK

face:
  enabled: true
  backend: "opencv"            # opencv | howdy
  camera_index: 0
  confidence_threshold: 80.0  # LBPH cut-off (lower = stricter)
```

---

## Decision Logic

| Fused score | Mode `dev` | Mode `enforce` |
|-------------|-----------|----------------|
| `< challenge` | SIMULATE_ALLOW | ALLOW |
| `>= challenge` | SIMULATE_CHALLENGE | CHALLENGE |
| `>= lock` | SIMULATE_CHALLENGE | LOCK (runs lock_cmd) |

---

## Running Tests

```bash
source .venv/bin/activate      # Linux
# .\.venv\Scripts\Activate.ps1  # Windows
pytest tests/ -v
```

---

## systemd Services (Linux)

```bash
# User-level services (collector + inference)
systemctl --user daemon-reload
systemctl --user enable --now behavioral-collector.service
systemctl --user enable --now behavioral-inference.service

# System-level feature-extraction timer (runs every 60 s)
sudo bash src/scripts/timer-install.sh
```

> **Windows equivalent:** use Task Scheduler to run
> `behavioral-features` and `behavioral-infer` on a schedule.

---

## Project Structure

```
behavioral-auth/
├── config/                    - YAML configuration files
│   ├── config.yaml            - production defaults
│   └── config.dev.yaml        - development overrides
├── db/
│   ├── schema.sql             - DuckDB schema
│   └── migrations/            - incremental schema migrations
├── src/behavioral_auth/
│   ├── cli/                   - CLI entry points (argparse)
│   ├── collector/             - evdev capture + DuckDB writer (Linux)
│   ├── face/                  - OpenCV LBPH detector / recognizer / enroll / verify
│   ├── features/              - keystroke, mouse, context feature extraction
│   ├── inference/             - ONNX runtime, score fusion, decision engine
│   ├── models/                - Conv1D autoencoder (PyTorch) + ONNX export
│   ├── reporting/             - FAR/FRR metrics
│   └── training/              - dataset loader, threshold calculation, train loop
├── src/scripts/
│   ├── bootstrap_db.py        - initialise DuckDB schema
│   ├── fedora-install.sh      - Fedora/RHEL system installer
│   ├── ubuntu-install.sh      - Ubuntu/Debian system installer
│   ├── windows-install.ps1    - Windows PowerShell installer
│   └── timer-install.sh       - systemd feature-extraction timer (Linux)
├── systemd/                   - systemd unit files
├── requirements.txt           - Linux dependencies (includes evdev)
├── requirements-windows.txt   - Windows dependencies (no evdev)
└── tests/                     - pytest unit tests
```

---

## License

See [LICENSE](LICENSE).
