"""Silent background face sampling during LEARNING.

Capturing face crops with nobody watching the screen is easy to get wrong. The
detector happily returns "the largest face in the frame", which during a
normal working day will sooner or later be a colleague leaning over your
shoulder, a motion-blurred smear, or your face in the dark. Any of those, once
enrolled, permanently weakens the pattern — and nobody would ever notice.

So every crop must earn its place: exactly one face in frame, big enough,
sharp enough, lit sensibly, and — once a provisional model exists — recognised
as the person already being enrolled.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from loguru import logger

from behavioral_auth.config import Settings
from behavioral_auth.face.camera import grab_frames
from behavioral_auth.face.detector import FaceDetector
from behavioral_auth.face.recognizer import ENROLLED_LABEL, FaceRecognizer


@dataclass
class Sample:
    crop: np.ndarray
    width: int
    sharpness: float
    brightness: float
    self_confidence: float | None


def _sharpness(crop: np.ndarray) -> float:
    """Variance of the Laplacian: low means blurred."""
    return float(cv2.Laplacian(crop, cv2.CV_64F).var())


def capture(cfg: Settings, recognizer: FaceRecognizer | None) -> list[Sample]:
    """Grab frames and return the crops that pass every quality gate.

    Blocking; run in a worker thread.
    """
    detector = FaceDetector()
    accepted: list[Sample] = []

    for frame in grab_frames(cfg.face.camera_index, n=10):
        faces = detector.detect_all(frame)
        if len(faces) != 1:
            continue  # nobody, or someone else in shot as well
        rect, crop = detector.largest_face(frame)
        if crop is None:
            continue

        width = int(rect[2])
        if width < cfg.face.min_face_width:
            continue
        sharp = _sharpness(crop)
        if sharp < cfg.face.min_sharpness:
            continue
        bright = float(np.mean(crop))
        if not (cfg.face.min_brightness <= bright <= cfg.face.max_brightness):
            continue

        self_conf = None
        if recognizer is not None and recognizer.is_trained:
            label, conf = recognizer.predict(crop)
            self_conf = float(conf)
            if label != ENROLLED_LABEL or conf > cfg.face.self_confidence_max:
                logger.debug(f'Rejected crop: not the enrolled person (conf={conf:.0f})')
                continue

        accepted.append(Sample(crop, width, sharp, bright, self_conf))

    return accepted


def save_sample(sample: Sample, cfg: Settings, enrollment_id: str) -> str | None:
    """Persist a crop under the enrollment's private sample directory."""
    if not cfg.face.keep_samples:
        return None
    d = Path(cfg.face.samples_dir) / enrollment_id
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = d / f'{uuid.uuid4().hex}.png'
    cv2.imwrite(str(path), sample.crop)
    path.chmod(0o600)
    return str(path)


def load_samples(cfg: Settings, enrollment_id: str) -> list[np.ndarray]:
    """Read back every stored crop for this enrollment."""
    d = Path(cfg.face.samples_dir) / enrollment_id
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob('*.png')):
        img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        if img is not None:
            out.append(img)
    return out


def delete_samples(cfg: Settings, enrollment_id: str) -> int:
    d = Path(cfg.face.samples_dir) / enrollment_id
    if not d.is_dir():
        return 0
    n = 0
    for p in d.glob('*.png'):
        p.unlink(missing_ok=True)
        n += 1
    d.rmdir()
    return n
