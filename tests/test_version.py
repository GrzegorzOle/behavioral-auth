"""The runtime version must be real, and it must match what was installed.

Before this existed nothing in src/ knew which build it was: the version lived
in pyproject.toml, in the git tag and in installer.iss, and a log could only be
attributed to a build by matching line numbers in tracebacks. It also matters
now for a second reason — a binary that misreports its own version tells every
user an upgrade is available forever.
"""

from __future__ import annotations

import pytest

from behavioral_auth import __version__
from behavioral_auth.updates import parse_version


def test_the_version_is_a_plain_release_number():
    """Not 'dev', not '0.5.12-dirty'. is_newer() refuses to compare anything
    else, so a decorated version silently disables the update notice."""
    assert parse_version(__version__) is not None
    assert len(parse_version(__version__)) == 3, 'major.minor.patch'


def test_the_installed_metadata_agrees_with_the_module():
    """pyproject.toml reads this attribute, so the two cannot drift at build
    time — but an editable install records the version at install time and then
    goes stale. This venv has carried a metadata version six releases behind the
    source before now. Fix with: pip install -e . --no-deps
    """
    from importlib import metadata
    try:
        installed = metadata.version('behavioral-auth')
    except metadata.PackageNotFoundError:
        pytest.skip('behavioral-auth is not installed in this environment')
    assert installed == __version__, (
        f'installed metadata says {installed}, source says {__version__} — '
        f'stale editable install; run: pip install -e . --no-deps')
