"""Tests for the OpenCV face recognition module."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_face_images(n: int = 10, seed: int = 42) -> list:
    """Generate synthetic 150×150 grayscale images (uint8)."""
    rng = np.random.default_rng(seed)
    return [rng.integers(0, 255, (150, 150), dtype=np.uint8) for _ in range(n)]


# ---------------------------------------------------------------------------
# FaceRecognizer
# ---------------------------------------------------------------------------

class TestFaceRecognizer:
    def test_untrained_state(self, tmp_path):
        from behavioral_auth.face.recognizer import FaceRecognizer
        rec = FaceRecognizer(str(tmp_path / "model.yml"))
        assert not rec.is_trained
        label, conf = rec.predict(_make_face_images(1)[0])
        assert label == -1
        assert conf == 999.0

    def test_train_and_predict_known(self, tmp_path):
        from behavioral_auth.face.recognizer import FaceRecognizer, ENROLLED_LABEL
        rec = FaceRecognizer(str(tmp_path / "model.yml"))
        faces = _make_face_images(15)
        rec.train(faces)
        assert rec.is_trained
        label, conf = rec.predict(faces[0])
        assert label == ENROLLED_LABEL
        assert conf < 50.0  # training sample should be very close

    def test_model_persisted(self, tmp_path):
        from behavioral_auth.face.recognizer import FaceRecognizer
        model_path = str(tmp_path / "model.yml")
        rec = FaceRecognizer(model_path)
        rec.train(_make_face_images(10))
        # Load fresh instance from disk
        rec2 = FaceRecognizer(model_path)
        assert rec2.is_trained

    def test_update_incremental(self, tmp_path):
        from behavioral_auth.face.recognizer import FaceRecognizer, ENROLLED_LABEL
        rec = FaceRecognizer(str(tmp_path / "model.yml"))
        rec.train(_make_face_images(10, seed=1))
        rec.update(_make_face_images(5, seed=2))
        assert rec.is_trained

    def test_delete(self, tmp_path):
        from behavioral_auth.face.recognizer import FaceRecognizer
        model_path = str(tmp_path / "model.yml")
        rec = FaceRecognizer(model_path)
        rec.train(_make_face_images(10))
        assert Path(model_path).exists()
        rec.delete()
        assert not Path(model_path).exists()
        assert not rec.is_trained

    def test_info_trained(self, tmp_path):
        from behavioral_auth.face.recognizer import FaceRecognizer
        rec = FaceRecognizer(str(tmp_path / "model.yml"))
        rec.train(_make_face_images(10))
        info = rec.info()
        assert info["trained"] is True
        assert "model_path" in info
        assert info["model_size_kb"] > 0

    def test_info_untrained(self, tmp_path):
        from behavioral_auth.face.recognizer import FaceRecognizer
        rec = FaceRecognizer(str(tmp_path / "model.yml"))
        assert rec.info() == {"trained": False}


# ---------------------------------------------------------------------------
# FaceDetector
# ---------------------------------------------------------------------------

class TestFaceDetector:
    def test_cascade_loads(self):
        from behavioral_auth.face.detector import FaceDetector
        det = FaceDetector()
        assert not det.cascade.empty()

    def test_no_face_in_blank_frame(self):
        from behavioral_auth.face.detector import FaceDetector
        det = FaceDetector()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        rect, crop = det.largest_face(frame)
        assert rect is None
        assert crop is None

    def test_detect_all_returns_list(self):
        from behavioral_auth.face.detector import FaceDetector
        det = FaceDetector()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        result = det.detect_all(frame)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# opencv_face_score (verify) – no camera needed
# ---------------------------------------------------------------------------

class TestOpencvFaceScore:
    def test_returns_neutral_when_no_model(self, tmp_path):
        from behavioral_auth.face.verify import opencv_face_score
        score = opencv_face_score(
            model_path=str(tmp_path / "nonexistent.yml"),
        )
        assert score == 0.5  # neutral – no model yet

    def test_returns_fail_when_camera_unavailable(self, tmp_path):
        """Use a non-existent camera index to simulate unavailable camera."""
        from behavioral_auth.face.recognizer import FaceRecognizer
        from behavioral_auth.face.verify import opencv_face_score

        model_path = str(tmp_path / "model.yml")
        rec = FaceRecognizer(model_path)
        rec.train(_make_face_images(10))

        # Camera index 99 should not exist on any reasonable system
        score = opencv_face_score(
            model_path=model_path,
            camera_index=99,
            fail_score=0.85,
        )
        assert score == 0.85

