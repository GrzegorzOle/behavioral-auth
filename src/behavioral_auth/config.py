"""Configuration loading and schema.

Settings come from a YAML file, searched in BEHAVIORAL_AUTH_CONFIG, then
/etc/behavioral-auth/config.yaml, then config/config.yaml, then config.yaml.
A mode-specific overlay (config.<mode>.yaml, e.g. config.dev.yaml) sitting
next to the base file is deep-merged on top of it.
"""

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

# The 21 features persisted per window. Order is significant: it defines the
# column order of every feature vector written to feature_windows.
FEATURE_COLUMNS = [
    'f_ks_count', 'f_ks_mean_dwell', 'f_ks_std_dwell',
    'f_ks_mean_flight', 'f_ks_std_flight', 'f_ks_backspace_ratio',
    'f_ks_repeat_ratio', 'f_ks_entropy',
    'f_ms_count', 'f_ms_speed_mean', 'f_ms_speed_std', 'f_ms_acc_mean',
    'f_ms_clicks', 'f_ms_click_dwell', 'f_ms_scrolls', 'f_ms_idle_ratio',
    'f_ms_curvature',
    'f_ctx_hour_sin', 'f_ctx_hour_cos', 'f_ctx_is_weekend',
    'f_activity_density',
]

# Wall-clock features carry no information about *who* is at the keyboard —
# an impostor at 10:00 on a Tuesday looks exactly like the owner. They only
# fire when the owner works at an unusual hour, and a feature that is constant
# across the whole enrolment (is_weekend, if you enrol on weekdays) makes the
# scaler divide by ~zero. Still stored for reporting; never fed to the model.
EXCLUDED_FROM_MODEL = ['f_ctx_hour_sin', 'f_ctx_hour_cos', 'f_ctx_is_weekend']

MODEL_COLUMNS = [c for c in FEATURE_COLUMNS if c not in EXCLUDED_FROM_MODEL]

# Sequences are stored with all 21 features so the model input can be changed
# without rebuilding them; this projects a stored vector onto the model input.
MODEL_COL_IDX = [FEATURE_COLUMNS.index(c) for c in MODEL_COLUMNS]


class GeneralCfg(BaseModel):
    mode: str = 'dev'                      # dev | prod
    data_dir: str
    log_level: str = 'INFO'
    log_file: str | None = None


class DaemonCfg(BaseModel):
    tick_sec: int = 5
    run_dir: str = '/var/lib/behavioral-auth/run'
    console: str = 'auto'                  # auto | always | never


class CollectorCfg(BaseModel):
    devices: list[str] = []
    batch_size: int = 200
    flush_interval_sec: float = 1.0


class StorageCfg(BaseModel):
    db_path: str


class FeaturesCfg(BaseModel):
    window_sec: int = 20
    stride_sec: int = 5
    min_keyboard_events: int = 12
    min_mouse_events: int = 10
    scaler_path: str
    dedup_gap_sec: int = 2
    # A sequence whose adjacent windows are further apart than this spans an
    # idle gap (windows below the activity threshold are dropped, so rows are
    # not time-contiguous) and would mix, say, Monday morning with Tuesday
    # evening into one training sample. Such sequences are discarded.
    max_seq_gap_sec: int = 40
    # Lower bound on per-feature std. Without it a feature that never varies
    # during enrolment amplifies any later deviation by ~1/epsilon.
    std_floor: float = 1e-3

    @property
    def model_columns(self) -> list[str]:
        return MODEL_COLUMNS


class ModelCfg(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    seq_len: int = 12
    hidden_dim: int = 24
    # The bottleneck. Keep it far below input_dim * seq_len (18 * 12 = 216) or
    # the autoencoder can just learn the identity and will flag nobody.
    latent_dim: int = 8
    num_layers: int = 3
    kernel_size: int = 3
    dropout: float = 0.10
    batch_size: int = 128
    epochs: int = 25
    lr: float = 0.001
    val_split: float = 0.2
    device: str = 'cpu'                    # cpu | cuda | auto
    # Fixed so that a learning cycle's result reflects the data, not the
    # random initialisation. See training.train.fit().
    seed: int = 1337
    model_path: str
    metadata_path: str

    @property
    def input_dim(self) -> int:
        return len(MODEL_COLUMNS)


class StabilityCfg(BaseModel):
    """Gates that a single learning cycle must pass to count as 'stable'."""
    pass_rate_min: float = 0.90
    error_ratio_max: float = 1.6
    threshold_drift_max: float = 0.25
    # Fraction of the owner's OWN held-out behaviour the threshold would flag.
    # This is the one error rate that is genuinely measurable here.
    false_alarm_max: float = 0.02
    # Fraction of SYNTHETIC impostor sequences that must exceed the anomaly
    # threshold, taken over the worst generator. Guards against a degenerate
    # model that reconstructs anyone's data well — such a model converges
    # beautifully and detects nothing. Not an accuracy figure: these impostors
    # are derived from the user's own data, not from a real second person.
    sanity_detection_min: float = 0.90


class LearningCfg(BaseModel):
    cycle_min_sec: int = 600
    cycle_min_new_sequences: int = 200
    holdout_frac: float = 0.20
    min_sequences: int = 1200
    min_active_minutes: int = 90
    min_distinct_hours: int = 3
    stable_consecutive_cycles: int = 3
    stability: StabilityCfg = StabilityCfg()


class AlarmCfg(BaseModel):
    # Consecutive anomalous scores AND the wall-clock span they cover. The
    # span matters: adjacent sequences share seq_len-1 windows, so a burst of
    # ticks is not by itself evidence of a sustained change.
    enter_consecutive: int = 16
    enter_min_span_sec: int = 120
    clear_consecutive: int = 16
    clear_min_span_sec: int = 120
    clear_hysteresis: float = 0.20
    notify_cmd: str | None = (
        "notify-send -u critical 'Behavioral Auth' "
        "'Osoba przy klawiaturze nie odpowiada wzorcowi'"
    )
    notify_cooldown_sec: int = 300
    heartbeat_sec: int = 300


class FaceCfg(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    enabled: bool = True
    backend: str = 'opencv'                # opencv | howdy
    camera_index: int = 0
    model_path: str = '/var/lib/behavioral-auth/face_model.yml'
    meta_path: str = '/var/lib/behavioral-auth/face_meta.json'
    samples_dir: str = '/var/lib/behavioral-auth/face_samples'
    # 'auto' calibrates the LBPH cut-off from held-out enrolment crops.
    # A float pins it manually.
    confidence_threshold: float | str = 'auto'
    required_for_promotion: bool = True
    min_samples: int = 60
    sample_interval_sec: int = 20
    retrain_every_n_samples: int = 30
    check_interval_sec: int = 30
    stranger_consecutive: int = 3
    keep_samples: bool = True
    # Quality gates for silent background capture.
    min_face_width: int = 100
    min_sharpness: float = 60.0
    min_brightness: float = 40.0
    max_brightness: float = 220.0
    # Once a provisional model exists, a crop that does not look like the
    # already-enrolled person is rejected — this is what stops a colleague
    # walking past the camera from being enrolled alongside you.
    self_confidence_max: float = 90.0
    # Only used when backend == 'howdy'.
    howdy_command: str = '/usr/bin/howdy test'
    howdy_timeout_sec: int = 3


class Settings(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    general: GeneralCfg
    daemon: DaemonCfg = DaemonCfg()
    collector: CollectorCfg = CollectorCfg()
    storage: StorageCfg
    features: FeaturesCfg
    model: ModelCfg
    learning: LearningCfg = LearningCfg()
    alarm: AlarmCfg = AlarmCfg()
    face: FaceCfg = FaceCfg()


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


def config_path() -> str:
    """Resolve the config file path, honouring BEHAVIORAL_AUTH_CONFIG."""
    env = os.environ.get('BEHAVIORAL_AUTH_CONFIG')
    candidates = [env, *_SEARCH_PATHS] if env else _SEARCH_PATHS
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise FileNotFoundError(f'No config file found. Tried: {candidates}')


def load_settings(path: str | None = None) -> Settings:
    """Load settings from *path*, or from the first existing search location.

    A config.<mode>.yaml overlay next to the base file is deep-merged on top,
    so config.dev.yaml can shrink the learning gates for a fast test run.
    """
    if path is None:
        path = config_path()
    data = yaml.safe_load(Path(path).read_text()) or {}

    mode = data.get('general', {}).get('mode', 'dev')
    overlay = Path(path).parent / f'config.{mode}.yaml'
    if overlay.exists() and str(overlay) != str(path):
        data = _deep_merge(data, yaml.safe_load(overlay.read_text()) or {})

    return Settings(**data)
