"""The promotion gates — the part that decides whether a pattern can be trusted."""

from __future__ import annotations

import numpy as np
import pytest

from behavioral_auth.training.promotion import (
    evaluate_cycle, make_synthetic_negatives, temporal_split, volume_gates,
)
from behavioral_auth.training.thresholds import (
    calibrate_from_holdout, false_alarm_rate, shape_ratio,
)
from tests.conftest import make_sequences


def test_temporal_split_drops_an_embargo_gap():
    """Sequences adjacent to the holdout overlap it and must not be trained on."""
    X = make_sequences(100, seq_len=4)
    train, hold = temporal_split(X, holdout_frac=0.2, embargo=4)

    assert len(hold) == 20
    assert len(train) == 100 - 20 - 4      # the embargo is really dropped
    # And the split is chronological, not shuffled.
    np.testing.assert_array_equal(hold, X[-20:])
    np.testing.assert_array_equal(train, X[:76])


def test_temporal_split_refuses_when_too_small():
    train, hold = temporal_split(make_sequences(5), holdout_frac=0.2, embargo=4)
    assert len(train) == 0 and len(hold) == 0


def test_synthetic_negatives_actually_differ():
    hold = make_sequences(30, seq_len=8)
    negs = make_synthetic_negatives(hold)

    assert set(negs) == {'typing_fast', 'typing_slow', 'mouse_fast', 'shuffled', 'spliced'}
    for name, neg in negs.items():
        assert neg.shape == hold.shape
        assert not np.allclose(neg, hold), f'{name} is identical to the real data'


def test_shuffled_negative_preserves_the_marginals():
    """The shuffled impostor keeps every value and only reorders time — which is
    what makes it a trap for a model that has learned nothing but the means."""
    hold = make_sequences(20, seq_len=8)
    shuffled = make_synthetic_negatives(hold)['shuffled']
    for i in range(len(hold)):
        np.testing.assert_allclose(np.sort(hold[i], axis=0), np.sort(shuffled[i], axis=0))


def test_a_blind_model_is_rejected(cfg):
    """A model that scores impostors exactly like the owner must not be promoted.

    This is the degenerate identity-map case: it converges beautifully, its
    holdout error is low and stable, and it would never fire on anyone.
    """
    err_train = np.full(100, 1.0)
    err_hold = np.full(50, 1.0)
    threshold = calibrate_from_holdout(err_hold)
    blind = {name: np.full(50, 1.0) for name in ('typing_fast', 'shuffled')}

    result = evaluate_cycle(err_train, err_hold, blind, threshold, None, cfg)

    assert not result.stable
    assert any('detects no synthetic impostor' in r for r in result.reasons)


def test_a_discriminating_model_passes(cfg):
    rng = np.random.default_rng(0)
    err_train = rng.lognormal(0, 0.2, 400)
    err_hold = rng.lognormal(0, 0.2, 100)
    threshold = calibrate_from_holdout(err_hold)
    caught = {
        'typing_fast': np.full(100, threshold * 8),    # far over the line
        'shuffled': np.full(100, threshold * 0.5),     # blind to this one
    }

    result = evaluate_cycle(err_train, err_hold, caught, threshold, None, cfg)

    assert result.stable, result.reasons
    assert result.detection == 1.0            # best generator, fully caught
    assert 'shuffled' in result.blind_to      # ...and the blind spot is reported


def test_a_noisy_threshold_is_rejected(cfg):
    """If the threshold would flag the owner constantly, the pattern is not ready."""
    rng = np.random.default_rng(1)
    err_train = rng.lognormal(0, 0.2, 200)
    err_hold = rng.lognormal(0, 0.2, 100)
    over_eager = float(np.percentile(err_hold, 50))     # flags half the owner's data
    synth = {'typing_fast': np.full(100, over_eager * 100)}

    result = evaluate_cycle(err_train, err_hold, synth, over_eager, None, cfg)

    assert not result.stable
    assert any('your own behaviour' in r for r in result.reasons)


def test_threshold_is_robust_to_outliers():
    """A tail percentile would lurch; the log-MAD estimator should not."""
    rng = np.random.default_rng(2)
    base = rng.lognormal(0, 0.3, 300)
    spiked = np.concatenate([base, [base.max() * 50]])   # one wild outlier

    assert calibrate_from_holdout(spiked) == pytest.approx(
        calibrate_from_holdout(base), rel=0.15)


def test_shape_ratio_is_scale_free():
    """Doubling every error must not move the shape statistic — otherwise the
    drift gate measures optimiser noise instead of pattern convergence."""
    errors = make_sequences(1, 1, 1).ravel() + np.linspace(0.5, 2.0, 1)
    errors = np.random.default_rng(3).lognormal(0, 0.3, 200)
    thr = calibrate_from_holdout(errors)
    assert shape_ratio(errors, thr) == pytest.approx(
        shape_ratio(errors * 7.0, calibrate_from_holdout(errors * 7.0)), rel=1e-6)


def test_false_alarm_rate_counts_the_owner():
    errors = np.array([1.0, 2.0, 3.0, 100.0])
    assert false_alarm_rate(errors, threshold=10.0) == 0.25


def test_volume_gates_report_what_is_missing(cfg):
    unmet = volume_gates(n_sequences=10, active_minutes=1.0, distinct_hours=1, cfg=cfg)
    assert any('sequences' in u for u in unmet)
    assert any('active_minutes' in u for u in unmet)

    assert volume_gates(
        n_sequences=99_999, active_minutes=9_999, distinct_hours=24, cfg=cfg) == []
