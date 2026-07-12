"""Operations on the frozen pattern.

One implementation, two callers: the daemon runs these on its own connection
when a control file arrives, and the CLI runs them directly against the
database when no daemon holds the lock. Keeping a single code path is what
stops the two from drifting apart.
"""

from __future__ import annotations

import os
import socket
import uuid
from pathlib import Path

from loguru import logger

from behavioral_auth.config import Settings
from behavioral_auth.face.sampler import delete_samples


def _remove(path: str | None) -> None:
    if path:
        Path(path).unlink(missing_ok=True)


def active_enrollment(conn) -> str | None:
    row = conn.execute(
        "SELECT enrollment_id FROM enrollments WHERE status <> 'retired' "
        'ORDER BY created_at DESC LIMIT 1'
    ).fetchone()
    return str(row[0]) if row else None


def reset(conn, cfg: Settings, purge_data: bool = False) -> str:
    """Forget the current person and start learning a new one from scratch.

    This is 'somebody else is going to use this machine now'. The old pattern
    and every face crop belonging to it are destroyed; the behavioural data is
    kept (it costs nothing and stays auditable) unless purge_data is set.
    """
    old = active_enrollment(conn)
    if old:
        conn.execute(
            "UPDATE enrollments SET status = 'retired', retired_at = now() "
            'WHERE enrollment_id = ?', [old])
        delete_samples(cfg, old)
        if purge_data:
            conn.execute('DELETE FROM fused_sequences WHERE enrollment_id = ?', [old])
            conn.execute('DELETE FROM feature_windows WHERE enrollment_id = ?', [old])

    for artifact in (cfg.model.model_path, cfg.model.metadata_path,
                     cfg.features.scaler_path, cfg.face.model_path, cfg.face.meta_path):
        _remove(artifact)

    new = str(uuid.uuid4())
    conn.execute(
        'INSERT INTO enrollments (enrollment_id, status, user_name, host_name) '
        "VALUES (?, 'learning', ?, ?)",
        [new, os.getenv('USER', 'unknown'), socket.gethostname()])

    logger.warning(
        f'Pattern reset: enrollment {old[:8] if old else "—"} retired, '
        f'{new[:8]} now learning'
        + (' (behavioural data purged)' if purge_data else ''))
    return f'Wzorzec skasowany. Nowy enrollment {new[:8]}… — trwa nauka od zera.'


def learn_more(conn, cfg: Settings) -> str:
    """Keep the pattern and the data, but reopen learning to refine it.

    The existing model stays live and keeps scoring while the new cycles run,
    so there is no window where the machine is unwatched.
    """
    eid = active_enrollment(conn)
    if not eid:
        return 'Brak aktywnego wzorca — nie ma czego douczać.'
    conn.execute(
        "UPDATE enrollments SET status = 'learning' WHERE enrollment_id = ?", [eid])
    logger.info(f'Re-opened learning for enrollment {eid[:8]}…')
    return f'Wzorzec {eid[:8]}… wraca do nauki. Stary model pozostaje aktywny do czasu promocji.'
