import time
import threading
import duckdb
from loguru import logger
from behavioral_auth.config import load_settings

class Writer:
    """Thread-safe batched writer with periodic background flush."""

    def __init__(self):
        self.cfg = load_settings()
        self.conn = duckdb.connect(self.cfg.storage.db_path)
        self.conn.execute('PRAGMA threads=4')
        self.buf: list = []
        self._lock = threading.Lock()
        self.last = time.monotonic()
        # Start a background thread that flushes every flush_interval_sec
        self._stop = threading.Event()
        self._timer = threading.Thread(target=self._periodic_flush, daemon=True)
        self._timer.start()

    def _periodic_flush(self) -> None:
        interval = max(self.cfg.collector.flush_interval_sec, 0.5)
        while not self._stop.wait(interval):
            self.flush()

    def add(self, row) -> None:
        with self._lock:
            self.buf.append(row)
            elapsed = time.monotonic() - self.last
            if (len(self.buf) >= self.cfg.collector.batch_size
                    or elapsed >= self.cfg.collector.flush_interval_sec):
                self._flush_locked()

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        if not self.buf:
            return
        try:
            self.conn.executemany(
                'INSERT INTO raw_events '
                '(ts_ns, ts_utc, session_id, dev_path, dev_name, dev_type, ev_type, ev_code, ev_value) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                self.buf,
            )
            logger.debug(f"Flushed {len(self.buf)} events")
            self.buf.clear()
            self.last = time.monotonic()
        except Exception as exc:
            logger.error(f"Flush failed: {exc}")

    def close(self) -> None:
        self._stop.set()
        self._timer.join(timeout=3)
        self.flush()
        self.conn.close()
