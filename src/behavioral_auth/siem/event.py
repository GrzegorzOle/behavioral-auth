"""The events this daemon is willing to tell a SIEM about.

Deliberately narrow. What leaves the machine is a verdict and a number — never
the behaviour those were computed from. No key codes, no mouse coordinates, no
face crops, no feature vectors. A SIEM needs to know that the person at the
keyboard stopped matching; it does not need to know what they typed.

Per-sequence scores are not modelled here either: at a five-second stride they
would be hundreds of events an hour, which is a way to drown a SIEM, not to
inform it.
"""

from __future__ import annotations

import json
import socket
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


class Category:
    ALARM = 'alarm'      # an alarm was raised or cleared
    STATE = 'state'      # the state machine moved
    OPS = 'ops'          # the daemon or its pattern was operated on


class Severity:
    """Syslog severities, for the subset we emit."""
    ALERT = 1
    WARNING = 4
    NOTICE = 5
    INFO = 6


@dataclass
class Event:
    category: str
    action: str
    severity: int = Severity.INFO
    ts: str = ''
    host: str = ''
    enrollment_id: str = ''
    session_id: str = ''
    detail: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.ts:
            self.ts = datetime.now(timezone.utc).isoformat()
        if not self.host:
            self.host = socket.gethostname()

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(',', ':'), sort_keys=True)

    @classmethod
    def from_json(cls, line: str) -> Event:
        return cls(**json.loads(line))
