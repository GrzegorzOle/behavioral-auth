"""Face recognition: Haar-cascade detection + LBPH, any USB or built-in camera.

Enrolment happens silently in the background while the daemon is LEARNING;
there is no interactive enroll step. Note that LBPH is trained with a single
label, so it can only ever answer "how confident am I that this is the enrolled
person" — the calibrated confidence cut-off is the whole of the decision.
"""
