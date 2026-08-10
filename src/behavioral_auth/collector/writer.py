"""Batched writer for raw input events.

The connection is injected rather than opened here: the daemon owns the single
DuckDB instance, and the old design — where the Writer opened its own
connection and held it for the process lifetime — is precisely what forced
the previous code to kill the collector before every scoring cycle.

No lock and no background thread either. `add()` is called from the asyncio
loop and `flush()` from the same loop, so there is nothing to synchronise.
"""

from __future__ import annotations

from loguru import logger

from behavioral_auth.collector.zones import KeyPseudonymiser

_INSERT = (
    'INSERT INTO raw_events '
    '(ts_ns, ts_utc, session_id, dev_path, dev_name, dev_id, dev_type, '
    ' ev_type, ev_code, ev_value, kb_zone, kb_pair) '
    'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'
)


class Writer:
    """Batches rows to disk, pseudonymising keystrokes on the way through.

    The pseudonymiser lives here rather than in each source because this is the
    single choke point every event passes on its way to the database — evdev,
    the Windows hook and the synthetic generator all funnel through `add()`. One
    place to change gives one invariant worth testing: **no key code reaches
    disk**. Spreading it across three emitters would make that a claim about
    three files instead of a property of one.

    The sources keep emitting real key codes in memory, which is what lets
    pairing stay exact across rollover; the identity is dropped at the boundary.
    """

    def __init__(self, conn, batch_size: int = 200):
        self.conn = conn
        self.batch_size = batch_size
        self.buf: list[tuple] = []
        self.total = 0
        self._pseudo = KeyPseudonymiser()

    def add(self, row: tuple) -> None:
        self.buf.append(self._pseudo.transform(row))
        if len(self.buf) >= self.batch_size:
            self.flush()

    def flush(self) -> int:
        """Write the buffer. Returns the number of events written."""
        if not self.buf:
            return 0
        batch, self.buf = self.buf, []
        try:
            self.conn.executemany(_INSERT, batch)
        except Exception as exc:
            # Put the batch back so the next flush retries it rather than
            # silently dropping the user's behaviour on the floor.
            self.buf = batch + self.buf
            logger.error(f'Flush of {len(batch)} events failed: {exc}')
            return 0
        self.total += len(batch)
        logger.debug(f'Flushed {len(batch)} events (total {self.total})')
        return len(batch)
