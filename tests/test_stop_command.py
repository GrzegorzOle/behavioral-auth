"""`behavioral-auth stop`.

There was no way to stop a session daemon except `taskkill`, which a hidden
console app cannot answer: no "Stopped cleanly", and DuckDB replaying its WAL on
the next start. Survivable, but not clean — and on the Windows box it has to be
done repeatedly to keep RDP out of the enrolment, so "survivable" was being
relied on daily.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from behavioral_auth.daemon import control
from behavioral_auth.cli import main as cli
from behavioral_auth.daemon.daemon import Daemon

REPO_ROOT = Path(__file__).resolve().parent.parent


def _stub_daemon():
    """Only what _apply touches. `stop` deliberately needs almost nothing — it
    sets a flag and returns, so that the shutdown is the ordinary one."""
    return SimpleNamespace(
        _stopping=False, _stop_by_command=False, source=None,
        siem=SimpleNamespace(emit=lambda *a, **k: None),
        store=SimpleNamespace(transition=lambda *a, **k: None))


def test_stop_sets_the_flag_and_answers_first():
    """The reply is written by _handle_control after _apply returns, so the
    caller must be answered rather than left waiting for a dead socket."""
    d = _stub_daemon()
    ok, msg = Daemon._apply(d, control.Request(path=Path('x'), id='r1', cmd='stop', args={}))
    assert ok is True
    assert msg
    assert d._stopping is True


def test_stop_records_that_a_human_asked():
    """A gap in coverage reads differently depending on whether somebody asked
    for it or the daemon died; the SIEM event carries the difference."""
    d = _stub_daemon()
    Daemon._apply(d, control.Request(path=Path('x'), id='r1', cmd='stop', args={}))
    assert d._stop_by_command is True


def test_an_ordinary_death_is_not_marked_as_requested():
    d = _stub_daemon()
    Daemon._apply(d, control.Request(path=Path('x'), id='r1', cmd='pause', args={}))
    assert d._stop_by_command is False


def test_stop_with_no_daemon_running_does_not_touch_the_database(cfg, monkeypatch):
    """_dispatch's no-daemon fallback opens the DB and runs migrations. That is
    exactly the wrong thing to do on behalf of a command whose subject is a
    running process, which is why stop does not go through it."""
    monkeypatch.setattr(control, 'daemon_running', lambda run_dir: False)

    def explode(*a, **k):
        raise AssertionError('stop must not open the database')

    monkeypatch.setattr('behavioral_auth.db.open_db', explode)
    assert cli.cmd_stop(cfg, SimpleNamespace(timeout=1.0)) == 1


def test_stop_waits_for_the_process_to_actually_go(cfg, monkeypatch):
    """It returns as soon as the daemon is gone, not as soon as it was asked:
    the caller is usually about to do the thing the stop was for, and a daemon
    still holding DuckDB's lock is the whole problem."""
    alive = [True, True, False]
    monkeypatch.setattr(control, 'daemon_running', lambda run_dir: alive.pop(0))
    monkeypatch.setattr(control, 'send', lambda *a, **k: {'ok': True, 'message': 'ok'})
    monkeypatch.setattr(cli.time, 'sleep', lambda s: None)
    assert cli.cmd_stop(cfg, SimpleNamespace(timeout=30.0)) == 0


def test_a_daemon_that_never_exits_is_reported_as_a_failure(cfg, monkeypatch):
    """Printing "stopped" over a process that is still holding the lock would be
    worse than saying nothing."""
    monkeypatch.setattr(control, 'daemon_running', lambda run_dir: True)
    monkeypatch.setattr(control, 'send', lambda *a, **k: {'ok': True, 'message': 'ok'})
    monkeypatch.setattr(cli.time, 'sleep', lambda s: None)
    monkeypatch.setattr(cli.time, 'monotonic',
                        (lambda ticks=iter([0.0, 0.0, 99.0]): next(ticks)))
    assert cli.cmd_stop(cfg, SimpleNamespace(timeout=30.0)) == 1


def test_stop_is_reachable_from_the_argument_parser():
    """A command nobody can type is not a command."""
    import inspect
    src = inspect.getsource(cli.main)
    assert "add_parser('stop'" in src
    assert 'fn=cmd_stop' in src


# ── --config must not silently resolve somewhere else ────────────────────────

def test_a_missing_config_path_is_refused_not_ignored(tmp_path):
    r"""config_path() treats the env var as a candidate and falls through to the
    machine-wide file when it is absent. Fine for the installer — a frozen
    bundle must still run with ProgramData emptied — but for a path a human just
    typed it meant `--config scratch.yaml` with a typo in it operating on the
    LIVE pattern. Measured on Windows: a nonexistent path resolved to
    C:\ProgramData\behavioral-auth\config.yaml and the command was delivered
    to the real daemon. With `reset` in the command set that is a foot-gun.
    """
    import pytest
    with pytest.raises(SystemExit) as exc:
        cli._set_config(str(tmp_path / 'not-here.yaml'))
    assert 'nie ma takiego pliku' in str(exc.value)


def test_an_existing_config_path_is_honoured(tmp_path, monkeypatch):
    import os
    path = tmp_path / 'c.yaml'
    path.write_text('general: {}\n')
    monkeypatch.delenv('BEHAVIORAL_AUTH_CONFIG', raising=False)
    cli._set_config(str(path))
    assert os.environ['BEHAVIORAL_AUTH_CONFIG'] == str(path)


def test_the_environment_variable_keeps_its_fallback(monkeypatch, tmp_path):
    """Only the explicit argument became strict. The installer sets a
    machine-wide variable and the bundled default is the last-resort fallback
    behind it; making config_path() itself strict would break a frozen bundle on
    a box where ProgramData was emptied."""
    from behavioral_auth.config import config_path
    monkeypatch.setenv('BEHAVIORAL_AUTH_CONFIG', str(tmp_path / 'gone.yaml'))
    monkeypatch.chdir(REPO_ROOT)
    assert config_path() != str(tmp_path / 'gone.yaml')
