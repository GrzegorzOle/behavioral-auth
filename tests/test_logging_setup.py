"""Startup with no standard streams, as in a Windows service.

A frozen service process has `sys.stderr is None`: there is no console attached,
and loguru rejects a None sink with TypeError. Adding it unconditionally killed
the Windows service inside setup_logging before it could do anything else — and
only under the SCM, because `debug` runs in a console where stderr exists, so
every manual check passed. Same shape as the other Windows service defects: the
path the service manager takes was the one nobody exercised.
"""

import sys
import types

import pytest
from loguru import logger

from behavioral_auth.daemon.main import setup_logging


@pytest.fixture(autouse=True)
def _drop_handlers():
    """setup_logging configures the process-wide loguru logger. Leaving a file
    sink attached to a tmp_path that pytest then deletes makes later, unrelated
    tests fail on a write to a vanished directory."""
    yield
    logger.remove()


def _console(enabled: bool):
    return types.SimpleNamespace(enabled=enabled, emit_log=lambda msg: None)


def test_logging_survives_a_service_with_no_stderr(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'stderr', None)
    cfg.general.log_file = str(tmp_path / 'logs' / 'behavioral-auth.log')

    setup_logging(cfg, _console(False))          # must not raise

    logger.info('written without a console')
    logger.complete()
    assert (tmp_path / 'logs' / 'behavioral-auth.log').exists()


def test_no_stderr_and_no_log_file_is_still_not_fatal(cfg, monkeypatch):
    """Nothing to log to is a silent daemon, but it must not be a dead one."""
    monkeypatch.setattr(sys, 'stderr', None)
    cfg.general.log_file = ''

    setup_logging(cfg, _console(False))
    logger.info('goes nowhere, harmlessly')


def test_a_real_stderr_is_still_used(cfg, monkeypatch, capsys):
    cfg.general.log_file = ''
    setup_logging(cfg, _console(False))
    logger.info('to stderr')
    logger.complete()
    assert 'to stderr' in capsys.readouterr().err


# ── the status console ───────────────────────────────────────────────────────
#
# Same root cause, a different call site: a process with no console has
# sys.stdout set to None. `auto` would crash on None.isatty() and `always` on
# the first write. This is reachable outside the service too — Task Scheduler at
# logon is the documented fallback when a Session 0 service cannot see the
# desktop, and it has no console window either.

def test_a_streamless_process_gets_no_console(monkeypatch):
    from behavioral_auth.daemon.console import Console

    monkeypatch.setattr(sys, 'stdout', None)
    assert Console('auto').enabled is False
    assert Console('always').enabled is False
    assert Console('never').enabled is False


def test_a_real_tty_still_gets_one(monkeypatch):
    from behavioral_auth.daemon.console import Console

    monkeypatch.setattr(sys, 'stdout', types.SimpleNamespace(isatty=lambda: True))
    assert Console('auto').enabled is True
    assert Console('always').enabled is True
    assert Console('never').enabled is False


def test_a_pipe_gets_one_only_when_asked(monkeypatch):
    from behavioral_auth.daemon.console import Console

    monkeypatch.setattr(sys, 'stdout', types.SimpleNamespace(isatty=lambda: False))
    assert Console('auto').enabled is False
    assert Console('always').enabled is True
