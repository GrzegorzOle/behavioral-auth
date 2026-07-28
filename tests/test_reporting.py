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


# ── pattern age and drift ────────────────────────────────────────────────────

def _promote(conn, eid: str, days_ago: int, version: int = 1) -> None:
    conn.execute(
        'INSERT INTO model_registry (version, created_at, model_path, scaler_path, '
        'threshold_challenge, threshold_lock, metrics_json, notes) '
        f"VALUES (?, now() - INTERVAL '{days_ago} days', ?, ?, ?, ?, ?, ?)",
        [version, 'm.onnx', 's.json', 1.0, 1.0, '{}', f'enrollment {eid}'])


def _scores(conn, eid: str, weeks_ago: int, n: int, ratio: float) -> None:
    import uuid as _uuid
    sid = str(_uuid.uuid4())
    conn.execute('INSERT INTO sessions (session_id, user_name, mode) VALUES (?, ?, ?)',
                 [sid, 'test', 'test'])
    for i in range(n):
        conn.execute(
            'INSERT INTO scores (ts_utc, enrollment_id, session_id, seq_end_ns, error, '
            'ratio, beh_anomalous, verdict, state) '
            f"VALUES (now() - INTERVAL '{weeks_ago * 7} days', ?, ?, ?, ?, ?, ?, ?, ?)",
            [eid, sid, i, ratio, ratio, False, 'normal', 'MONITORING'])


def test_report_shows_how_old_the_pattern_is(conn, cfg, capsys):
    eid = '33333333-3333-3333-3333-333333333333'
    _enrol(conn, eid, 'active')
    _promote(conn, eid, days_ago=47, version=3)

    _print(conn, cfg)

    out = capsys.readouterr().out
    assert 'Wiek wzorca i dryf' in out
    assert '47 dni temu' in out
    assert 'wersja 3' in out


def test_report_shows_a_rising_trend_and_refuses_to_explain_it(conn, cfg, capsys):
    """The number is the deliverable; the interpretation is not ours to give.

    A rising median is the owner drifting or somebody else typing, and with no
    impostor data this system cannot tell those apart. It must not imply that it
    can.
    """
    eid = '44444444-4444-4444-4444-444444444444'
    _enrol(conn, eid, 'active')
    _promote(conn, eid, days_ago=30)
    _scores(conn, eid, weeks_ago=3, n=40, ratio=0.50)
    _scores(conn, eid, weeks_ago=0, n=40, ratio=0.90)

    _print(conn, cfg)

    out = capsys.readouterr().out
    assert '0.50x' in out and '0.90x' in out
    assert '80%' in out                      # 0.50 -> 0.90
    assert 'ALBO inna osoba' in out
    assert 'learn-more' in out


def test_report_will_not_call_two_thin_weeks_a_trend(conn, cfg, capsys):
    """A handful of scores in a week says more about the sample than the person."""
    eid = '55555555-5555-5555-5555-555555555555'
    _enrol(conn, eid, 'active')
    _promote(conn, eid, days_ago=10)
    _scores(conn, eid, weeks_ago=2, n=3, ratio=0.4)
    _scores(conn, eid, weeks_ago=0, n=3, ratio=2.0)

    _print(conn, cfg)

    out = capsys.readouterr().out
    assert 'za mało danych na trend' in out
    assert 'pierwszy tydzień' not in out


def test_report_says_nothing_about_drift_when_there_is_nothing_to_say(conn, cfg, capsys):
    eid = '66666666-6666-6666-6666-666666666666'
    _enrol(conn, eid, 'learning')

    _print(conn, cfg)

    assert 'Wiek wzorca i dryf' not in capsys.readouterr().out
