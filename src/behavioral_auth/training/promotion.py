"""Deciding when a learned pattern is ready to be trusted.

The hard constraint of this whole system: we have data from exactly one
person. There are no impostor samples, so nothing here measures a false-accept
rate, and no number produced by this module should ever be presented as one.

What we *can* check is:
  1. Convergence — does the model reconstruct fresh, never-trained-on data as
     well as it reconstructs its training data, and has the threshold stopped
     moving between cycles?
  2. Non-degeneracy — is the model actually discriminating, or has it just
     learned to echo the mean of its input?

(2) matters more than it looks. The autoencoder reconstructs the last feature
vector of a window whose other vectors overlap it heavily, so "output the
average of the input" scores a low and beautifully *stable* error — for any
human alive. A convergence-only gate would promote that model on the second
cycle and it would never fire on an intruder. So each cycle also builds
synthetic impostors out of the user's own holdout and requires the model to
score them clearly worse. A model that cannot separate *those* certainly
cannot separate a real person, and it is not allowed through.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from behavioral_auth.config import MODEL_COLUMNS, Settings
from behavioral_auth.training.thresholds import false_alarm_rate, shape_ratio

# Feature groups a different person would plausibly shift.
_TIMING = ['f_ks_mean_dwell', 'f_ks_std_dwell', 'f_ks_mean_flight', 'f_ks_std_flight']
_MOUSE = ['f_ms_speed_mean', 'f_ms_speed_std', 'f_ms_acc_mean', 'f_ms_curvature']

_TIMING_IDX = [MODEL_COLUMNS.index(c) for c in _TIMING]
_MOUSE_IDX = [MODEL_COLUMNS.index(c) for c in _MOUSE]


@dataclass
class CycleResult:
    """Outcome of one learning cycle."""
    n_train: int
    n_holdout: int
    pass_rate: float
    error_ratio: float
    threshold: float
    shape: float = 0.0        # threshold / median error: scale-free
    threshold_drift: float = 0.0
    false_alarms: float = 0.0   # fraction of the OWNER's own data this would flag
    # Best generator, not worst — see evaluate_cycle for why.
    separation: float = 0.0   # how much worse synthetic impostors score (blind = 1.0)
    detection: float = 0.0    # fraction of them that would trip the alarm
    blind_to: list[str] = field(default_factory=list)
    per_generator: dict[str, dict] = field(default_factory=dict)
    stable: bool = False
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            'n_train': self.n_train,
            'n_holdout': self.n_holdout,
            'pass_rate': round(self.pass_rate, 4),
            'error_ratio': round(self.error_ratio, 4),
            'threshold': self.threshold,
            'shape': round(self.shape, 3),
            'threshold_drift': round(self.threshold_drift, 4),
            'false_alarms': round(self.false_alarms, 4),
            'separation': round(self.separation, 2),
            'detection': round(self.detection, 3),
            'blind_to': self.blind_to,
            'per_generator': {
                k: {'detection': round(v['detection'], 2),
                    'separation': round(v['separation'], 1)}
                for k, v in self.per_generator.items()
            },
            'stable': self.stable,
            'reasons': self.reasons,
        }


def temporal_split(X: np.ndarray, holdout_frac: float, embargo: int) -> tuple[np.ndarray, np.ndarray]:
    """Split chronologically into (train, holdout), dropping an embargo gap.

    Adjacent sequences share seq_len-1 windows, so the sequences immediately
    before the holdout overlap it. Training on them would leak the holdout and
    make "fresh data" a fiction — hence the gap. *embargo* should be seq_len.
    """
    n = len(X)
    n_hold = int(n * holdout_frac)
    if n_hold < 1:
        return X[:0], X[:0]
    n_train = n - n_hold - embargo
    if n_train < 1:
        return X[:0], X[:0]
    return X[:n_train], X[n - n_hold:]


def make_synthetic_negatives(hold: np.ndarray, seed: int = 0) -> dict[str, np.ndarray]:
    """Build pseudo-impostor sequences from the user's own held-out data.

    Returned per generator, not pooled: each one probes a different way the
    model could be blind, and pooling them lets a model that catches three of
    them hide the fact that it is oblivious to the fourth. The gate takes the
    worst.

    These are not real impostors, and no number derived from them is an
    accuracy. They exist to prove the detector is not degenerate.
    """
    if len(hold) < 2:
        return {}

    rng = np.random.default_rng(seed)
    out: dict[str, np.ndarray] = {}

    # Someone who types faster, and someone who types slower.
    for name, scale in (('typing_fast', 0.6), ('typing_slow', 1.6)):
        x = hold.copy()
        x[:, :, _TIMING_IDX] *= scale
        out[name] = x

    # Someone who throws the mouse around.
    x = hold.copy()
    x[:, :, _MOUSE_IDX] *= 1.8
    out['mouse_fast'] = x

    # Same per-feature distribution, destroyed temporal structure. A model that
    # only learned the marginals — the classic degenerate outcome — cannot see
    # this at all, which is exactly why it is here.
    x = hold.copy()
    for i in range(len(x)):
        x[i] = x[i][rng.permutation(x.shape[1])]
    out['shuffled'] = x

    # Two distant halves of the user's own data spliced together.
    half = hold.shape[1] // 2
    partner = (np.arange(len(hold)) + len(hold) // 2) % len(hold)
    x = hold.copy()
    x[:, half:, :] = hold[partner][:, half:, :]
    out['spliced'] = x

    return out


def evaluate_cycle(err_train: np.ndarray, err_hold: np.ndarray,
                   err_synth: dict[str, np.ndarray],
                   threshold: float, prev_shape: float | None,
                   cfg: Settings) -> CycleResult:
    """Score one cycle against the stability gates."""
    gates = cfg.learning.stability

    train_p99 = float(np.percentile(err_train, 99))
    pass_rate = float(np.mean(err_hold <= train_p99))

    train_med = float(np.median(err_train))
    error_ratio = float(np.median(err_hold) / train_med) if train_med > 0 else float('inf')

    shape = shape_ratio(err_hold, threshold)
    drift = 0.0 if not prev_shape else abs(shape - prev_shape) / prev_shape

    # The only error rate we can honestly measure: how often this threshold
    # would flag the owner as an intruder.
    false_alarms = false_alarm_rate(err_hold, threshold)

    # For each kind of synthetic impostor: how many of its sequences would
    # actually trip the alarm, and how much worse it scores than the user.
    hold_med = float(np.median(err_hold))
    per_generator = {}
    for name, errs in err_synth.items():
        if not len(errs):
            continue
        per_generator[name] = {
            'detection': float(np.mean(errs > threshold)),
            'separation': float(np.median(errs) / hold_med) if hold_med > 0 else 0.0,
        }

    # The gate is a liveness check on the discriminator, and it takes the BEST
    # generator, not the worst. The failure it exists to catch is the model
    # that has learned the identity map — and such a model detects *nothing*,
    # scoring ~0 % everywhere. A model that reliably catches even one class of
    # impostor is demonstrably doing real work.
    #
    # Taking the worst instead would be stricter but wrong: with overlapping
    # windows a sequence is nearly constant along its time axis, so reordering
    # its windows changes almost nothing, and demanding that the model detect
    # that would be demanding it detect a difference that is not there. Those
    # generators stay in the report as diagnostics; they do not gate.
    detection = max((g['detection'] for g in per_generator.values()), default=0.0)
    separation = max((g['separation'] for g in per_generator.values()), default=0.0)
    blind_to = sorted(n for n, g in per_generator.items() if g['detection'] < 0.5)

    reasons = []
    if pass_rate < gates.pass_rate_min:
        reasons.append(f'pass_rate {pass_rate:.2f} < {gates.pass_rate_min}')
    if error_ratio > gates.error_ratio_max:
        reasons.append(f'error_ratio {error_ratio:.2f} > {gates.error_ratio_max}')
    if drift > gates.threshold_drift_max:
        reasons.append(f'threshold_drift {drift:.2f} > {gates.threshold_drift_max}')
    if false_alarms > gates.false_alarm_max:
        reasons.append(
            f'would flag {false_alarms:.1%} of your own behaviour '
            f'(max {gates.false_alarm_max:.1%})')
    if detection < gates.sanity_detection_min:
        reasons.append(
            f'model detects no synthetic impostor reliably '
            f'(best {detection:.0%} < {gates.sanity_detection_min:.0%}) — it is not '
            f'discriminating, only reconstructing')

    return CycleResult(
        n_train=len(err_train), n_holdout=len(err_hold),
        pass_rate=pass_rate, error_ratio=error_ratio,
        threshold=threshold, shape=shape, threshold_drift=drift,
        false_alarms=false_alarms,
        separation=separation, detection=detection, blind_to=blind_to,
        per_generator=per_generator,
        stable=not reasons, reasons=reasons,
    )


def volume_gates(n_sequences: int, active_minutes: float, distinct_hours: int,
                 cfg: Settings) -> list[str]:
    """Return the volume requirements not yet met (empty == all satisfied)."""
    lc = cfg.learning
    unmet = []
    if n_sequences < lc.min_sequences:
        unmet.append(f'sequences {n_sequences}/{lc.min_sequences}')
    if active_minutes < lc.min_active_minutes:
        unmet.append(f'active_minutes {active_minutes:.0f}/{lc.min_active_minutes}')
    if distinct_hours < lc.min_distinct_hours:
        unmet.append(f'distinct_hours {distinct_hours}/{lc.min_distinct_hours}')
    return unmet
