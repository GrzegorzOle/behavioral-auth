"""Face verification during MONITORING.

Returns a FaceState, never a score. The distinction that matters is between
"the camera saw someone else" (STRANGER — evidence) and "the camera told us
nothing" (UNKNOWN — no evidence). The old code collapsed both into a numeric
score and blended it into the decision, so a dark room or a camera held by
another app quietly pushed the system toward an alarm.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from behavioral_auth.config import Settings
from behavioral_auth.face.calibrate import load_face_meta
from behavioral_auth.face.camera import grab_frames
from behavioral_auth.face.detector import FaceDetector
from behavioral_auth.face.recognizer import ENROLLED_LABEL, FaceRecognizer
from behavioral_auth.inference.fusion import FaceState


def check(cfg: Settings, max_frames: int = 8) -> tuple[FaceState, float | None]:
    """One face check. Blocking; run in a worker thread.

    Returns (state, confidence). Confidence is None when no face was seen.
    """
    if not cfg.face.enabled:
        return FaceState.UNKNOWN, None

    if cfg.face.backend == 'howdy':
        from behavioral_auth.inference.howdy import howdy_state
        return howdy_state(cfg), None

    if not Path(cfg.face.model_path).exists():
        return FaceState.UNKNOWN, None

    meta = load_face_meta(cfg)
    if not meta:
        return FaceState.UNKNOWN, None
    threshold = float(meta['threshold'])

    recognizer = FaceRecognizer(cfg.face.model_path)
    if not recognizer.is_trained:
        return FaceState.UNKNOWN, None

    detector = FaceDetector()
    for frame in grab_frames(cfg.face.camera_index, n=max_frames):
        _, crop = detector.largest_face(frame)
        if crop is None:
            continue
        label, confidence = recognizer.predict(crop)
        if label == ENROLLED_LABEL and confidence <= threshold:
            logger.debug(f'Face match (conf={confidence:.0f} <= {threshold:.0f})')
            return FaceState.MATCH, float(confidence)
        logger.info(f'Face does NOT match (conf={confidence:.0f} > {threshold:.0f})')
        return FaceState.STRANGER, float(confidence)

    return FaceState.UNKNOWN, None   # nobody in front of the camera
