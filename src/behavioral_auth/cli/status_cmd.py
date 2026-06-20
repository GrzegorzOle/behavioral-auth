"""behavioral-status – show current pipeline state at a glance.

Prints a structured summary of every pipeline stage:
  Step 1 – Data collection  (raw events)
  Step 2 – Feature extraction (windows + fused sequences)
  Step 3 – Model training    (ONNX autoencoder)
  Step 4 – Inference         (anomaly decisions)
  Step 5 – Face verification (OpenCV LBPH)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _bar(value: int, max_value: int, width: int = 20) -> str:
    """Return a Unicode progress bar string."""
    if max_value <= 0:
        return "─" * width
    filled = int(min(value / max_value, 1.0) * width)
    return "█" * filled + "░" * (width - filled)


def main() -> None:  # noqa: C901
    from behavioral_auth.config import load_settings
    import duckdb

    cfg = load_settings()

    print("\n╔══════════════════════════════════════════╗")
    print("║      behavioral-auth  •  pipeline status      ║")
    print("╚══════════════════════════════════════════╝\n")

    # ── 1. Config ───────────────────────────────────────────────────────────
    print(f"  Config          : {cfg.general.mode.upper()} mode")
    print(f"  Data dir        : {cfg.general.data_dir}")
    print(f"  DB path         : {cfg.storage.db_path}")

    db_path = Path(cfg.storage.db_path)
    if not db_path.exists():
        print("\n  ❌  Database not found – run:  make schema\n")
        sys.exit(1)

    db_size_mb = round(db_path.stat().st_size / 1024 / 1024, 2)
    print(f"  DB size         : {db_size_mb} MB")

    conn = duckdb.connect(cfg.storage.db_path, read_only=True)

    # ── 2. Sessions ─────────────────────────────────────────────────────────
    n_sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    last_session = conn.execute(
        "SELECT session_id, started_at FROM sessions ORDER BY started_at DESC LIMIT 1"
    ).fetchone()

    print(f"\n  {'STEP 1 – Data collection':─<42}")
    print(f"  Sessions        : {n_sessions}")
    if last_session:
        print(f"  Last session    : {str(last_session[0])[:8]}…  @  {last_session[1]}")

    n_raw = conn.execute("SELECT COUNT(*) FROM raw_events").fetchone()[0]
    print(f"  Raw events      : {n_raw:,}")
    status1 = "✅" if n_raw > 1000 else ("⚠️ " if n_raw > 0 else "❌")
    print(f"  Status          : {status1}  {'OK' if n_raw > 1000 else 'need more data (min ~1000 events)'}")

    # ── 3. Feature windows ──────────────────────────────────────────────────
    n_windows = conn.execute("SELECT COUNT(*) FROM feature_windows").fetchone()[0]
    n_sequences = conn.execute("SELECT COUNT(*) FROM fused_sequences").fetchone()[0]
    needed_seq = cfg.model.seq_len * 5  # minimum sensible training set

    print(f"\n  {'STEP 2 – Feature extraction':─<42}")
    print(f"  Feature windows : {n_windows:,}")
    print(f"  Fused sequences : {n_sequences:,}  {_bar(n_sequences, needed_seq)}")
    print(f"  Required (min)  : ~{needed_seq} sequences for training")
    status2 = "✅" if n_sequences >= needed_seq else ("⚠️ " if n_sequences > 0 else "❌")
    print(f"  Status          : {status2}  {'OK' if n_sequences >= needed_seq else f'too few ({n_sequences}/{needed_seq})'}")

    # ── 4. Model ────────────────────────────────────────────────────────────
    model_path = Path(cfg.model.model_path)
    meta_path = Path(cfg.model.metadata_path)
    scaler_path = Path(cfg.features.scaler_path)

    print(f"\n  {'STEP 3 – Model training':─<42}")
    if model_path.exists() and meta_path.exists():
        meta = json.loads(meta_path.read_text())
        model_kb = round(model_path.stat().st_size / 1024, 1)
        n_versions = conn.execute("SELECT COUNT(*) FROM model_registry").fetchone()[0]
        print(f"  Model ONNX      : ✅  {model_kb} KB  (v{n_versions})")
        print(f"  Scaler          : {'✅' if scaler_path.exists() else '❌'}")
        print(f"  Train samples   : {meta.get('train_samples', '?')}")
        print(f"  Val samples     : {meta.get('val_samples', '?')}")
        print(f"  Val MSE mean    : {meta.get('val_mean_error', 0):.6f}")
        print(f"  Challenge thr.  : {meta.get('challenge_threshold', '?'):.4f}")
        print(f"  Lock thr.       : {meta.get('lock_threshold', '?'):.4f}")
        status3 = "✅"
    else:
        print(f"  Model ONNX      : ❌  {cfg.model.model_path}")
        print(f"  Scaler          : {'✅' if scaler_path.exists() else '❌'}")
        status3 = "❌"
    print(f"  Status          : {status3}  {'ready' if status3 == '✅' else 'run: behavioral-train'}")

    # ── 5. Inference / Decisions ────────────────────────────────────────────
    n_decisions = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    print(f"\n  {'STEP 4 – Inference':─<42}")
    print(f"  Decisions in DB : {n_decisions:,}")
    if n_decisions > 0:
        last_d = conn.execute(
            "SELECT behavioral_score, fused_score, decision, ts_utc FROM decisions ORDER BY ts_utc DESC LIMIT 1"
        ).fetchone()
        print(f"  Last decision   : score={last_d[1]:.4f}  [{last_d[2]}]  @  {last_d[3]}")
        dist = conn.execute(
            "SELECT decision, COUNT(*) FROM decisions GROUP BY decision ORDER BY COUNT(*) DESC"
        ).fetchall()
        print(f"  Decision dist.  : " + "  ".join(f"{r[0]}={r[1]}" for r in dist))

    # ── 6. Face ─────────────────────────────────────────────────────────────
    face_model = Path(cfg.face.model_path)
    print(f"\n  {'STEP 5 – Face verification (OpenCV)':─<42}")
    print(f"  Enabled         : {'✅' if cfg.face.enabled else '❌'}")
    print(f"  Backend         : {cfg.face.backend}")
    print(f"  Model           : {'✅  ' + str(round(face_model.stat().st_size/1024/1024,1)) + ' MB' if face_model.exists() else '❌  not enrolled – run: behavioral-face enroll'}")

    conn.close()

    # ── 7. Next steps ────────────────────────────────────────────────────────
    print(f"\n  {'Next steps':─<42}")
    steps = []
    if n_raw == 0:
        steps.append("  1.  behavioral-collector          ← collect keyboard/mouse data")
    if n_sequences < needed_seq:
        steps.append("  2.  behavioral-features           ← extract features from raw events")
    if status3 == "❌":
        steps.append("  3.  behavioral-train              ← train the autoencoder model")
    if status3 == "✅":
        steps.append("  4.  behavioral-infer              ← run one inference cycle")
        steps.append("      behavioral-infer --loop        ← continuous inference loop")
    if not steps:
        steps.append("  ✅  Pipeline fully operational!")
    for s in steps:
        print(s)

    print()
