"""The update check: it must compare versions correctly, and it must never
turn into a download.

The comparison tests are not padding. A string comparison of '0.5.2' against
'0.5.12' says the older one is newer, which would announce a downgrade at every
status call — and this project has already shipped a 0.5.2 and a 0.5.12.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from behavioral_auth import __version__, updates
from behavioral_auth.config import Settings, UpdatesCfg


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self, _n: int | None = None) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


def _payload(tag: str = 'v9.9.9', **extra) -> bytes:
    data = {
        'tag_name': tag,
        'html_url': 'https://github.com/GrzegorzOle/behavioral-auth/releases/tag/' + tag,
        # A real release payload carries these. Nothing in updates.py may read
        # them — see test_assets_are_never_read.
        'assets': [{'name': 'behavioral-auth-setup-9.9.9.exe',
                    'browser_download_url': 'https://example.invalid/setup.exe',
                    'size': 422251878}],
        'tarball_url': 'https://example.invalid/tar',
        'body': 'release notes',
    }
    data.update(extra)
    return json.dumps(data).encode()


@pytest.fixture
def captured(monkeypatch):
    """Intercept urlopen and record what was asked for."""
    seen: list = []

    def fake_urlopen(request, timeout=None):
        seen.append((request, timeout))
        return _FakeResponse(_payload())

    monkeypatch.setattr(updates.urllib.request, 'urlopen', fake_urlopen)
    return seen


# ── version parsing and comparison ────────────────────────────────────────────

@pytest.mark.parametrize('text, expected', [
    ('0.5.12', (0, 5, 12)),
    ('v0.5.12', (0, 5, 12)),
    ('  v1.0  ', (1, 0)),
    ('2', (2,)),
])
def test_parse_version_accepts_plain_releases(text, expected):
    assert updates.parse_version(text) == expected


@pytest.mark.parametrize('text', [
    'v0.6.0-rc1',        # a pre-release is not an upgrade to offer anyone
    'v0.3.0-citest3',    # this repo has actually published one of these
    'latest', '', '0.5.x', None, 12,
])
def test_parse_version_refuses_anything_else(text):
    assert updates.parse_version(text) is None


def test_is_newer_compares_numerically_not_as_strings():
    # The whole point: '0.5.2' > '0.5.12' as strings.
    assert updates.is_newer('0.5.12', '0.5.2') is True
    assert updates.is_newer('0.5.2', '0.5.12') is False


@pytest.mark.parametrize('latest, current, expected', [
    ('0.5.13', '0.5.12', True),
    ('0.5.12', '0.5.12', False),
    ('0.5.11', '0.5.12', False),
    ('0.6', '0.5.12', True),          # shorter tuple, still newer
    ('0.5', '0.5.0', False),          # padded with zeros, so equal
    ('1.0.0', '0.99.99', True),
    ('v0.5.13', '0.5.12', True),      # the tag form works unchanged
])
def test_is_newer(latest, current, expected):
    assert updates.is_newer(latest, current) is expected


def test_is_newer_says_no_when_either_side_is_unparseable():
    """Never nag on a tag this code does not understand."""
    assert updates.is_newer('v0.9.0-rc1', '0.5.12') is False
    assert updates.is_newer('0.9.0', 'nonsense') is False


# ── the request itself ────────────────────────────────────────────────────────

def test_fetch_latest_reads_the_tag(cfg, captured):
    tag, page = updates.fetch_latest(cfg)
    assert tag == 'v9.9.9'
    assert page.startswith('https://github.com/')
    assert len(captured) == 1, 'exactly one request per check'
    request, timeout = captured[0]
    assert request.full_url == cfg.updates.url
    assert timeout == cfg.updates.timeout_sec


def test_the_request_carries_no_version_and_no_identifier(cfg, captured):
    """Telling a third party which version of a security tool an IP runs is a
    disclosure. GitHub needs a User-Agent; it gets a constant one."""
    updates.fetch_latest(cfg)
    request = captured[0][0]
    assert request.get_header('User-agent') == 'behavioral-auth'
    assert __version__ not in json.dumps(dict(request.headers))
    assert '?' not in request.full_url, 'no query string, so nothing to smuggle into it'


def test_assets_are_never_read(cfg, captured):
    """A closed set of fields comes back, and none of them is a downloadable file.

    The payload above deliberately carries a browser_download_url. If it ever
    turns up in the status, someone has taught this module to fetch a binary.
    """
    status = updates.check(cfg)
    blob = json.dumps(status.__dict__)
    assert 'example.invalid' not in blob
    assert 'browser_download_url' not in blob
    assert set(status.__dict__) == {'checked_at', 'current', 'latest', 'url', 'error'}


def test_check_reports_a_newer_release(cfg, captured):
    status = updates.check(cfg)
    assert status.latest == '9.9.9'         # the leading v is stripped
    assert status.current == __version__
    assert status.error is None
    assert status.update_available is True


def test_check_turns_a_dead_network_into_a_status_not_an_exception(cfg, monkeypatch):
    def boom(request, timeout=None):
        raise updates.urllib.error.URLError('Network is unreachable')

    monkeypatch.setattr(updates.urllib.request, 'urlopen', boom)
    status = updates.check(cfg)
    assert status.error and 'unreachable' in status.error
    assert status.update_available is False


def test_check_survives_an_answer_that_is_not_a_release(cfg, monkeypatch):
    monkeypatch.setattr(updates.urllib.request, 'urlopen',
                        lambda request, timeout=None: _FakeResponse(b'<html>nope</html>'))
    status = updates.check(cfg)
    assert status.error is not None
    assert status.update_available is False


def test_check_survives_a_missing_tag(cfg, monkeypatch):
    monkeypatch.setattr(updates.urllib.request, 'urlopen',
                        lambda request, timeout=None: _FakeResponse(b'{"message": "Not Found"}'))
    assert updates.check(cfg).error is not None


def test_a_plain_http_release_page_is_dropped(cfg, monkeypatch):
    """A downgraded link is not printed at the user as if it were ours."""
    monkeypatch.setattr(
        updates.urllib.request, 'urlopen',
        lambda request, timeout=None: _FakeResponse(
            _payload(html_url='http://evil.invalid/releases')))
    tag, page = updates.fetch_latest(cfg)
    assert tag == 'v9.9.9'
    assert page == ''


# ── the cached status ─────────────────────────────────────────────────────────

def test_status_round_trips(cfg):
    status = updates.UpdateStatus(checked_at='2026-08-04T10:00:00+00:00',
                                  current='0.5.12', latest='0.5.13',
                                  url='https://example.com/r')
    updates.write_status(cfg.daemon.run_dir, status)
    assert updates.read_status(cfg.daemon.run_dir) == status


def test_reading_a_status_that_was_never_written_is_not_an_error(cfg):
    assert updates.read_status(cfg.daemon.run_dir) is None


def test_a_corrupt_status_file_is_not_an_error(cfg):
    updates.status_path(cfg.daemon.run_dir).parent.mkdir(parents=True, exist_ok=True)
    updates.status_path(cfg.daemon.run_dir).write_text('{ half written')
    assert updates.read_status(cfg.daemon.run_dir) is None


def test_writing_a_status_nowhere_writable_is_swallowed(cfg, monkeypatch):
    """Under a Windows service install the run dir belongs to another principal.
    The answer has already been printed; losing the cache is not worth a crash.
    """
    def denied(*a, **k):
        raise PermissionError('Access is denied')

    monkeypatch.setattr(updates.Path, 'mkdir', denied)
    updates.write_status(cfg.daemon.run_dir, updates.UpdateStatus())   # must not raise


# ── scheduling ────────────────────────────────────────────────────────────────

def test_never_checked_is_due(cfg):
    assert updates.due(None, 24) is True


def test_a_fresh_check_is_not_due_again(cfg):
    just_now = datetime.now(timezone.utc).isoformat()
    assert updates.due(updates.UpdateStatus(checked_at=just_now), 24) is False


def test_an_old_check_is_due(cfg):
    old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    assert updates.due(updates.UpdateStatus(checked_at=old), 24) is True


def test_a_failed_check_still_holds_off_for_the_interval():
    """An offline box must not retry on every five-second tick."""
    just_now = datetime.now(timezone.utc).isoformat()
    failed = updates.UpdateStatus(checked_at=just_now, error='Network is unreachable')
    assert updates.due(failed, 24) is False


def test_an_unreadable_timestamp_is_due(cfg):
    assert updates.due(updates.UpdateStatus(checked_at='not a date'), 24) is True


# ── what the user is shown ────────────────────────────────────────────────────

def test_describe_is_silent_when_up_to_date():
    assert updates.describe(None) is None
    assert updates.describe(updates.UpdateStatus(current='0.5.12', latest='0.5.12')) is None
    assert updates.describe(updates.UpdateStatus(current='0.5.12', error='boom')) is None


def test_describe_names_both_versions():
    line = updates.describe(updates.UpdateStatus(
        current='0.5.12', latest='0.5.13', url='https://example.com/r'))
    assert '0.5.13' in line and '0.5.12' in line


def test_describe_leaves_the_url_to_the_caller():
    """It goes on its own line — folded in, it pushes the sentence past any
    reasonable terminal width."""
    line = updates.describe(updates.UpdateStatus(
        current='0.5.12', latest='0.5.13', url='https://example.com/r'))
    assert 'https://' not in line
    assert len(line) < 80


# ── the daemon's gate ─────────────────────────────────────────────────────────
#
# Imported inside the tests: pulling daemon.daemon drags in torch and onnx, and
# nothing else in this file needs them.

def _stub_daemon(cfg):
    from types import SimpleNamespace
    return SimpleNamespace(cfg=cfg, _update_task=None, _update_status=None)


def test_the_daemon_asks_nothing_while_the_check_is_disabled(cfg, monkeypatch):
    """The guard is the first line, before any scheduling question.

    With this wrong the daemon reaches the network on a box whose owner left the
    setting alone, which is the one thing the config file promises it will not do.
    """
    from behavioral_auth.daemon.daemon import Daemon

    called: list[str] = []
    monkeypatch.setattr(updates, 'due', lambda *a: called.append('due') or True)
    monkeypatch.setattr(updates, 'check', lambda *a: called.append('check'))

    assert cfg.updates.check_enabled is False
    Daemon._maybe_check_updates(_stub_daemon(cfg))
    assert called == []


def test_the_daemon_consults_the_interval_once_enabled(cfg, monkeypatch):
    from behavioral_auth.daemon.daemon import Daemon

    called: list[str] = []
    cfg.updates.check_enabled = True
    # Answering "not due" stops it before asyncio.create_task, which has no loop
    # here — so this pins the order: enabled first, interval second, task last.
    monkeypatch.setattr(updates, 'due', lambda *a: called.append('due') or False)

    Daemon._maybe_check_updates(_stub_daemon(cfg))
    assert called == ['due']


# ── configuration ─────────────────────────────────────────────────────────────

def test_checking_is_off_by_default():
    """The promise the README makes. If this ever defaults to true, the daemon
    talks to the network on a machine whose owner never asked it to."""
    assert Settings(**{
        'general': {'data_dir': '/tmp/x'},
        'storage': {'db_path': '/tmp/x/db'},
        'features': {'scaler_path': '/tmp/x/s.json'},
        'model': {'model_path': '/tmp/x/m.onnx', 'metadata_path': '/tmp/x/m.json'},
    }).updates.check_enabled is False


def test_the_url_must_be_https():
    with pytest.raises(ValidationError, match='https'):
        UpdatesCfg(url='http://api.github.com/repos/x/y/releases/latest')


def test_the_interval_cannot_be_zero():
    with pytest.raises(ValidationError, match='at least 1'):
        UpdatesCfg(interval_hours=0)
