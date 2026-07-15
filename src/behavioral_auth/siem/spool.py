"""The disk spool: what holds an event while the SIEM is unreachable.

Forwarding must never block scoring or bring the daemon down, so it cannot
deliver synchronously — an event is appended here and drained on a later tick.
Delivered events are removed, so in steady state nothing accumulates locally;
only a backlog does.

The spool is bounded. When it overflows the oldest events are dropped and the
daemon says so, loudly: an audit trail that silently truncates is worse than one
that admits the gap, because only the second kind can be trusted when it is
quiet.

Delivery is at-least-once. A crash between a successful send and the rewrite
that removes the event will replay it, so the SIEM must tolerate a duplicate.
The alternative — remove first, then send — loses events instead, which is the
worse failure for an audit trail.
"""

from __future__ import annotations

import os
from pathlib import Path

from loguru import logger

from behavioral_auth.siem.event import Event


class Spool:
    def __init__(self, path: str, max_events: int):
        self.path = Path(path)
        self.max_events = max_events
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.events: list[Event] = []
        self._dropped = 0
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        kept, corrupt = [], 0
        for line in self.path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                kept.append(Event.from_json(line))
            except (ValueError, TypeError):
                corrupt += 1
        self.events = kept
        if corrupt:
            logger.warning(f'SIEM spool: discarded {corrupt} unreadable event(s)')
        if kept:
            logger.info(f'SIEM spool: {len(kept)} event(s) carried over from last run')

    def append(self, event: Event) -> None:
        self.events.append(event)
        if len(self.events) > self.max_events:
            overflow = len(self.events) - self.max_events
            del self.events[:overflow]
            self._dropped += overflow
            logger.error(
                f'SIEM spool full ({self.max_events}) — dropped {self._dropped} oldest '
                f'event(s) so far. The SIEM is missing them; it has not been told so.'
            )
        self._rewrite()

    def take(self, event: Event) -> None:
        """Mark one event as delivered."""
        try:
            self.events.remove(event)
        except ValueError:
            return
        self._rewrite()

    def _rewrite(self) -> None:
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(''.join(e.to_json() + '\n' for e in self.events))
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def __len__(self) -> int:
        return len(self.events)
