"""LBPH training and confidence-threshold calibration.

LBPH is trained with a single label, so `predict()` always answers "that's the
enrolled person" — the confidence value is the *only* thing that separates you
from a stranger. A hard-coded cut-off (the old confidence_threshold: 80.0) is
therefore a guess about a number that varies with your camera, your lighting
and your face. Calibrate it instead: train on 80 % of the crops, see how
confident the model is about the 20 % it has not seen, and put the cut-off a
margin above that.

This measures self-similarity, not the ability to reject a stranger. We have
no stranger to measure against. Face stays a corroborating signal.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
from loguru import logger

from behavioral_auth.config import Settings
from behavioral_auth.face.recognizer import FaceRecognizer

MARGIN = 1.25
MIN_FOR_CALIBRATION = 10


def calibrate_threshold(crops: list[np.ndarray], seed: int = 0) -> float | None:
    """Cut-off implied by held-out crops, or None if there are too few."""
    if len(crops) < MIN_FOR_CALIBRATION:
        return None

    rng = np.random.default_rng(seed)
    idx = rng.permutation(len(crops))
    n_hold = max(2, int(len(crops) * 0.2))
    hold_idx, train_idx = idx[:n_hold], idx[n_hold:]

    with tempfile.TemporaryDirectory() as tmp:
        probe = FaceRecognizer(str(Path(tmp) / 'probe.yml'))
        probe.train([crops[i] for i in train_idx])
        confidences = [probe.predict(crops[i])[1] for i in hold_idx]

    return float(np.percentile(confidences, 95) * MARGIN)


def train_and_calibrate(crops: list[np.ndarray], cfg: Settings) -> dict | None:
    """Train the real LBPH model on every crop and write its metadata.

    Blocking; run in a worker thread.
    """
    if len(crops) < MIN_FOR_CALIBRATION:
        return None

    threshold = calibrate_threshold(crops)
    if threshold is None:
        return None

    # A manual confidence_threshold in config always wins over calibration.
    configured = cfg.face.confidence_threshold
    if isinstance(configured, (int, float)):
        threshold = float(configured)

    recognizer = FaceRecognizer(cfg.face.model_path)
    recognizer.train(crops)

    meta = {'threshold': threshold, 'n_samples': len(crops),
            'calibrated': not isinstance(configured, (int, float))}
    p = Path(cfg.face.meta_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(meta, indent=2))

    logger.info(f'Face model trained on {len(crops)} crops, threshold={threshold:.1f}')
    return meta


def load_face_meta(cfg: Settings) -> dict | None:
    p = Path(cfg.face.meta_path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None
