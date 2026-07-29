"""The numpy <-> torch crossings in the training path.

Nothing else in the suite calls `fit` or `reconstruction_errors`, so the two
places where a numpy array becomes a tensor and comes back were untested on
every OS. That gap shipped a real defect: the Windows `torch==2.4.0+cpu` wheel
is built against numpy 1.x, and under `numpy==2.0.1` the crossing *back* out
(`err.detach().cpu().numpy()`) raised `RuntimeError: Numpy is not available` —
so on Windows a learning cycle trained to completion and then died computing
its reconstruction errors, taking promotion with it. Linux stayed green, and
the Windows CI job (which runs only the port tests) never touched torch.

The inbound crossing survives that mismatch, which is why the round trip is
what has to be asserted: testing only `_as_batch` would have passed on a build
that cannot return an array. These tests are deliberately about the *interop*,
not about learning anything — they fail loudly when the pinned torch and numpy
cannot exchange arrays, which is the thing the pins can silently get wrong.
"""

import numpy as np

from behavioral_auth.training.train import (
    _as_batch,
    fit,
    reconstruction_errors,
    resolve_device,
)

from tests.conftest import make_sequences


def test_numpy_input_crosses_into_a_tensor_in_the_conv1d_layout(cfg):
    X = make_sequences(6, seq_len=cfg.model.seq_len,
                       n_features=cfg.model.input_dim)
    batch = _as_batch(X, resolve_device(cfg))
    # (n, seq_len, features) -> (n, features, seq_len)
    assert tuple(batch.shape) == (6, X.shape[2], cfg.model.seq_len)
    assert batch.dtype.is_floating_point


def test_a_fitted_model_returns_errors_as_a_numpy_array(cfg):
    X = make_sequences(8, seq_len=cfg.model.seq_len,
                       n_features=cfg.model.input_dim)
    model = fit(X, cfg)
    err = reconstruction_errors(model, X)

    assert isinstance(err, np.ndarray)
    assert err.shape == (8,)
    # A reconstruction error is a mean of squares; negatives or NaNs would mean
    # the array crossed back wrong rather than that the model trained badly.
    assert np.all(np.isfinite(err))
    assert np.all(err >= 0.0)


def test_no_sequences_still_yields_an_empty_numpy_array(cfg):
    model = fit(make_sequences(4, seq_len=cfg.model.seq_len,
                               n_features=cfg.model.input_dim), cfg)
    err = reconstruction_errors(
        model,
        np.empty((0, cfg.model.seq_len, cfg.model.input_dim), dtype=np.float32))
    assert isinstance(err, np.ndarray)
    assert err.shape == (0,)
