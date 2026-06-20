"""Training entry point for the behavioural autoencoder.

Pipeline:
  1. Load all fused_sequences from DuckDB.
  2. Fit and save a per-feature z-score scaler.
  3. Train a Conv1D autoencoder (Encoder) using MSE loss.
  4. Compute validation reconstruction errors → derive thresholds.
  5. Export model to ONNX and write metadata JSON.
  6. Register the new model version in model_registry.
"""

from pathlib import Path
import json, os
import duckdb, numpy as np, torch, torch.nn as nn
from sklearn.model_selection import train_test_split
from behavioral_auth.config import load_settings
from behavioral_auth.models.encoder import Encoder
from behavioral_auth.models.onnx_export import export_onnx
from behavioral_auth.features.scaler import fit_and_save_scaler, apply_scaler
from behavioral_auth.training.dataset import load_training_dataset
from behavioral_auth.training.thresholds import calculate_thresholds

def train() -> None:
    """Run the full training pipeline and export the ONNX model."""
    cfg = load_settings()
    conn = duckdb.connect(cfg.storage.db_path)
    X = load_training_dataset(conn)
    mean, std = fit_and_save_scaler(X, cfg.features.scaler_path)
    X = apply_scaler(X, mean, std)
    y = X[:, -1, :]
    Xtr, Xva, ytr, yva = train_test_split(X, y, test_size=cfg.model.val_split, random_state=42)
    device = torch.device('cuda' if torch.cuda.is_available() and not os.environ.get('CUDA_VISIBLE_DEVICES', '') == '' else 'cpu')
    if device.type == 'cuda':
        # Verify CUDA compute capability is supported
        try:
            torch.zeros(1, device=device)
        except Exception:
            device = torch.device('cpu')
    print(f'Training device: {device}')
    Xtr = torch.tensor(Xtr).permute(0,2,1).to(device); ytr = torch.tensor(ytr).to(device)
    Xva = torch.tensor(Xva).permute(0,2,1).to(device); yva = torch.tensor(yva).to(device)
    model = Encoder(cfg.model.input_dim, cfg.model.hidden_dim, cfg.model.num_layers, cfg.model.kernel_size, cfg.model.dropout).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=cfg.model.lr); loss_fn = nn.MSELoss()
    for epoch in range(cfg.model.epochs):
        model.train(); perm = torch.randperm(Xtr.size(0), device=device); total = 0.0
        for i in range(0, Xtr.size(0), cfg.model.batch_size):
            idx = perm[i:i+cfg.model.batch_size]; xb, yb = Xtr[idx], ytr[idx]
            opt.zero_grad(); pred = model(xb); loss = loss_fn(pred, yb); loss.backward(); opt.step(); total += float(loss.item()) * len(idx)
        model.eval()
        with torch.no_grad(): val_loss = float(loss_fn(model(Xva), yva).item())
        print({'epoch': epoch + 1, 'train_loss': total / Xtr.size(0), 'val_loss': val_loss})
    with torch.no_grad():
        va_err = ((model(Xva) - yva) ** 2).mean(dim=1).detach().cpu().numpy()
    challenge, lock = calculate_thresholds(va_err)
    export_onnx(model, cfg.model.model_path, cfg.model.input_dim, cfg.model.seq_len, device)
    meta = {'input_dim': cfg.model.input_dim, 'seq_len': cfg.model.seq_len, 'challenge_threshold': challenge, 'lock_threshold': lock, 'val_mean_error': float(va_err.mean()), 'train_samples': int(Xtr.size(0)), 'val_samples': int(Xva.size(0))}
    Path(cfg.model.metadata_path).write_text(json.dumps(meta, indent=2))
    version = conn.execute('SELECT COALESCE(MAX(version),0)+1 FROM model_registry').fetchone()[0]
    conn.execute('INSERT INTO model_registry (version, model_path, scaler_path, threshold_challenge, threshold_lock, metrics_json, notes) VALUES (?, ?, ?, ?, ?, ?, ?)', [version, cfg.model.model_path, cfg.features.scaler_path, challenge, lock, json.dumps(meta), 'autoencoder-structured'])
    print(json.dumps(meta, indent=2))
