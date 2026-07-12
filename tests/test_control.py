"""The control channel: the daemon holds the DB write lock, so the CLI can only
reach it through the spool."""

from __future__ import annotations

from behavioral_auth.daemon import control


def test_pidfile_signals_liveness(tmp_path):
    run_dir = str(tmp_path)
    assert not control.daemon_running(run_dir)

    pid = control.PidFile(run_dir)
    assert pid.acquire()
    assert control.daemon_running(run_dir)

    # A second daemon must not be able to start alongside the first.
    assert not control.PidFile(run_dir).acquire()

    pid.release()
    assert not control.daemon_running(run_dir)


def test_a_command_round_trips(tmp_path):
    run_dir = str(tmp_path)

    control.send(run_dir, 'reset', {'purge_data': True}, timeout=0.01)

    pending = control.poll(run_dir)
    assert len(pending) == 1
    assert pending[0].cmd == 'reset'
    assert pending[0].args == {'purge_data': True}

    control.reply(run_dir, pending[0], ok=True, message='done')

    assert control.poll(run_dir) == []          # drained
    assert (tmp_path / 'control' / 'done' / f'{pending[0].id}.json').exists()


def test_a_malformed_command_is_discarded(tmp_path):
    run_dir = str(tmp_path)
    ctl = tmp_path / 'control'
    ctl.mkdir(parents=True, exist_ok=True)
    (ctl / 'garbage.json').write_text('{not json')

    assert control.poll(run_dir) == []
    assert not (ctl / 'garbage.json').exists()
