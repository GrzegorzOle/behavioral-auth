"""The LEARNING controller.

Runs a cycle whenever enough new material has accumulated: retrain on
everything except the newest slice, judge the model on that newest slice, and
count how many cycles in a row come out stable. When the streak is long
enough and there is enough data, the pattern is promoted and frozen.

Freezing is the whole point. After promotion nothing here runs again unless
the user explicitly asks for it (`reset` or `learn-more`), so a stranger
cannot gradually teach the system to accept them just by using the computer.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from loguru import logger

from behavioral_auth.config import MODEL_COLUMNS, Settings
from behavioral_auth.features.pipeline import to_model_input
from behavioral_auth.features.scaler import apply_scaler, fit_scaler, save_scaler
from behavioral_auth.models.onnx_export import export_onnx
from behavioral_auth.training import dataset
from behavioral_auth.training.promotion import (
    CycleResult, evaluate_cycle, make_synthetic_negatives, temporal_split, volume_gates,
)
from behavioral_auth.training.thresholds import calibrate_from_holdout
from behavioral_auth.training.train import fit, reconstruction_errors, resolve_device


@dataclass
class Artifacts:
    onnx: bytes
    scaler: dict
    meta: dict


def run_cycle_blocking(X: np.ndarray, cfg: Settings,
                       prev_shape: float | None) -> tuple[CycleResult, Artifacts] | None:
    """Train and judge one candidate model. Blocking; run in a worker thread.

    Returns None when there is not yet enough data to form a train/holdout
    split with an embargo between them.
    """
    train_X, hold_X = temporal_split(
        to_model_input(X), cfg.learning.holdout_frac, embargo=cfg.model.seq_len)
    if len(train_X) < 8 or len(hold_X) < 5:
        return None

    scaler = fit_scaler(train_X, cfg.features.std_floor)   # never fit on the holdout
    train_s = apply_scaler(train_X, scaler)
    hold_s = apply_scaler(hold_X, scaler)

    device = resolve_device(cfg)
    model = fit(train_s, cfg, device)

    err_train = reconstruction_errors(model, train_s, device)
    err_hold = reconstruction_errors(model, hold_s, device)
    threshold = calibrate_from_holdout(err_hold)

    err_synth = {
        name: reconstruction_errors(model, apply_scaler(neg, scaler), device)
        for name, neg in make_synthetic_negatives(hold_X).items()
    }

    result = evaluate_cycle(err_train, err_hold, err_synth, threshold, prev_shape, cfg)

    meta = {
        'seq_len': cfg.model.seq_len,
        'input_dim': cfg.model.input_dim,
        'feature_columns': MODEL_COLUMNS,
        'threshold': threshold,
        'hold_p50': float(np.median(err_hold)),
        'hold_p95': float(np.percentile(err_hold, 95)),
        'separation': result.separation,
        'false_alarm_rate': result.false_alarms,
        'synthetic_detection': result.detection,
        'blind_to': result.blind_to,
        'n_train': int(len(train_X)),
        'n_holdout': int(len(hold_X)),
    }
    onnx = export_onnx(model, cfg.model.input_dim, cfg.model.seq_len, device)
    return result, Artifacts(onnx=onnx, scaler=scaler, meta=meta)


def _install(path: str, data: bytes) -> None:
    """Write *data* to *path* atomically."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + '.tmp')
    tmp.write_bytes(data)
    os.replace(tmp, p)


class LearningController:
    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self.cycle_no = 0
        self.stable_streak = 0
        self.prev_shape: float | None = None
        self.last_cycle_at = 0.0
        self.seq_at_last_cycle = 0
        self.last_result: CycleResult | None = None
        self.blocked_by: list[str] = []
        self.face_ready = False

    def reset(self) -> None:
        self.cycle_no = 0
        self.stable_streak = 0
        self.prev_shape = None
        self.last_cycle_at = 0.0
        self.seq_at_last_cycle = 0
        self.last_result = None
        self.face_ready = False

    def next_cycle_in(self) -> int:
        remaining = self.cfg.learning.cycle_min_sec - (time.monotonic() - self.last_cycle_at)
        return max(0, int(remaining))

    def should_run_cycle(self, n_sequences: int) -> bool:
        if time.monotonic() - self.last_cycle_at < self.cfg.learning.cycle_min_sec:
            return False
        new = n_sequences - self.seq_at_last_cycle
        return new >= self.cfg.learning.cycle_min_new_sequences

    def gates(self, conn, enrollment_id: str, n_sequences: int) -> list[str]:
        """Everything still standing between us and promotion."""
        unmet = volume_gates(
            n_sequences,
            dataset.active_minutes(conn, enrollment_id, self.cfg.features.stride_sec),
            dataset.distinct_hours(conn, enrollment_id),
            self.cfg,
        )
        if self.cfg.face.enabled and self.cfg.face.required_for_promotion and not self.face_ready:
            unmet.append('face pattern not ready')
        need = self.cfg.learning.stable_consecutive_cycles
        if self.stable_streak < need:
            unmet.append(f'stable cycles {self.stable_streak}/{need}')
        self.blocked_by = unmet
        return unmet

    def record(self, conn, enrollment_id: str, result: CycleResult, promoted: bool) -> None:
        self.cycle_no += 1
        self.last_cycle_at = time.monotonic()
        self.last_result = result
        self.stable_streak = self.stable_streak + 1 if result.stable else 0
        self.prev_shape = result.shape

        conn.execute(
            'INSERT INTO learning_cycles (enrollment_id, cycle_no, n_train, n_holdout, '
            'pass_rate, error_ratio, threshold, threshold_drift, separation, stable, '
            'stable_streak, promoted, metrics_json) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            [enrollment_id, self.cycle_no, result.n_train, result.n_holdout,
             result.pass_rate, result.error_ratio, result.threshold,
             result.threshold_drift, result.separation, result.stable,
             self.stable_streak, promoted, json.dumps(result.as_dict())],
        )

        verdict = 'stable' if result.stable else f'unstable ({"; ".join(result.reasons)})'
        per_gen = '  '.join(f'{k}: {v["detection"]:.0%} @{v["separation"]:.1f}x'
                            for k, v in sorted(result.per_generator.items()))
        logger.info(
            f'Cycle {self.cycle_no}: {verdict} — pass_rate={result.pass_rate:.2f} '
            f'err_ratio={result.error_ratio:.2f} '
            f'fałszywe-alarmy={result.false_alarms:.1%} '
            f'wykrywalność-syntetyczna={result.detection:.0%} '
            f'streak={self.stable_streak}'
        )
        logger.debug(f'  by impostor type: {per_gen}')

    def promote(self, conn, enrollment_id: str, artifacts: Artifacts) -> None:
        """Install the pattern, freeze it, and say plainly what it does not mean."""
        _install(self.cfg.model.model_path, artifacts.onnx)
        save_scaler(artifacts.scaler, self.cfg.features.scaler_path)

        meta = dict(artifacts.meta, enrollment_id=enrollment_id)
        _install(self.cfg.model.metadata_path, json.dumps(meta, indent=2).encode())

        version = conn.execute(
            'SELECT COALESCE(MAX(version), 0) + 1 FROM model_registry').fetchone()[0]
        conn.execute(
            'INSERT INTO model_registry (version, model_path, scaler_path, '
            'threshold_challenge, threshold_lock, metrics_json, notes) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            [version, self.cfg.model.model_path, self.cfg.features.scaler_path,
             meta['threshold'], meta['threshold'], json.dumps(meta),
             f'enrollment {enrollment_id}'],
        )
        conn.execute(
            "UPDATE enrollments SET status = 'active' WHERE enrollment_id = ?",
            [enrollment_id])

        logger.success(
            f'Pattern promoted (v{version}): trained on {meta["n_train"]} sequences, '
            f'judged on {meta["n_holdout"]} it never saw, threshold={meta["threshold"]:.4f}. '
            f'The pattern is now FROZEN — it will not change until you run '
            f'"behavioral-auth reset" or "behavioral-auth learn-more".'
        )
        logger.warning(
            f'What this does NOT mean. No false-accept or false-reject rate has been '
            f'measured, because there is no impostor data to measure against — only your '
            f'own behaviour. The one thing that was verified is that the model is not a '
            f'degenerate copier: it flags {meta["synthetic_detection"]:.0%} of synthetic '
            f'impostors built by distorting your own data.'
            + (f' It is, however, blind to: {", ".join(meta["blind_to"])} — those '
               f'differences it would not notice.' if meta.get('blind_to') else '')
        )
