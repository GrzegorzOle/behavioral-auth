import numpy as np
import pytest

from behavioral_auth.config import Settings


@pytest.fixture
def cfg(tmp_path) -> Settings:
    """A complete Settings pointing entirely inside tmp_path."""
    return Settings(**{
        'general': {'mode': 'test', 'data_dir': str(tmp_path)},
        'daemon': {'run_dir': str(tmp_path / 'run'), 'console': 'never'},
        'storage': {'db_path': str(tmp_path / 'behavior.duckdb')},
        'features': {
            'window_sec': 10, 'stride_sec': 2,
            'min_keyboard_events': 3, 'min_mouse_events': 3,
            'scaler_path': str(tmp_path / 'scaler.json'),
            'dedup_gap_sec': 1, 'max_seq_gap_sec': 20,
        },
        'model': {
            'seq_len': 4, 'epochs': 2,
            'model_path': str(tmp_path / 'model.onnx'),
            'metadata_path': str(tmp_path / 'meta.json'),
        },
        'face': {'enabled': False},
    })


@pytest.fixture
def conn(cfg):
    from behavioral_auth.db import open_db
    c = open_db(cfg)
    yield c
    c.close()


def make_sequences(n: int, seq_len: int = 4, n_features: int = 21,
                   seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.lognormal(0.0, 0.3, size=(n, seq_len, n_features)).astype(np.float32)
