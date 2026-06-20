import torch

def export_onnx(model, model_path: str, input_dim: int, seq_len: int, device):
    dummy = torch.randn(1, input_dim, seq_len, device=device)
    torch.onnx.export(model, dummy, model_path, input_names=['input'], output_names=['recon'], dynamic_axes={'input': {0: 'batch'}, 'recon': {0: 'batch'}}, opset_version=17)
