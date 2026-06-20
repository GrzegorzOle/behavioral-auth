"""Conv1D autoencoder model.

Architecture: a stack of 1-D convolutional layers (encoder) followed by
an adaptive-average-pool head that reconstructs the last feature vector of
the input sequence.  The reconstruction error (MSE) serves as the anomaly
score: low error → normal behaviour, high error → anomaly.
"""

import torch
import torch.nn as nn


class Encoder(nn.Module):
    """Lightweight Conv1D autoencoder for behavioural anomaly detection.

    Args:
        input_dim: Number of input features (default 21).
        hidden:    Number of channels in each convolutional layer.
        layers:    Number of Conv1D blocks.
        kernel:    Convolution kernel size.
        dropout:   Dropout probability applied after each ReLU.
    """

    def __init__(self, input_dim: int, hidden: int = 24, layers: int = 3,
                 kernel: int = 3, dropout: float = 0.1) -> None:
        super().__init__()
        mods = []
        in_ch = input_dim
        for _ in range(layers):
            mods += [nn.Conv1d(in_ch, hidden, kernel, padding=kernel // 2),
                     nn.ReLU(),
                     nn.Dropout(dropout)]
            in_ch = hidden
        self.net = nn.Sequential(*mods)
        # Project back to input_dim: one value per feature
        self.head = nn.Sequential(
            nn.AdaptiveAvgPool1d(1),
            nn.Flatten(),
            nn.Linear(hidden, input_dim),
        )

    def forward(self, x: 'torch.Tensor') -> 'torch.Tensor':
        """Forward pass.

        Args:
            x: Tensor of shape (batch, input_dim, seq_len).

        Returns:
            Reconstructed last-step feature vector of shape (batch, input_dim).
        """
        return self.head(self.net(x))
