"""The scaler — where a subtle bug used to guarantee false alarms."""

from __future__ import annotations

import numpy as np

from behavioral_auth.features.scaler import apply_scaler, fit_scaler


def test_a_constant_feature_cannot_explode_the_error():
    """A feature that never varies during enrolment (is_weekend, if you enrol on
    weekdays) used to get std ~1e-6. The first Saturday, the deviation was
    multiplied by a million and the reconstruction error went to the moon — an
    intruder alert triggered by the calendar. The floor makes that impossible.
    """
    X = np.ones((50, 4, 3), dtype=np.float32)
    X[:, :, 0] = np.random.default_rng(0).normal(5.0, 2.0, (50, 4))
    X[:, :, 1] = 0.0     # dead constant

    scaler = fit_scaler(X, std_floor=1e-3)

    assert scaler['constant'] == [False, True, True]
    assert min(scaler['std']) >= 1e-3

    deviant = X[:1].copy()
    deviant[:, :, 1] = 1.0                       # the weekend arrives
    scaled = apply_scaler(deviant, scaler)
    assert np.abs(scaled).max() <= 1_100         # bounded, not 1e6


def test_scaler_round_trips(tmp_path):
    from behavioral_auth.features.scaler import load_scaler, save_scaler

    X = np.random.default_rng(1).lognormal(0, 0.4, (30, 4, 5)).astype(np.float32)
    scaler = fit_scaler(X)
    path = tmp_path / 'scaler.json'
    save_scaler(scaler, str(path))

    assert load_scaler(str(path)) == scaler


def test_scaling_centres_the_training_data():
    X = np.random.default_rng(2).normal(10.0, 3.0, (200, 4, 2)).astype(np.float32)
    scaled = apply_scaler(X, fit_scaler(X))

    assert abs(float(scaled.mean())) < 0.05
    assert abs(float(scaled.std()) - 1.0) < 0.05
