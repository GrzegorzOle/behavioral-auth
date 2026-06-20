"""Face detector using OpenCV Haar cascade.

Works cross-platform with any camera.
"""

from __future__ import annotations

import cv2
import numpy as np


# Built-in cascade shipped with OpenCV – no external file needed
_CASCADE_FRONTAL = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
_CASCADE_PROFILE  = cv2.data.haarcascades + "haarcascade_profileface.xml"

# Target size for face crops fed to the recognizer
FACE_SIZE = (150, 150)


class FaceDetector:
    """Detect faces in BGR frames using Haar cascade."""

    def __init__(
        self,
        scale_factor: float = 1.1,
        min_neighbors: int = 5,
        min_size: tuple[int, int] = (60, 60),
    ) -> None:
        self.cascade = cv2.CascadeClassifier(_CASCADE_FRONTAL)
        if self.cascade.empty():
            raise RuntimeError(f"Cannot load cascade from {_CASCADE_FRONTAL}")
        self.scale_factor = scale_factor
        self.min_neighbors = min_neighbors
        self.min_size = min_size

    def detect_all(self, frame: np.ndarray) -> list[tuple[int, int, int, int]]:
        """Return list of (x, y, w, h) for all detected faces."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        faces = self.cascade.detectMultiScale(
            gray,
            scaleFactor=self.scale_factor,
            minNeighbors=self.min_neighbors,
            minSize=self.min_size,
            flags=cv2.CASCADE_SCALE_IMAGE,
        )
        return faces.tolist() if len(faces) > 0 else []

    def largest_face(
        self, frame: np.ndarray
    ) -> tuple[tuple[int, int, int, int] | None, np.ndarray | None]:
        """Return (rect, gray_crop) for the largest detected face, or (None, None)."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        faces = self.detect_all(frame)
        if not faces:
            return None, None
        rect = max(faces, key=lambda f: f[2] * f[3])
        x, y, w, h = rect
        crop = gray[y : y + h, x : x + w]
        crop = cv2.resize(crop, FACE_SIZE)
        return rect, crop

    @staticmethod
    def draw_rect(
        frame: np.ndarray,
        rect: tuple[int, int, int, int],
        color: tuple[int, int, int] = (0, 255, 0),
        thickness: int = 2,
    ) -> None:
        x, y, w, h = rect
        cv2.rectangle(frame, (x, y), (x + w, y + h), color, thickness)

