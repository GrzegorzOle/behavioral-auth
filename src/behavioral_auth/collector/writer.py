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

_INSERT = (
    'INSERT INTO raw_events '
    '(ts_ns, ts_utc, session_id, dev_path, dev_name, dev_type, ev_type, ev_code, ev_value) '
    'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)'
)


class Writer:
    def __init__(self, conn, batch_size: int = 200):
        self.conn = conn
        self.batch_size = batch_size
        self.buf: list[tuple] = []
        self.total = 0

    def add(self, row: tuple) -> None:
        self.buf.append(row)
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
