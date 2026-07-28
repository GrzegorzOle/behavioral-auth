"""Config loading: the mode overlay, and the --mode override that drives it."""

import yaml

from behavioral_auth.config import load_settings

BASE = {
    'general': {'mode': 'prod', 'data_dir': '/tmp/ba'},
    'storage': {'db_path': '/tmp/ba/behavior.duckdb'},
    'features': {'scaler_path': '/tmp/ba/scaler.json', 'min_keyboard_events': 12},
    'model': {'model_path': '/tmp/ba/model.onnx', 'metadata_path': '/tmp/ba/meta.json',
              'epochs': 30},
    'learning': {'min_sequences': 900},
    'face': {'enabled': False},
}

DEV_OVERLAY = {
    'features': {'min_keyboard_events': 3},
    'model': {'epochs': 10},
    'learning': {'min_sequences': 60},
}


def _write(tmp_path):
    (tmp_path / 'config.yaml').write_text(yaml.safe_dump(BASE))
    (tmp_path / 'config.dev.yaml').write_text(yaml.safe_dump(DEV_OVERLAY))
    return str(tmp_path / 'config.yaml')


def test_prod_config_ignores_the_dev_overlay(tmp_path):
    cfg = load_settings(_write(tmp_path))
    assert cfg.general.mode == 'prod'
    assert cfg.learning.min_sequences == 900
    assert cfg.model.epochs == 30


def test_mode_override_pulls_in_the_matching_overlay(tmp_path):
    # The whole point of --mode dev: not just a relabelled prod run. The gates
    # have to come down too, or `make demo` still takes hours.
    cfg = load_settings(_write(tmp_path), mode='dev')
    assert cfg.general.mode == 'dev'
    assert cfg.learning.min_sequences == 60
    assert cfg.model.epochs == 10
    assert cfg.features.min_keyboard_events == 3


def test_mode_override_to_prod_drops_a_dev_default(tmp_path):
    BASE_DEV = dict(BASE, general={'mode': 'dev', 'data_dir': '/tmp/ba'})
    (tmp_path / 'config.yaml').write_text(yaml.safe_dump(BASE_DEV))
    (tmp_path / 'config.dev.yaml').write_text(yaml.safe_dump(DEV_OVERLAY))

    cfg = load_settings(str(tmp_path / 'config.yaml'), mode='prod')
    assert cfg.general.mode == 'prod'
    assert cfg.learning.min_sequences == 900


def test_no_override_leaves_the_file_alone(tmp_path):
    cfg = load_settings(_write(tmp_path), mode=None)
    assert cfg.general.mode == 'prod'
    assert cfg.learning.min_sequences == 900
