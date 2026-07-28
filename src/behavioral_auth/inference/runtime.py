"""ONNX scoring runtime.

Loads the frozen pattern (model + scaler + metadata) and turns stored
sequences into reconstruction errors. The InferenceSession is cached: it used
to be rebuilt on every single score, which cost 50-200 ms a tick for nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import onnxruntime as ort
from loguru import logger

from behavioral_auth.collector.stack import key_matches
from behavioral_auth.config import Settings
from behavioral_auth.features.pipeline import to_model_input
from behavioral_auth.features.scaler import apply_scaler, load_scaler

_SESSIONS: dict[tuple[str, int], ort.InferenceSession] = {}


def _session(model_path: str) -> ort.InferenceSession:
    key = (model_path, Path(model_path).stat().st_mtime_ns)
    if key not in _SESSIONS:
        _SESSIONS.clear()  # a new model supersedes the old one
        _SESSIONS[key] = ort.InferenceSession(
            model_path, providers=['CPUExecutionProvider'])
    return _SESSIONS[key]


class PatternMismatch(RuntimeError):
    """The stored model does not match the current feature configuration."""


@dataclass
class Pattern:
    """A trained, frozen pattern: the thing MONITORING scores against."""
    model_path: str
    scaler: dict
    meta: dict

    @property
    def threshold(self) -> float:
        return float(self.meta['threshold'])

    @property
    def enrollment_id(self) -> str | None:
        return self.meta.get('enrollment_id')

    @property
    def stacks(self) -> list[str]:
        """Hardware stacks this pattern was trained on.

        Empty for a pattern promoted before stacks were recorded. Empty means
        "unknown", and an unknown enrolment gates nothing — an upgrade must not
        retroactively suspend a working pattern.
        """
        return list(self.meta.get('stacks') or [])

    def accepts_stack(self, key: str | None) -> bool:
        """May a sequence from stack *key* be scored against this pattern?"""
        if not self.stacks or key is None:
            return True
        return key_matches(key, self.stacks)

    def errors(self, X: np.ndarray) -> np.ndarray:
        """Reconstruction error per sequence. *X* is (n, seq_len, 21) as stored."""
        if len(X) == 0:
            return np.empty(0, dtype=np.float32)
        Xm = apply_scaler(to_model_input(X).astype(np.float32), self.scaler)
        inputs = np.transpose(Xm, (0, 2, 1))  # (n, features, seq_len)
        recon = _session(self.model_path).run(['recon'], {'input': inputs})[0]
        return np.mean((recon - inputs) ** 2, axis=(1, 2))

    def ratios(self, X: np.ndarray) -> np.ndarray:
        """Error relative to the calibrated threshold: >1 means anomalous."""
        thr = self.threshold
        return self.errors(X) / thr if thr > 0 else np.zeros(len(X))


def load_pattern(cfg: Settings) -> Pattern | None:
    """Load the frozen pattern, or None if this machine has not trained one.

    Raises PatternMismatch when the artifacts exist but were built for a
    different sequence length — ONNX freezes seq_len at export, so scoring
    anyway would silently produce garbage.
    """
    model_path = Path(cfg.model.model_path)
    meta_path = Path(cfg.model.metadata_path)
    scaler_path = Path(cfg.features.scaler_path)
    if not (model_path.exists() and meta_path.exists() and scaler_path.exists()):
        return None

    meta = json.loads(meta_path.read_text())
    if meta.get('seq_len') != cfg.model.seq_len:
        raise PatternMismatch(
            f"model was trained with seq_len={meta.get('seq_len')} but config "
            f"says {cfg.model.seq_len} — retrain (behavioral-auth reset) or "
            f"restore the old setting"
        )
    if meta.get('input_dim') != cfg.model.input_dim:
        raise PatternMismatch(
            f"model expects input_dim={meta.get('input_dim')} but the feature "
            f"configuration yields {cfg.model.input_dim}"
        )

    logger.debug(f'Loaded pattern: threshold={meta.get("threshold")}')
    return Pattern(str(model_path), load_scaler(str(scaler_path)), meta)
