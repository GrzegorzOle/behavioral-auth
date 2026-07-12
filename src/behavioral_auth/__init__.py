"""behavioral-auth — local-only continuous behavioural authentication.

The daemon learns how one person types and moves the mouse, freezes that
pattern, and then warns when the behaviour at the keyboard stops matching it.
It never locks the session and never logs anyone out.

Modules:
  daemon     – state machine (LEARNING → MONITORING → ALARM), control channel
  collector  – evdev capture, plus a synthetic source for testing
  features   – incremental window and sequence extraction
  models     – Conv1D autoencoder (PyTorch → ONNX)
  training   – dataset scoping, fitting, promotion gates
  inference  – ONNX scoring, behavioural/face channel rules
  face       – silent LBPH enrolment and verification
  reporting  – what was observed (no FAR/FRR: there is no impostor data)
  db         – DuckDB access and schema migrations
"""

__all__ = []
