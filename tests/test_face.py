"""Face recognition.

The thing to keep in mind throughout: LBPH is trained with a single label, so
predict() *always* answers "that's the enrolled person". Only the confidence
value separates you from a stranger, which is why the cut-off is calibrated
rather than guessed, and why an unrecognised frame is UNKNOWN rather than
evidence of an intruder.
"""

from __future__ import annotations

import numpy as np

from behavioral_auth.face.calibrate import calibrate_threshold
from behavioral_auth.face.detector import FaceDetector
from behavioral_auth.face.recognizer import ENROLLED_LABEL, FaceRecognizer
from behavioral_auth.inference.fusion import FaceState


def _faces(n: int, seed: int = 0) -> list[np.ndarray]:
    rng = np.random.default_rng(seed)
    base = rng.integers(60, 190, (150, 150), dtype=np.uint8)
    return [np.clip(base.astype(int) + rng.integers(-12, 12, (150, 150)),
                    0, 255).astype(np.uint8) for _ in range(n)]


class TestFaceRecognizer:
    def test_untrained_recognises_nobody(self, tmp_path):
        rec = FaceRecognizer(str(tmp_path / 'm.yml'))
        assert not rec.is_trained
        assert rec.predict(_faces(1)[0]) == (-1, 999.0)

    def test_training_persists_to_disk(self, tmp_path):
        path = tmp_path / 'm.yml'
        rec = FaceRecognizer(str(path))
        rec.train(_faces(15))

        assert rec.is_trained and path.exists()
        assert FaceRecognizer(str(path)).is_trained      # reloads

    def test_recognises_the_enrolled_person(self, tmp_path):
        faces = _faces(15)
        rec = FaceRecognizer(str(tmp_path / 'm.yml'))
        rec.train(faces)

        label, confidence = rec.predict(faces[0])
        assert label == ENROLLED_LABEL
        assert confidence < 50.0

    def test_delete_removes_the_pattern(self, tmp_path):
        path = tmp_path / 'm.yml'
        rec = FaceRecognizer(str(path))
        rec.train(_faces(12))
        rec.delete()

        assert not rec.is_trained and not path.exists()


class TestCalibration:
    def test_threshold_comes_from_held_out_crops(self):
        threshold = calibrate_threshold(_faces(40))
        assert threshold is not None and threshold > 0

    def test_too_few_crops_yield_no_threshold(self):
        """Better no face channel at all than one calibrated on four photos."""
        assert calibrate_threshold(_faces(4)) is None


class TestDetector:
    def test_a_blank_frame_holds_no_face(self):
        blank = np.zeros((480, 640, 3), dtype=np.uint8)
        assert FaceDetector().largest_face(blank) == (None, None)


class TestVerify:
    def test_no_model_means_unknown_not_stranger(self, cfg):
        """With no pattern enrolled we know nothing — and knowing nothing must
        never read as 'an intruder is here'."""
        from behavioral_auth.face.verify import check

        cfg.face.enabled = True
        cfg.face.model_path = '/nonexistent/face.yml'
        state, confidence = check(cfg)

        assert state is FaceState.UNKNOWN
        assert confidence is None

    def test_disabled_face_is_unknown(self, cfg):
        from behavioral_auth.face.verify import check

        cfg.face.enabled = False
        assert check(cfg)[0] is FaceState.UNKNOWN
