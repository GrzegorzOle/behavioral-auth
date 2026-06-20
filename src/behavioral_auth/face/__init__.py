"""OpenCV-based face recognition module.

Works cross-platform (Linux, Windows, macOS) with any USB/built-in camera.
Uses Haar cascade for detection and LBPH for recognition.
"""

from behavioral_auth.face.enroll import enroll_face
from behavioral_auth.face.verify import opencv_face_score

__all__ = ["enroll_face", "opencv_face_score"]

