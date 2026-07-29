"""Config loading: the mode overlay, and the --mode override that drives it."""

import sys
from pathlib import PureWindowsPath

import yaml

from behavioral_auth.config import (
    _search_paths,
    _system_config_path,
    config_path,
    load_settings,
)

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


# ── where the config is looked for ───────────────────────────────────────────
#
# The Windows service used to die here before it could report to the SCM, which
# surfaces only as "did not respond to the start signal in time" (7000/7009).
# The installer writes an editable config to %PROGRAMDATA%\behavioral-auth and
# points BEHAVIORAL_AUTH_CONFIG at it — but a machine-wide variable set during
# install is not visible to the SCM until the box reboots, and the search path
# had no Windows equivalent of /etc, so there was nothing to fall back to.

def _same_windows_path(produced: str, expected: str) -> bool:
    """Compare as Windows paths, not as strings.

    These tests fake sys.platform, but pathlib still uses the *host's* flavour:
    on a Linux runner `Path(r'C:\\ProgramData') / 'behavioral-auth'` yields
    'C:\\ProgramData/behavioral-auth', with forward slashes. A plain string
    comparison therefore passes on Windows and fails in CI, which is exactly how
    this got past a green local run once. PureWindowsPath normalises both
    separators, so the assertion means the same thing on either host.
    """
    return PureWindowsPath(produced) == PureWindowsPath(expected)


def test_the_machine_wide_config_is_under_programdata_on_windows(monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setenv('PROGRAMDATA', r'C:\ProgramData')
    assert _same_windows_path(_system_config_path(),
                              r'C:\ProgramData\behavioral-auth\config.yaml')


def test_a_relocated_programdata_is_honoured(monkeypatch):
    """%PROGRAMDATA% is not always on C:, and it is read per call rather than
    frozen at import — a service inherits a different environment than a shell."""
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setenv('PROGRAMDATA', r'D:\ProgramData')
    assert _system_config_path().startswith(r'D:\ProgramData')


def test_windows_falls_back_when_programdata_is_unset(monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.delenv('PROGRAMDATA', raising=False)
    assert _same_windows_path(_system_config_path(),
                              r'C:\ProgramData\behavioral-auth\config.yaml')


def test_unix_keeps_etc(monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'linux')
    assert _system_config_path() == '/etc/behavioral-auth/config.yaml'


def test_the_machine_wide_location_is_searched_before_the_working_directory(
        monkeypatch, tmp_path):
    """Order matters: a config.yaml lying in the process's cwd must not quietly
    win over the one an administrator installed."""
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setenv('PROGRAMDATA', str(tmp_path))
    paths = _search_paths()
    assert paths[0] == str(tmp_path / 'behavioral-auth' / 'config.yaml')
    assert paths[1:] == ['config/config.yaml', 'config.yaml']


def test_config_path_finds_the_machine_wide_file_without_the_env_var(
        monkeypatch, tmp_path):
    """The regression: with BEHAVIORAL_AUTH_CONFIG absent — which is what the
    SCM sees until a reboot — the installed config must still be found."""
    monkeypatch.delenv('BEHAVIORAL_AUTH_CONFIG', raising=False)
    monkeypatch.chdir(tmp_path)                       # no config.yaml in cwd
    installed = tmp_path / 'machine' / 'behavioral-auth' / 'config.yaml'
    installed.parent.mkdir(parents=True)
    installed.write_text(yaml.safe_dump(BASE))
    monkeypatch.setattr('behavioral_auth.config._system_config_path',
                        lambda: str(installed))

    assert config_path() == str(installed)
