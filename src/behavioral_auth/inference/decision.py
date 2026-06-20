import json, subprocess, time, duckdb
from pathlib import Path
from behavioral_auth.config import load_settings
from behavioral_auth.inference.runtime import predict_behavioral
from behavioral_auth.inference.howdy import howdy_score
from behavioral_auth.inference.fusion import fuse_scores

_last_action = 0.0


def _load_model_meta(cfg) -> dict:
    """Return model_meta.json dict, or empty dict if not yet trained."""
    p = Path(cfg.model.metadata_path)
    if p.exists():
        return json.loads(p.read_text())
    return {}


def normalize_behavioral(score: float, meta: dict) -> float:
    """Map raw MSE reconstruction error to [0, 1] anomaly score.

    Uses the model's lock_threshold (99th percentile of val errors) as the
    upper reference: score / lock_threshold, capped at 1.0.
    That way a perfectly normal session yields ~0.17 and a strongly anomalous
    one yields 1.0.
    """
    lock = meta.get('lock_threshold', None)
    if lock and lock > 0:
        return min(score / lock, 1.0)
    # Fallback: no model metadata yet → pass through capped at 1.0
    return min(score, 1.0)


def get_face_score(cfg) -> float:
    """Return a face verification score using the configured backend.

    - backend 'opencv': use OpenCV LBPH camera-based recognition
    - backend 'howdy':  use Linux howdy daemon (legacy / Linux-only)
    - face disabled:    neutral score 0.5 (no face factor)
    """
    if not cfg.face.enabled:
        # Fall back to howdy if it is still enabled
        if cfg.howdy.enabled:
            return howdy_score()
        return 0.5

    if cfg.face.backend == "howdy":
        return howdy_score()

    # OpenCV backend
    from behavioral_auth.face.verify import opencv_face_score
    return opencv_face_score(
        model_path=cfg.face.model_path,
        camera_index=cfg.face.camera_index,
        confidence_threshold=cfg.face.confidence_threshold,
        success_score=cfg.face.success_score,
        fail_score=cfg.face.fail_score,
        show_preview=cfg.face.show_preview,
    )


def run_once(verbose: bool = False):
    global _last_action
    cfg = load_settings()
    meta = _load_model_meta(cfg)
    conn = duckdb.connect(cfg.storage.db_path)
    pred = predict_behavioral(conn)
    if not pred:
        print('missing model/scaler or no sequence')
        return None
    sid, seq_end_ns, behavioral_raw = pred
    behavioral_norm = normalize_behavioral(behavioral_raw, meta)
    face = get_face_score(cfg)
    fused = fuse_scores(behavioral_norm, face)

    if cfg.general.mode == 'enforce' and fused >= cfg.fusion.lock_threshold:
        decision, action = 'LOCK', 'LOCK'
        if time.time() - _last_action >= cfg.fusion.cooldown_sec:
            subprocess.run(cfg.actions.lock_cmd, shell=True, check=False)
            subprocess.run(cfg.actions.notify_cmd, shell=True, check=False)
            _last_action = time.time()
    elif fused >= cfg.fusion.challenge_threshold:
        decision, action = ('SIMULATE_CHALLENGE', 'NONE') if cfg.general.mode != 'enforce' else ('CHALLENGE', 'NONE')
    else:
        decision, action = ('SIMULATE_ALLOW', 'NONE') if cfg.general.mode != 'enforce' else ('ALLOW', 'NONE')

    conn.execute(
        'INSERT INTO decisions (session_id, seq_end_ns, behavioral_score, howdy_score, fused_score, decision, action_taken, mode, details) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        [sid, seq_end_ns, behavioral_raw, face, fused, decision, action, cfg.general.mode, json.dumps({})]
    )

    result = {
        'session_id': str(sid),
        'behavioral_raw': round(behavioral_raw, 4),
        'behavioral_norm': round(behavioral_norm, 4),
        'face': round(face, 4),
        'face_backend': cfg.face.backend if cfg.face.enabled else 'howdy',
        'fused': round(fused, 4),
        'decision': decision,
        'thresholds': {
            'challenge': cfg.fusion.challenge_threshold,
            'lock': cfg.fusion.lock_threshold,
            'model_lock_ref': meta.get('lock_threshold', '?'),
        },
    }
    print(json.dumps(result, indent=2 if verbose else None))
    return result


def loop_forever():
    cfg = load_settings()
    while True:
        run_once()
        time.sleep(cfg.inference.interval_sec)
