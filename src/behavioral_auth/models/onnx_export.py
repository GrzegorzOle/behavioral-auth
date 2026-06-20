"""ONNX export helper for the behavioural autoencoder."""

import torch


def export_onnx(model, model_path: str, input_dim: int, seq_len: int, device) -> None:
    """Export *model* to ONNX format at *model_path*.

    Uses a dummy input of shape (1, input_dim, seq_len) to trace the graph.
    Dynamic batch axis is enabled so the exported model accepts any batch size.

    Args:
        model:      Trained Encoder instance (in eval mode recommended).
        model_path: Destination file path for the .onnx file.
        input_dim:  Number of input features.
        seq_len:    Sequence length.
        device:     Torch device used to create the dummy tensor.
    """
    dummy = torch.randn(1, input_dim, seq_len, device=device)
    torch.onnx.export(
        model, dummy, model_path,
        input_names=['input'],
        output_names=['recon'],
        dynamic_axes={'input': {0: 'batch'}, 'recon': {0: 'batch'}},
        opset_version=17,
    )
