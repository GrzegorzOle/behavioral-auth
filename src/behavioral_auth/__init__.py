"""behavioral-auth – local-only behavioural authentication library.

Modules:
  collector  – evdev-based keyboard/mouse event capture
  features   – feature extraction pipeline (keystroke, mouse, context)
  models     – Conv1D autoencoder (PyTorch + ONNX export)
  training   – dataset loading, scaler, threshold calculation, train loop
  inference  – ONNX runtime, score fusion, anomaly decision engine
  face       – OpenCV LBPH face enrollment and verification
  reporting  – FAR/FRR metrics
  cli        – argparse entry points for all commands
"""

__all__ = []
