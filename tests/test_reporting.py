"""behavioral-report must survive a metadata file it does not fully understand."""

import json
from pathlib import Path

from behavioral_auth.reporting.metrics import _print


def _enrol(conn, eid: str, status: str) -> None:
    conn.execute(
        'INSERT INTO enrollments (enrollment_id, status) VALUES (?, ?)',
        [eid, status])


def test_report_survives_old_format_metadata(conn, cfg, capsys):
    """A pre-0.3.0 metadata file lacks the current keys — report, don't crash.

    The real trigger: a leftover model_meta.json with lock_threshold/train_samples
    and none of threshold/n_train/n_holdout/separation.
    """
    _enrol(conn, '11111111-1111-1111-1111-111111111111', 'learning')
    Path(cfg.model.metadata_path).write_text(json.dumps({
        'lock_threshold': 1.0, 'challenge_threshold': 0.5,
        'train_samples': 100, 'val_samples': 20, 'input_dim': 18, 'seq_len': 4,
    }))

    _print(conn, cfg)   # must not raise KeyError

    out = capsys.readouterr().out
    assert 'starym formacie' in out
    assert 'próg anomalii' not in out


def test_report_prints_threshold_for_current_metadata(conn, cfg, capsys):
    _enrol(conn, '22222222-2222-2222-2222-222222222222', 'active')
    Path(cfg.model.metadata_path).write_text(json.dumps({
        'threshold': 0.1234, 'n_train': 400, 'n_holdout': 100, 'separation': 3.5,
    }))

    _print(conn, cfg)

    out = capsys.readouterr().out
    assert 'próg anomalii 0.1234' in out
    assert 'separacja od syntetycznych negatywów: 3.5x' in out
    assert 'starym formacie' not in out
