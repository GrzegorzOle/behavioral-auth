"""Face enrollment: capture samples from camera, train LBPH model."""

from __future__ import annotations

import time

import cv2
from loguru import logger

from behavioral_auth.face.gui import ensure_display
from behavioral_auth.face.detector import FaceDetector
from behavioral_auth.face.recognizer import FaceRecognizer


def enroll_face(
    model_path: str,
    camera_index: int = 0,
    n_samples: int = 40,
    show_preview: bool = True,
    update: bool = False,
) -> bool:
    """Capture *n_samples* face images from *camera_index* and (re-)train the model.

    Args:
        model_path:    Path to save the LBPH model (.yml).
        camera_index:  OpenCV camera index (0 = default/first camera).
        n_samples:     How many face crops to collect before training.
        show_preview:  Show live camera window (requires GUI).
        update:        If True, incrementally update existing model instead of
                       retraining from scratch.

    Returns:
        True on success, False on failure / cancellation.
    """
    detector = FaceDetector()
    recognizer = FaceRecognizer(model_path)

    if show_preview:
        ensure_display()

    cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        logger.error(f"Cannot open camera index {camera_index}")
        return False

    collected: list = []
    logger.info(f"Starting enrollment – collecting {n_samples} samples.  "
                "Look at the camera.  Press ESC to cancel.")

    try:
        while len(collected) < n_samples:
            ret, frame = cap.read()
            if not ret:
                logger.warning("Failed to read frame – retrying…")
                time.sleep(0.05)
                continue

            rect, crop = detector.largest_face(frame)

            if show_preview:
                display = frame.copy()
                progress = len(collected)
                if rect is not None:
                    detector.draw_rect(display, rect, (0, 220, 0))
                    label = f"Samples: {progress}/{n_samples}"
                    color = (0, 220, 0)
                else:
                    label = "No face detected"
                    color = (0, 60, 255)
                cv2.putText(display, label, (10, 32),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2)
                cv2.imshow("Behavioral-Auth – Face Enrollment", display)
                key = cv2.waitKey(1) & 0xFF
                if key == 27:  # ESC
                    logger.info("Enrollment cancelled by user (ESC)")
                    return False

            if crop is not None:
                collected.append(crop)
                time.sleep(0.08)  # slight delay to get varied poses

        logger.info(f"Collected {len(collected)} samples – training model…")
        if update and recognizer.is_trained:
            recognizer.update(collected)
            logger.success(f"Model updated → {model_path}")
        else:
            recognizer.train(collected)
            logger.success(f"Model trained → {model_path}")
        return True

    except KeyboardInterrupt:
        logger.info("Enrollment interrupted")
        return False
    finally:
        cap.release()
        if show_preview:
            cv2.destroyAllWindows()

