"""Is there a newer release? Notification only, and off by default.

This is the second and last thing in the tree that can open a socket — SIEM
forwarding is the first — and what it does when switched on is deliberately
narrow:

  * one HTTPS GET to a URL the operator can repoint at an internal mirror,
  * two strings read out of the answer and the rest forgotten,
  * no asset downloaded, no file made executable, no installer started.

**Why it stops at telling you.** This daemon sees every keystroke on the box and
starts automatically. A channel that could fetch new code and run it here is the
single most valuable thing an attacker could subvert: compromise the release
account once and every installation becomes a keylogger, without anyone typing a
command. So there is no code in this module that knows how to accept a binary —
not disabled, not gated behind a flag, absent. Installing an update stays a human
act, which is also the only way the Windows post-install trap (the installer
re-enables a service that captures nothing) gets a human's attention.

The request carries no version number and no machine identifier. GitHub requires
a User-Agent, so it gets a constant one: telling a third party which version of a
security tool a given IP is running is a disclosure, not a feature.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from behavioral_auth import __version__

USER_AGENT = 'behavioral-auth'
STATUS_FILE = 'update.json'

# A release payload from GitHub is a few kilobytes. Cap the read so a broken or
# hostile endpoint cannot feed the daemon an unbounded body.
_MAX_BYTES = 1 << 20

_VERSION_RE = re.compile(r'^v?(\d+(?:\.\d+)*)$')


class UpdateCheckError(RuntimeError):
    """The check could not be completed.

    Never fatal. A box with no route to the internet is the normal case for this
    product, not an error worth waking anyone for.
    """


def parse_version(text: object) -> tuple[int, ...] | None:
    """'v0.5.12' -> (0, 5, 12). None for anything that is not a plain release.

    A tag carrying a suffix — v0.6.0-rc1, or the v0.3.0-citest3 this repo has
    actually had — is deliberately unparseable rather than truncated to its
    numeric head. A pre-release is not an upgrade to offer anybody, and None here
    makes is_newer() answer no rather than guess.
    """
    if not isinstance(text, str):
        return None
    m = _VERSION_RE.match(text.strip())
    if not m:
        return None
    return tuple(int(part) for part in m.group(1).split('.'))


def is_newer(latest: object, current: object) -> bool:
    """Is *latest* a release worth telling the user about?

    Compared as integer tuples, never as strings: '0.5.2' > '0.5.12'
    lexicographically, so a string comparison would announce an upgrade to an
    *older* release and go on announcing it at every status call. Shorter tuples
    pad with zeros, so 0.6 beats 0.5.12 and 0.5 does not beat 0.5.0.
    """
    a, b = parse_version(latest), parse_version(current)
    if a is None or b is None:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


@dataclass
class UpdateStatus:
    """The whole of what a check produces. A closed set of fields on purpose.

    Nothing here points at a downloadable file: `url` is the human-readable
    release page, checked to be https, and it is only ever printed.
    """
    checked_at: str = ''
    current: str = ''
    latest: str | None = None
    url: str | None = None
    error: str | None = None

    @property
    def update_available(self) -> bool:
        return is_newer(self.latest, self.current)


def fetch_latest(cfg) -> tuple[str, str]:
    """Return (tag, release page URL) for the newest *published* release.

    /releases/latest excludes drafts and pre-releases, which is what we want:
    this repo has published test tags before, and none of them is something to
    push at a user.

    `assets` is not read. There is no code path in this module that could fetch
    one — see the module docstring.
    """
    request = urllib.request.Request(
        cfg.updates.url,
        headers={'User-Agent': USER_AGENT, 'Accept': 'application/vnd.github+json'},
        method='GET',
    )
    try:
        with urllib.request.urlopen(request, timeout=cfg.updates.timeout_sec) as response:
            body = response.read(_MAX_BYTES)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise UpdateCheckError(str(exc)) from exc

    try:
        data = json.loads(body)
        tag = data['tag_name']
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, KeyError) as exc:
        raise UpdateCheckError(f'unexpected answer from {cfg.updates.url}: {exc}') from exc
    if not isinstance(tag, str):
        raise UpdateCheckError('tag_name is not a string')

    page = data.get('html_url')
    return tag, page if isinstance(page, str) and page.startswith('https://') else ''


def check(cfg) -> UpdateStatus:
    """Perform one check. Returns a status; raises nothing."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        tag, page = fetch_latest(cfg)
    except UpdateCheckError as exc:
        return UpdateStatus(checked_at=now, current=__version__, error=str(exc))
    return UpdateStatus(checked_at=now, current=__version__,
                        latest=tag.removeprefix('v'), url=page or None)


# ── the cached answer, so nothing has to ask the network to report ────────────

def status_path(run_dir: str) -> Path:
    return Path(run_dir) / STATUS_FILE


def write_status(run_dir: str, status: UpdateStatus) -> None:
    """Cache the answer next to state.json, atomically.

    Best-effort: under a Windows service install the run directory belongs to
    another principal and the CLI cannot write here. Losing the cache is not
    worth an exception — the answer has already been printed to the person who
    asked for it.
    """
    path = status_path(run_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(asdict(status), indent=2))
        os.replace(tmp, path)
    except OSError:
        pass


def read_status(run_dir: str) -> UpdateStatus | None:
    """The last cached answer, or None. Reads a file; never the network."""
    try:
        data = json.loads(status_path(run_dir).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    fields = ('checked_at', 'current', 'latest', 'url', 'error')
    return UpdateStatus(**{k: data.get(k) for k in fields})


def due(status: UpdateStatus | None, interval_hours: int) -> bool:
    """Has enough time passed to ask again?

    A failed check still stamps checked_at, so an offline box waits the full
    interval rather than retrying on every tick. That is the trade deliberately
    made: this is a convenience, and a daemon that hammers a URL every five
    seconds because the network is down is worse than one that notices a release
    a day late.
    """
    if status is None or not status.checked_at:
        return True
    try:
        last = datetime.fromisoformat(status.checked_at)
    except (TypeError, ValueError):
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last).total_seconds() >= interval_hours * 3600


def describe(status: UpdateStatus | None) -> str | None:
    """The one line the CLIs print, or None when there is nothing to say.

    Silent when up to date. A tool that reports "no update available" every time
    someone checks the state of their pattern is noise, and noise is what trains
    people to stop reading.

    The release page is deliberately not folded in here: it is long enough to
    push the sentence past any sane terminal width, and every caller prints it on
    its own line from status.url.
    """
    if status is None or not status.update_available:
        return None
    return (f'dostępna nowsza wersja {status.latest} (masz {status.current}) — '
            f'pobierz i zainstaluj ręcznie')
