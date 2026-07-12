"""ONNX export.

Returns bytes rather than writing a file: the export runs in a worker thread,
and only the loop thread is allowed to touch artifacts on disk — so the model
comes back as data and is installed atomically once the caller has decided it
is good enough to promote.

The batch axis is dynamic but seq_len is baked into the graph. The runtime
refuses to score a model whose seq_len disagrees with the configuration; the
alternative is silently reconstructing nonsense.
"""

from __future__ import annotations

import io

import torch


def export_onnx(model, input_dim: int, seq_len: int,
                device: torch.device | None = None) -> bytes:
    """Serialise *model* to ONNX and return the raw bytes."""
    device = device or next(model.parameters()).device
    dummy = torch.randn(1, input_dim, seq_len, device=device)
    buf = io.BytesIO()
    model.eval()
    torch.onnx.export(
        model, dummy, buf,
        input_names=['input'],
        output_names=['recon'],
        dynamic_axes={'input': {0: 'batch'}, 'recon': {0: 'batch'}},
        opset_version=17,
    )
    return buf.getvalue()
