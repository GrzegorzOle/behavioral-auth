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


def rebuild_features(conn, cfg: Settings) -> str:
    """Recompute this enrolment's feature windows and sequences from raw events.

    The raw events are never discarded, so a defect in feature *extraction* does
    not have to cost the collected behaviour — which is otherwise days of someone
    working normally, and cannot be re-collected on demand. This is what turns
    "the extractor was wrong" from a reset into a recomputation.

    The learning cycles of the enrolment are deleted with the windows, and that
    is deliberate rather than tidy-mindedness: a cycle records `shape`, the
    threshold as a multiple of the typical error, and the next cycle's stability
    is judged by how far that moved. Keeping cycles computed in the old feature
    space would have the first rebuilt cycle measure drift against a number from
    a different world, and a spurious drift is exactly what refuses promotion.

    Requires that no daemon is running: DuckDB's write lock is exclusive, and
    rebuilding underneath a live collector would race it.
    """
    from behavioral_auth.features.pipeline import build_feature_windows, build_sequences

    eid = active_enrollment(conn)
    if not eid:
        return 'Brak aktywnego wzorca — nie ma czego przeliczać.'

    # Captured before the delete: feature_windows is the only thing linking a
    # session to an enrolment, since raw_events carries no enrolment id.
    sessions = [str(r[0]) for r in conn.execute(
        'SELECT DISTINCT session_id FROM feature_windows WHERE enrollment_id = ?',
        [eid]).fetchall()]
    if not sessions:
        return 'Ten wzorzec nie ma jeszcze żadnych okien cech — nie ma czego przeliczać.'

    def counts():
        w = conn.execute('SELECT count(*) FROM feature_windows WHERE enrollment_id = ?',
                         [eid]).fetchone()[0]
        s = conn.execute('SELECT count(*) FROM fused_sequences WHERE enrollment_id = ?',
                         [eid]).fetchone()[0]
        return w, s

    before = counts()
    conn.execute('DELETE FROM fused_sequences WHERE enrollment_id = ?', [eid])
    conn.execute('DELETE FROM feature_windows WHERE enrollment_id = ?', [eid])
    conn.execute('DELETE FROM learning_cycles WHERE enrollment_id = ?', [eid])

    for sid in sessions:
        build_feature_windows(conn, sid, eid, cfg)
        build_sequences(conn, sid, eid, cfg)

    after = counts()
    logger.info(f'Rebuilt features for {eid[:8]}: windows {before[0]} -> {after[0]}, '
                f'sequences {before[1]} -> {after[1]}, over {len(sessions)} session(s)')
    return (f'Przeliczono wzorzec {eid[:8]} z {len(sessions)} sesji surowych zdarzeń.\n'
            f'  okna cech  {before[0]} -> {after[0]}\n'
            f'  sekwencje  {before[1]} -> {after[1]}\n'
            f'  cykle nauki skasowane — oceniały dane, których już nie ma.')
