"""Face verification via OpenCV camera.

Returns an *anomaly score* in the same semantic as howdy_score():
  low score  → face recognised  → user is legitimate
  high score → no match / error → treat as anomaly
"""

from __future__ import annotations

import time
from pathlib import Path

import cv2
from loguru import logger

from behavioral_auth.face.gui import ensure_display
from behavioral_auth.face.detector import FaceDetector
from behavioral_auth.face.recognizer import ENROLLED_LABEL, FaceRecognizer


def opencv_face_score(
    model_path: str,
    camera_index: int = 0,
    confidence_threshold: float = 80.0,
    success_score: float = 0.05,
    fail_score: float = 0.85,
    max_attempts: int = 8,
    show_preview: bool = False,
) -> float:
    """Capture a frame, detect a face, run LBPH predict.

    Args:
        model_path:           Path to trained LBPH model (.yml).
        camera_index:         OpenCV camera index.
        confidence_threshold: LBPH confidence cut-off (lower = stricter).
                              Predictions ≤ threshold → match.
        success_score:        Anomaly score when face is recognised.
        fail_score:           Anomaly score when face is NOT recognised or
                              on any error.
        max_attempts:         How many frames to try before giving up.
        show_preview:         Show live window during verification.

    Returns:
        Float anomaly score.
    """
    if not Path(model_path).exists():
        logger.warning("Face model not found – run 'behavioral-face enroll' first")
        return 0.5  # neutral: no model yet, don't penalise

    detector = FaceDetector()
    recognizer = FaceRecognizer(model_path)

    if not recognizer.is_trained:
        logger.warning("Face model exists but appears empty")
        return 0.5

    if show_preview:
        ensure_display()

    cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        logger.warning(f"Cannot open camera {camera_index}")
        return fail_score

    try:
        for attempt in range(max_attempts):
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.05)
                continue

            rect, crop = detector.largest_face(frame)

            if show_preview:
                display = frame.copy()
                if rect is not None:
                    detector.draw_rect(display, rect)
                cv2.putText(display, f"Verifying… attempt {attempt+1}/{max_attempts}",
                            (10, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 200, 255), 2)
                cv2.imshow("Behavioral-Auth – Face Verify", display)
                cv2.waitKey(1)

            if crop is None:
                continue  # no face in this frame – try again

            label, confidence = recognizer.predict(crop)
            logger.debug(f"Face predict: label={label}, confidence={confidence:.1f}")

            if label == ENROLLED_LABEL and confidence <= confidence_threshold:
                logger.info(f"Face recognised  (conf={confidence:.1f} ≤ {confidence_threshold})")
                return success_score
            else:
                logger.info(f"Face NOT recognised (conf={confidence:.1f}, label={label})")
                return fail_score

        logger.info("No face detected after all attempts")
        return fail_score

    finally:
        cap.release()
        if show_preview:
            cv2.destroyAllWindows()

