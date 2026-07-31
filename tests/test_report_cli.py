"""behavioral-report must honour --config, and refuse what it cannot honour.

It used to take no arguments at all — three lines that never read sys.argv — so
`behavioral-report --config <path>` was silently dropped and the *default*
database was reported instead. That is how a report of the wrong machine's
pattern reaches the screen with nothing on it to say so.

Both halves are pinned, because fixing only the first leaves the same trap for
the next flag nobody implemented: the flag has to steer resolution, and an
argument the command does not understand has to be an error rather than a shrug.
"""

import sys

import pytest

from behavioral_auth.cli import report_cmd
from behavioral_auth.config import config_path


@pytest.fixture
def elsewhere(tmp_path, monkeypatch):
    """A config file nowhere near the default search order."""
    target = tmp_path / 'elsewhere.yaml'
    target.write_text('general:\n  mode: prod\n')
    monkeypatch.delenv('BEHAVIORAL_AUTH_CONFIG', raising=False)
    return target


def test_config_flag_steers_resolution(elsewhere, monkeypatch):
    """Assert on config_path(), not on the environment variable.

    Checking that main() set BEHAVIORAL_AUTH_CONFIG would pass even if nothing
    downstream consulted it — which is precisely the defect being fixed.
    """
    seen = {}
    monkeypatch.setattr(report_cmd, 'report', lambda: seen.update(path=config_path()))
    monkeypatch.setattr(sys, 'argv', ['behavioral-report', '--config', str(elsewhere)])

    report_cmd.main()

    assert seen['path'] == str(elsewhere)


def test_without_the_flag_the_search_order_is_untouched(elsewhere, monkeypatch):
    """No --config must leave resolution exactly as it was — no empty override."""
    seen = {}
    monkeypatch.setattr(report_cmd, 'report',
                        lambda: seen.update(env=report_cmd.os.environ.get('BEHAVIORAL_AUTH_CONFIG')))
    monkeypatch.setattr(sys, 'argv', ['behavioral-report'])

    report_cmd.main()

    assert seen['env'] is None


def test_an_argument_it_does_not_understand_is_an_error(monkeypatch):
    """Silently ignoring the unknown is the half that produced the wrong report."""
    monkeypatch.setattr(report_cmd, 'report', lambda: pytest.fail('report must not run'))
    monkeypatch.setattr(sys, 'argv', ['behavioral-report', '--databse', '/tmp/typo.duckdb'])

    with pytest.raises(SystemExit) as exc:
        report_cmd.main()

    assert exc.value.code == 2
