"""Autoencoder fitting.

Pure functions: no database, no printing, no file writes. The caller (the
learning controller) owns the data, the split and the artifacts — which is
what lets this run inside a worker thread while the daemon keeps collecting.
"""

from __future__ import annotations

import numpy as np
import torch
import torch.nn as nn

from behavioral_auth.config import Settings
from behavioral_auth.models.encoder import Encoder


def resolve_device(cfg: Settings) -> torch.device:
    want = cfg.model.device
    if want == 'cuda' or (want == 'auto' and torch.cuda.is_available()):
        try:
            torch.zeros(1, device='cuda')
            return torch.device('cuda')
        except Exception:
            pass
    return torch.device('cpu')


def _as_batch(X: np.ndarray, device: torch.device) -> torch.Tensor:
    """(n, seq_len, features) -> (n, features, seq_len), the Conv1d layout."""
    return torch.tensor(X, dtype=torch.float32).permute(0, 2, 1).to(device)


def build_model(cfg: Settings) -> Encoder:
    return Encoder(
        cfg.model.input_dim, cfg.model.hidden_dim, cfg.model.num_layers,
        cfg.model.kernel_size, cfg.model.dropout,
        seq_len=cfg.model.seq_len, latent=cfg.model.latent_dim,
    )


def fit(X: np.ndarray, cfg: Settings, device: torch.device | None = None) -> Encoder:
    """Train an Encoder to reconstruct whole sequences through the bottleneck.

    *X* must already be scaled and must contain only the model's input columns.

    The seed is fixed on purpose. Each learning cycle trains a fresh model, and
    the promotion gate asks "has the pattern stopped changing?" — a question
    about the *data*. With a random initialisation each cycle, two models fitted
    to near-identical data land at different minima with differently shaped
    error distributions, and the gate reads that optimiser noise as an unstable
    pattern. Pinning the seed makes cycle-to-cycle differences mean what the
    gate thinks they mean.
    """
    device = device or resolve_device(cfg)
    torch.manual_seed(cfg.model.seed)
    inputs = _as_batch(X, device)      # the input is also the target

    model = build_model(cfg).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=cfg.model.lr)
    loss_fn = nn.MSELoss()

    n = inputs.size(0)
    for _ in range(cfg.model.epochs):
        model.train()
        perm = torch.randperm(n, device=device)
        for i in range(0, n, cfg.model.batch_size):
            batch = inputs[perm[i:i + cfg.model.batch_size]]
            opt.zero_grad()
            loss = loss_fn(model(batch), batch)
            loss.backward()
            opt.step()

    model.eval()
    return model


@torch.no_grad()
def reconstruction_errors(model: Encoder, X: np.ndarray,
                          device: torch.device | None = None) -> np.ndarray:
    """Per-sequence MSE over the whole reconstructed sequence."""
    if len(X) == 0:
        return np.empty(0, dtype=np.float32)
    device = device or next(model.parameters()).device
    model.eval()
    inputs = _as_batch(X, device)
    err = ((model(inputs) - inputs) ** 2).mean(dim=(1, 2))
    return err.detach().cpu().numpy()
