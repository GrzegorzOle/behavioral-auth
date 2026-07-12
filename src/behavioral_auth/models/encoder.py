"""Conv1D autoencoder with a bottleneck.

The sequence is squeezed through a latent vector far smaller than the input
and then rebuilt in full. Reconstruction error is the anomaly score: low for
behaviour that lies on the manifold the model learned, high for behaviour that
does not.

The bottleneck is the entire point, and getting this wrong is subtle. An
earlier version fed the model the whole sequence and asked it to reproduce
only the *last* feature vector — which was itself part of the input. That is
an identity map with extra steps: the network learns "copy what you were
shown", scores a low and perfectly stable error for any human being alive, and
never fires on an intruder. It converged beautifully and detected nothing.

So: reconstruct everything, and force it through `latent` numbers. To rebuild
216 inputs from 8 latents the model has no choice but to learn what this
particular person's typing and pointing actually look like.
"""

from __future__ import annotations

import torch
import torch.nn as nn


class Encoder(nn.Module):
    """Bottlenecked Conv1D autoencoder over a sequence of feature vectors.

    Args:
        input_dim: Features per timestep.
        hidden:    Channels in each convolutional block.
        layers:    Number of Conv1D blocks per side.
        kernel:    Convolution kernel size.
        dropout:   Dropout after each ReLU.
        seq_len:   Timesteps per sequence (baked in: the decoder rebuilds it).
        latent:    Bottleneck width. Must stay far below input_dim * seq_len.
    """

    def __init__(self, input_dim: int, hidden: int = 24, layers: int = 3,
                 kernel: int = 3, dropout: float = 0.1, seq_len: int = 12,
                 latent: int = 8) -> None:
        super().__init__()
        self.seq_len = seq_len
        self.hidden = hidden

        enc: list[nn.Module] = []
        in_ch = input_dim
        for _ in range(layers):
            enc += [nn.Conv1d(in_ch, hidden, kernel, padding=kernel // 2),
                    nn.ReLU(),
                    nn.Dropout(dropout)]
            in_ch = hidden
        self.encode = nn.Sequential(*enc)
        # Flatten, not average-pool. Pooling over time makes the encoder
        # permutation-invariant by construction — it literally cannot perceive
        # the order of the windows, so a sequence with the user's values in a
        # scrambled order encodes identically to the real thing. Flattening
        # keeps the temporal structure inside the bottleneck.
        self.to_latent = nn.Sequential(
            nn.Flatten(),
            nn.Linear(hidden * seq_len, latent),
        )

        self.from_latent = nn.Linear(latent, hidden * seq_len)
        dec: list[nn.Module] = []
        for _ in range(layers - 1):
            dec += [nn.Conv1d(hidden, hidden, kernel, padding=kernel // 2),
                    nn.ReLU(),
                    nn.Dropout(dropout)]
        dec += [nn.Conv1d(hidden, input_dim, kernel, padding=kernel // 2)]
        self.decode = nn.Sequential(*dec)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """(batch, input_dim, seq_len) -> reconstruction of the same shape."""
        z = self.to_latent(self.encode(x))
        h = self.from_latent(z).view(-1, self.hidden, self.seq_len)
        return self.decode(h)
