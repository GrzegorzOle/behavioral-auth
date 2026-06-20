"""Score fusion: combine behavioral anomaly score with face verification score.

Both scores use the same semantic: low = normal/known user, high = anomaly.
"""

from __future__ import annotations

from behavioral_auth.config import load_settings


def fuse_scores(behavioral: float, face: float) -> float:
    """Weighted average of behavioral and face scores.

    Args:
        behavioral: Reconstruction-error anomaly score from the ONNX model.
        face:       Face verification score (howdy or OpenCV LBPH).

    Returns:
        Fused anomaly score in [0, 1].
    """
    cfg = load_settings()
    return cfg.fusion.behavioral_weight * behavioral + cfg.fusion.howdy_weight * face
