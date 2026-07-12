"""Camera access.

The camera is opened, used and released for every job. Holding VideoCapture
open would lock the device against every other application on the desktop for
as long as the daemon runs, which is not an acceptable price for a check that
happens every 30 seconds.

These functions block; call them from a worker thread. cv2's read() releases
the GIL, so they do not stall the event loop.
"""

from __future__ import annotations

import cv2
from loguru import logger

WARMUP_FRAMES = 3   # the first frames off a webcam are usually dark or green


def grab_frames(camera_index: int, n: int = 8) -> list:
    """Open the camera, discard warm-up frames, return up to *n* frames."""
    cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        logger.debug(f'Camera {camera_index} unavailable')
        return []
    try:
        for _ in range(WARMUP_FRAMES):
            cap.read()
        frames = []
        for _ in range(n):
            ok, frame = cap.read()
            if ok:
                frames.append(frame)
        return frames
    finally:
        cap.release()
