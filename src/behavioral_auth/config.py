from pathlib import Path
import yaml
from pydantic import BaseModel, ConfigDict

class GeneralCfg(BaseModel):
    mode: str = 'dev'
    data_dir: str
    log_level: str = 'INFO'
class CollectorCfg(BaseModel):
    devices: list[str] = []
    include_builtin_only: bool = False
    batch_size: int = 4000
    flush_interval_sec: float = 1.0
class StorageCfg(BaseModel):
    db_path: str
    parquet_dir: str | None = None
    archive_after_days: int | None = None
    delete_after_days: int | None = None
class FeaturesCfg(BaseModel):
    window_sec: int = 30
    stride_sec: int = 5
    min_keyboard_events: int = 12
    min_mouse_events: int = 10
    scaler_path: str
    dedup_gap_sec: int = 2
class ModelCfg(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    seq_len: int = 24
    input_dim: int = 21
    hidden_dim: int = 24
    num_layers: int = 3
    kernel_size: int = 3
    dropout: float = 0.1
    batch_size: int = 128
    epochs: int = 25
    lr: float = 0.001
    val_split: float = 0.2
    model_path: str
    metadata_path: str
class InferenceCfg(BaseModel):
    interval_sec: int = 5
    min_fused_windows: int = 24
class FusionCfg(BaseModel):
    behavioral_weight: float = 0.7
    howdy_weight: float = 0.3
    challenge_threshold: float = 0.55
    lock_threshold: float = 0.78
    unlock_threshold: float = 0.30
    hysteresis: float = 0.04
    cooldown_sec: int = 30
class HowdyCfg(BaseModel):
    enabled: bool = True
    command: str = "/usr/bin/howdy test"
    timeout_sec: int = 3
    success_score: float = 0.05
    fail_score: float = 0.85

class FaceCfg(BaseModel):
    """OpenCV-based face recognition (cross-platform, works with any camera)."""
    enabled: bool = False
    # 'opencv' – use OpenCV LBPH; 'howdy' – delegate to Linux howdy daemon
    backend: str = 'opencv'
    camera_index: int = 0
    model_path: str = '/var/lib/behavioral-auth/face_model.yml'
    # LBPH confidence cut-off: predictions ≤ threshold count as a match
    # Lower = stricter.  Typical ranges: 0–40 excellent, 40–80 good, >100 unknown
    confidence_threshold: float = 80.0
    n_enroll_samples: int = 40
    success_score: float = 0.05
    fail_score: float = 0.85
    timeout_sec: int = 5
    show_preview: bool = False

class ActionsCfg(BaseModel):
    lock_cmd: str
    notify_cmd: str
class TimerCfg(BaseModel):
    feature_interval_sec: int = 60
class Settings(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    general: GeneralCfg
    collector: CollectorCfg
    storage: StorageCfg
    features: FeaturesCfg
    model: ModelCfg
    inference: InferenceCfg
    fusion: FusionCfg
    howdy: HowdyCfg
    face: FaceCfg = FaceCfg(model_path='/var/lib/behavioral-auth/face_model.yml')
    actions: ActionsCfg
    timer: TimerCfg | None = None

_SEARCH_PATHS = [
    '/etc/behavioral-auth/config.yaml',
    'config/config.yaml',
    'config.yaml',
]

def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*."""
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result

def load_settings(path: str | None = None) -> Settings:
    """Load settings from *path* (or search standard locations).

    If the general.mode is 'dev', also merges config.dev.yaml (if present)
    so that lightweight dev parameters override the production defaults.
    """
    if path is None:
        for candidate in _SEARCH_PATHS:
            p = Path(candidate)
            if p.exists():
                path = str(p)
                break
        else:
            raise FileNotFoundError(
                f"No config file found. Tried: {_SEARCH_PATHS}"
            )
    data = yaml.safe_load(Path(path).read_text())

    # Merge mode-specific overlay (e.g. config.dev.yaml)
    mode = data.get('general', {}).get('mode', 'dev')
    overlay_path = Path(path).parent / f'config.{mode}.yaml'
    if overlay_path.exists() and str(overlay_path) != path:
        overlay = yaml.safe_load(overlay_path.read_text()) or {}
        data = _deep_merge(data, overlay)

    return Settings(**data)
