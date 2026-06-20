import torch
import torch.nn as nn

class Encoder(nn.Module):
    def __init__(self, input_dim, hidden=24, layers=3, kernel=3, dropout=0.1):
        super().__init__()
        mods = []
        in_ch = input_dim
        for _ in range(layers):
            mods += [nn.Conv1d(in_ch, hidden, kernel, padding=kernel // 2), nn.ReLU(), nn.Dropout(dropout)]
            in_ch = hidden
        self.net = nn.Sequential(*mods)
        self.head = nn.Sequential(nn.AdaptiveAvgPool1d(1), nn.Flatten(), nn.Linear(hidden, input_dim))
    def forward(self, x):
        return self.head(self.net(x))
