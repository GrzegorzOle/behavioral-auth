"""The forwarder: the only thing the rest of the daemon talks to.

Off unless the config turns it on. With `siem.enabled: false` — the default —
nothing here opens a socket, and the daemon remains what the README promises: a
program that does not talk to the network.

`emit()` never raises and never blocks on the network. It spools, and the spool
drains on a later tick. A SIEM that is down must not be able to stop the daemon
from watching the keyboard.
"""

from __future__ import annotations

import time

from loguru import logger

from behavioral_auth.siem.event import Category, Event, Severity
from behavioral_auth.siem.sinks import SinkError, build_sink
from behavioral_auth.siem.spool import Spool


class Forwarder:
    def __init__(self, cfg, enrollment_id: str = '', session_id: str = ''):
        self.cfg = cfg.siem
        self.enabled = self.cfg.enabled
        self.enrollment_id = enrollment_id
        self.session_id = session_id
        self._last_flush = 0.0
        self._warned_unreachable = False
        if not self.enabled:
            self.sink = None
            self.spool = None
            return
        self.sink = build_sink(self.cfg)
        self.spool = Spool(self.cfg.spool_path, self.cfg.spool_max_events)
        logger.info(f'SIEM forwarding on: sink={self.cfg.sink} '
                    f'(alarms stored locally: {self.cfg.store_alarms_locally})')

    # ── the API the daemon uses ───────────────────────────────────────────

    def emit(self, category: str, action: str, severity: int = Severity.INFO,
             **detail) -> None:
        """Queue an event. Never raises, never touches the network."""
        if not self.enabled:
            return
        try:
            self.spool.append(Event(
                category=category, action=action, severity=severity,
                enrollment_id=self.enrollment_id, session_id=self.session_id,
                detail=detail,
            ))
        except OSError as exc:                       # a full or unwritable disk
            logger.error(f'SIEM spool write failed, event lost: {exc}')

    def flush(self, force: bool = False) -> None:
        """Drain the spool. Called on the daemon's tick; failures are kept."""
        if not self.enabled or not self.spool:
            return
        now = time.monotonic()
        if not force and now - self._last_flush < self.cfg.flush_interval_sec:
            return
        self._last_flush = now

        for event in list(self.spool.events):
            try:
                self.sink.send(event)
            except SinkError as exc:
                if not self._warned_unreachable:
                    self._warned_unreachable = True
                    logger.warning(f'SIEM unreachable ({exc}) — {len(self.spool)} event(s) '
                                   f'held in the spool, retrying')
                return                               # keep order: stop at the first failure
            except OSError as exc:
                logger.error(f'SIEM spool rewrite failed: {exc}')
                return
            self.spool.take(event)

        if self._warned_unreachable:
            self._warned_unreachable = False
            logger.info('SIEM reachable again — spool drained')

    def store_alarms_locally(self) -> bool:
        """Whether alarms should still be written to DuckDB.

        With forwarding off this is always true, because otherwise the alarm
        would exist nowhere at all.
        """
        return (not self.enabled) or self.cfg.store_alarms_locally

    def close(self) -> None:
        self.flush(force=True)
        if self.enabled and self.spool and len(self.spool):
            logger.warning(f'SIEM: {len(self.spool)} undelivered event(s) left in the '
                           f'spool at shutdown — they will be sent on the next start')


class NullForwarder(Forwarder):
    """Used where a Forwarder is required but forwarding is not configured."""

    def __init__(self):                              # noqa: D107 - see the class docstring
        self.enabled = False
        self.sink = None
        self.spool = None

    def emit(self, *args, **kwargs) -> None:
        return

    def flush(self, force: bool = False) -> None:
        return

    def store_alarms_locally(self) -> bool:
        return True

    def close(self) -> None:
        return


__all__ = ['Category', 'Forwarder', 'NullForwarder', 'Severity']
