"""Daemon state: the state machine, its persistence, and the public snapshot.

State lives in two places on purpose:

  * DuckDB (`daemon_state` + `state_transitions`) is the durable record. It
    survives restarts and gives an auditable history of every transition.
  * `run/state.json` is how *other processes* read the daemon. They cannot
    open the database — the daemon holds DuckDB's single write lock for its
    whole life — so `behavioral-auth status` reads this file instead.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from loguru import logger

from behavioral_auth.siem import Category, NullForwarder, Severity


class State(str, Enum):
    BOOTSTRAP = 'BOOTSTRAP'
    LEARNING = 'LEARNING'
    MONITORING = 'MONITORING'
    ALARM = 'ALARM'
    PAUSED = 'PAUSED'
    # Distinct from PAUSED, which is the user's decision, and from MONITORING,
    # which while not scoring would be a lie: the pattern was learned on other
    # hardware, so there is nothing meaningful to compare against. Never an
    # ALARM — "you undocked" is not something the user can act on.
    SUSPENDED = 'SUSPENDED'


@dataclass
class Snapshot:
    """Everything a reader needs, with no access to the database."""
    state: str = State.BOOTSTRAP.value
    since: str = ''
    pid: int = 0
    enrollment_id: str = ''
    session_id: str = ''
    started_at: str = ''
    stopped: bool = False

    # LEARNING progress
    n_sequences: int = 0
    min_sequences: int = 0
    active_minutes: float = 0.0
    min_active_minutes: int = 0
    distinct_hours: int = 0
    min_distinct_hours: int = 0
    face_samples: int = 0
    face_min_samples: int = 0
    cycles_done: int = 0
    stable_streak: int = 0
    stable_needed: int = 0
    next_cycle_in_sec: int = 0
    last_cycle: dict | None = None
    blocked_by: list[str] = field(default_factory=list)

    # MONITORING / ALARM
    last_ratio: float | None = None
    recent_ratios: list[float] = field(default_factory=list)
    face_state: str = 'unknown'
    consec_anom: int = 0
    consec_norm: int = 0
    alarm_since: str | None = None
    alarm_reason: str | None = None
    alarm_peak_ratio: float | None = None

    # How much of the captured input claims to have been synthesised rather than
    # typed. Windows only — the low-level hooks carry the flag and evdev has no
    # equivalent — so None means "this platform cannot tell", which a reader must
    # not confuse with "nothing was injected".
    injection: dict | None = None

    # Hardware stack
    pattern_stacks: list[str] = field(default_factory=list)
    stack_suspended_on: str | None = None

    last_error: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class StateStore:
    """Owns the current state, its persistence and the state.json snapshot."""

    def __init__(self, conn, run_dir: str, siem=None):
        self.conn = conn
        self.siem = siem or NullForwarder()
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_file = self.run_dir / 'state.json'
        self.snapshot = Snapshot(pid=os.getpid(), started_at=_now().isoformat())

        row = conn.execute(
            'SELECT state, enrollment_id FROM daemon_state WHERE id = 1'
        ).fetchone()
        self.state = State(row[0]) if row else State.BOOTSTRAP
        self.enrollment_id = str(row[1]) if row and row[1] else ''
        self.snapshot.state = self.state.value
        self.snapshot.enrollment_id = self.enrollment_id

    # ── enrollment ────────────────────────────────────────────────────────

    def active_enrollment(self) -> str | None:
        row = self.conn.execute(
            "SELECT enrollment_id FROM enrollments WHERE status <> 'retired' "
            'ORDER BY created_at DESC LIMIT 1'
        ).fetchone()
        return str(row[0]) if row else None

    def create_enrollment(self) -> str:
        eid = str(uuid.uuid4())
        self.conn.execute(
            'INSERT INTO enrollments (enrollment_id, status, user_name, host_name) '
            "VALUES (?, 'learning', ?, ?)",
            [eid, os.getenv('USER', 'unknown'), socket.gethostname()],
        )
        self.enrollment_id = eid
        self.snapshot.enrollment_id = eid
        logger.info(f'New enrollment {eid[:8]}…')
        return eid

    def mark_enrollment(self, enrollment_id: str, status: str) -> None:
        retired = 'retired' if status == 'retired' else None
        self.conn.execute(
            'UPDATE enrollments SET status = ?, retired_at = ? WHERE enrollment_id = ?',
            [status, _now() if retired else None, enrollment_id],
        )

    # ── transitions ───────────────────────────────────────────────────────

    def transition(self, to: State, reason: str, details: dict | None = None) -> None:
        if to is self.state:
            return
        frm = self.state
        self.conn.execute(
            'INSERT INTO state_transitions '
            '(enrollment_id, from_state, to_state, reason, details) VALUES (?, ?, ?, ?, ?)',
            [self.enrollment_id or None, frm.value, to.value, reason,
             json.dumps(details or {})],
        )
        self.conn.execute(
            'INSERT OR REPLACE INTO daemon_state (id, state, since, enrollment_id, updated_at, details) '
            'VALUES (1, ?, ?, ?, ?, ?)',
            [to.value, _now(), self.enrollment_id or None, _now(),
             json.dumps(details or {})],
        )
        self.state = to
        self.snapshot.state = to.value
        self.snapshot.since = _now().isoformat()
        logger.info(f'{frm.value} → {to.value}  ({reason})')

        self.siem.enrollment_id = self.enrollment_id
        # `details` is deliberately NOT forwarded. It is a free-form dict written
        # to the local database, and splatting it into the event would mean that
        # whatever a future caller decides to put in it leaves the machine —
        # silently, and without anyone revisiting this decision. What goes to a
        # SIEM is an explicit, closed list of fields.
        self.siem.emit(
            Category.STATE, 'transition',
            severity=Severity.ALERT if to is State.ALARM else Severity.NOTICE,
            from_state=frm.value, to_state=to.value, reason=reason,
        )

    def persist(self) -> None:
        """Rewrite daemon_state and the public snapshot atomically."""
        self.conn.execute(
            'INSERT OR REPLACE INTO daemon_state (id, state, since, enrollment_id, updated_at, details) '
            'VALUES (1, ?, ?, ?, ?, ?)',
            [self.state.value, self.snapshot.since or _now(), self.enrollment_id or None,
             _now(), '{}'],
        )
        self.write_snapshot()

    def write_snapshot(self) -> None:
        tmp = self.state_file.with_suffix('.tmp')
        tmp.write_text(self.snapshot.to_json())
        os.replace(tmp, self.state_file)

    def mark_stopped(self) -> None:
        self.snapshot.stopped = True
        self.write_snapshot()


def read_snapshot(run_dir: str) -> dict | None:
    """Read the daemon's public snapshot, or None if it has never run."""
    path = Path(run_dir) / 'state.json'
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
