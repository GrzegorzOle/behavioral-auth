"""LBPH face recognizer wrapper.

Stores a trained model as a YAML file on disk.  Confidence interpretation:
  0–40   → excellent match
  40–80  → good match
  80–100 → borderline
  >100   → no match / unknown person

Lower confidence = more similar to enrolled face.
"""

from __future__ import annotations

import cv2
import numpy as np
from pathlib import Path
from loguru import logger


# Label used for the enrolled (legitimate) user
ENROLLED_LABEL = 0


class FaceRecognizer:
    """Thin wrapper around OpenCV LBPHFaceRecognizer."""

    def __init__(self, model_path: str) -> None:
        self.model_path = Path(model_path)
        self._rec = cv2.face.LBPHFaceRecognizer_create(
            radius=1, neighbors=8, grid_x=8, grid_y=8, threshold=200.0
        )
        self._trained = False
        if self.model_path.exists():
            try:
                self._rec.read(str(self.model_path))
                self._trained = True
                logger.debug(f"Loaded face model from {self.model_path}")
            except Exception as exc:
                logger.warning(f"Could not load face model: {exc}")

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def train(self, face_crops: list[np.ndarray], labels: list[int] | None = None) -> None:
        """Train from scratch on *face_crops* (grayscale 150×150 images)."""
        if labels is None:
            labels = [ENROLLED_LABEL] * len(face_crops)
        self._rec.train(face_crops, np.array(labels, dtype=np.int32))
        self._save()
        self._trained = True

    def update(self, face_crops: list[np.ndarray], labels: list[int] | None = None) -> None:
        """Update existing model with new samples (incremental training)."""
        if not self._trained:
            self.train(face_crops, labels)
            return
        if labels is None:
            labels = [ENROLLED_LABEL] * len(face_crops)
        self._rec.update(face_crops, np.array(labels, dtype=np.int32))
        self._save()

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------

    def predict(self, face_crop: np.ndarray) -> tuple[int, float]:
        """Return (label, confidence).  Lower confidence = better match."""
        if not self._trained:
            return -1, 999.0
        label, confidence = self._rec.predict(face_crop)
        return int(label), float(confidence)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save(self) -> None:
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        self._rec.save(str(self.model_path))
        logger.debug(f"Face model saved → {self.model_path}")

    def delete(self) -> None:
        """Remove trained model from disk."""
        if self.model_path.exists():
            self.model_path.unlink()
            self._trained = False
            logger.info(f"Deleted face model {self.model_path}")

    # ------------------------------------------------------------------
    # Info
    # ------------------------------------------------------------------

    @property
    def is_trained(self) -> bool:
        return self._trained

    def info(self) -> dict:
        """Return model metadata if available."""
        if not self._trained:
            return {"trained": False}
        return {
            "trained": True,
            "model_path": str(self.model_path),
            "model_size_kb": round(self.model_path.stat().st_size / 1024, 1),
        }

