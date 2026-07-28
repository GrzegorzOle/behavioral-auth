"""Control channel between the CLI and a running daemon.

The daemon holds DuckDB's write lock for its whole life, so a second process
simply cannot open the database to change anything. Commands therefore travel
as small JSON files in a spool directory that the daemon drains once per tick,
and replies come back the same way.

The pidfile doubles as the liveness probe: if the CLI can take an exclusive
flock on it, no daemon is running, and the CLI is free to do the work itself
against the database directly.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

# An exclusive, non-blocking advisory lock on the pidfile, held for the daemon's
# life and probed by the CLI for liveness. fcntl is Unix-only; Windows has no
# evdev but it has msvcrt byte-range locking, which gives the same "another
# process holds it -> I can't take it" signal on a 1-byte region at offset 0.
if sys.platform == 'win32':
    import msvcrt

    def _lock_nb(fd: int) -> bool:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl

    def _lock_nb(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


@dataclass
class Request:
    path: Path
    id: str
    cmd: str
    args: dict


class PidFile:
    """Exclusive flock on run/daemon.pid, held for the daemon's lifetime."""

    def __init__(self, run_dir: str):
        self.path = Path(run_dir) / 'daemon.pid'
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._fd: int | None = None

    def acquire(self) -> bool:
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        if not _lock_nb(fd):
            os.close(fd)
            return False
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        os.fsync(fd)
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is not None:
            _unlock(self._fd)
            os.close(self._fd)
            self._fd = None
        self.path.unlink(missing_ok=True)


def daemon_running(run_dir: str) -> bool:
    """True if a daemon currently holds the pidfile lock."""
    path = Path(run_dir) / 'daemon.pid'
    if not path.exists():
        return False
    fd = os.open(path, os.O_RDWR)
    try:
        if not _lock_nb(fd):
            return True       # someone else holds it — the daemon is alive
        _unlock(fd)
        return False
    finally:
        os.close(fd)


def _control_dir(run_dir: str) -> Path:
    d = Path(run_dir) / 'control'
    (d / 'done').mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def send(run_dir: str, cmd: str, args: dict | None = None, timeout: float = 10.0) -> dict:
    """Send *cmd* to the daemon and wait for its acknowledgement."""
    ctl = _control_dir(run_dir)
    req_id = f'{time.strftime("%Y%m%dT%H%M%S")}-{uuid.uuid4().hex[:8]}'
    payload = json.dumps({'id': req_id, 'cmd': cmd, 'args': args or {}})

    tmp = ctl / f'.{req_id}.tmp'
    tmp.write_text(payload)
    os.chmod(tmp, 0o600)
    os.replace(tmp, ctl / f'{req_id}.json')

    ack = ctl / 'done' / f'{req_id}.json'
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ack.exists():
            try:
                return json.loads(ack.read_text())
            except json.JSONDecodeError:
                pass
        time.sleep(0.15)
    return {'ok': False, 'message': f'daemon did not answer within {timeout:.0f}s'}


def poll(run_dir: str) -> list[Request]:
    """Drain pending requests, oldest first."""
    ctl = _control_dir(run_dir)
    out = []
    for path in sorted(ctl.glob('*.json'), key=lambda p: p.stat().st_mtime):
        try:
            data = json.loads(path.read_text())
            out.append(Request(path=path, id=data['id'], cmd=data['cmd'],
                               args=data.get('args', {})))
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning(f'Discarding malformed control file {path.name}: {exc}')
            path.unlink(missing_ok=True)
    return out


def reply(run_dir: str, req: Request, ok: bool, message: str) -> None:
    """Acknowledge *req* and remove it from the queue."""
    done = _control_dir(run_dir) / 'done'
    (done / f'{req.id}.json').write_text(json.dumps({'ok': ok, 'message': message}))
    req.path.unlink(missing_ok=True)

    cutoff = time.time() - 300
    for stale in done.glob('*.json'):
        if stale.stat().st_mtime < cutoff:
            stale.unlink(missing_ok=True)
