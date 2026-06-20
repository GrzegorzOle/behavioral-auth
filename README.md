# behavioral-auth

A local-only **behavioural authentication** stack for Linux (and Windows via OpenCV).
It continuously profiles your typing and mouse patterns, detects anomalies with an
ONNX autoencoder, and optionally adds face verification via OpenCV LBPH or Linux howdy.

---

## Architecture

```
keyboard / mouse
      │
      ▼
 behavioral-collector          ← evdev event capture (Linux)
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

| Dependency | Version |
|------------|---------|
| Python | 3.11+ |
| PyTorch | ≥ 2.4 |
| ONNX Runtime | ≥ 1.19 |
| OpenCV (contrib) | ≥ 4.10 |
| DuckDB | ≥ 0.10 |
| evdev | ≥ 1.7 (Linux only) |

---

## Installation

```bash
# 1. Create virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# 2. Install system dependencies (Fedora)
sudo bash src/scripts/fedora-install.sh

# 3. Install Python dependencies
pip install -r requirements.txt
pip install -e .

# 4. Initialise database
python src/scripts/bootstrap_db.py
```

---

## Quick Start

### Step 1 – Collect data (min. 15 minutes of normal use)

```bash
sg input -c "behavioral-collector"
# Press Ctrl+C to stop
```

### Step 2 – Extract features

```bash
behavioral-features
```

### Step 3 – Train the model

```bash
CUDA_VISIBLE_DEVICES="" behavioral-train
```

### Step 4 – Live verification

```bash
behavioral-verify --duration 120 --no-face
```

### Step 5 – Enroll face (optional)

```bash
behavioral-face enroll
```

---

## CLI Reference

| Command | Description |
|---------|-------------|
| `behavioral-collector` | Capture keyboard/mouse events to DuckDB |
| `behavioral-features` | Extract feature windows and sequences |
| `behavioral-train` | Train the ONNX autoencoder |
| `behavioral-infer` | Run one inference cycle |
| `behavioral-infer --loop` | Continuous inference loop |
| `behavioral-verify` | Live dashboard: collect + score |
| `behavioral-face enroll` | Capture face samples and train LBPH model |
| `behavioral-face verify` | One-shot face verification |
| `behavioral-face info` | Show face model status |
| `behavioral-face delete` | Remove trained face model |
| `behavioral-status` | Full pipeline status report |
| `behavioral-report` | Print decision metrics (FAR/FRR) |

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

**LBPH confidence scale:**

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
source .venv/bin/activate
pytest tests/ -v
```

---

## systemd Services (Linux)

```bash
# User-level services
systemctl --user daemon-reload
systemctl --user enable --now behavioral-collector.service
systemctl --user enable --now behavioral-inference.service

# System-level feature timer
sudo bash src/scripts/timer-install.sh
```

---

## Project Structure

```
src/behavioral_auth/
├── cli/           - CLI entry points (argparse)
├── collector/     - evdev capture + DuckDB writer
├── face/          - OpenCV LBPH detector / recognizer / enroll / verify
├── features/      - keystroke, mouse, context feature extraction
├── inference/     - ONNX runtime, score fusion, decision engine
├── models/        - Conv1D autoencoder (PyTorch) + ONNX export
├── reporting/     - FAR/FRR metrics
└── training/      - dataset loader, threshold calculation, train loop
```

---

## License

See [LICENSE](LICENSE).
