"""behavioral-report — what the daemon has actually observed.

Deliberately does NOT print FAR/FRR/EER. The previous version computed them
from the user's own scores, which is meaningless: with no impostor samples, a
"false accept rate" measured against yourself is a number with no referent. It
looked like a security metric and was not one. What follows are observations,
labelled as such.
"""

from __future__ import annotations

import json
from pathlib import Path

from behavioral_auth.config import load_settings
from behavioral_auth.db import open_db


def report() -> None:
    cfg = load_settings()
    conn = open_db(cfg)
    try:
        _print(conn, cfg)
    finally:
        conn.close()


def _print(conn, cfg) -> None:
    enrollment = conn.execute(
        "SELECT enrollment_id, status, created_at FROM enrollments "
        "WHERE status <> 'retired' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()

    print()
    if not enrollment:
        print('Brak wzorca. Uruchom: behavioral-authd')
        return
    eid, status, created = enrollment
    print(f'Wzorzec {str(eid)[:8]}…  status={status}  utworzony {created:%Y-%m-%d %H:%M}')

    meta_path = Path(cfg.model.metadata_path)
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        print(f'  próg anomalii {meta["threshold"]:.4f}  '
              f'(trening {meta["n_train"]} sekwencji, holdout {meta["n_holdout"]})')
        print(f'  separacja od syntetycznych negatywów: {meta["separation"]:.1f}x')

    cycles = conn.execute(
        'SELECT cycle_no, pass_rate, error_ratio, separation, stable, promoted '
        'FROM learning_cycles WHERE enrollment_id = ? ORDER BY cycle_no', [eid]
    ).fetchall()
    if cycles:
        print(f'\nCykle nauki ({len(cycles)}):')
        for no, pr, er, sep, stable, promoted in cycles:
            mark = '✓' if stable else '·'
            tail = '  ← PROMOCJA' if promoted else ''
            print(f'  {mark} #{no}  pass_rate {pr:.2f}  err_ratio {er:.2f}  '
                  f'separacja {sep:.1f}x{tail}')

    scores = conn.execute(
        'SELECT count(*), avg(ratio), max(ratio), '
        "count(*) FILTER (WHERE verdict = 'anomalous') "
        'FROM scores WHERE enrollment_id = ?', [eid]
    ).fetchone()
    if scores and scores[0]:
        n, avg, mx, anom = scores
        print(f'\nPunktacja w nadzorze: {n} sekwencji')
        print(f'  odchylenie od progu: średnio {avg:.2f}x, maksymalnie {mx:.2f}x')
        print(f'  ocenionych jako anomalne: {anom} ({anom / n * 100:.1f}%)')

    if cfg.siem.enabled and not cfg.siem.store_alarms_locally:
        # An empty list here would read as "nothing happened", which is a lie the
        # report must not tell: the alarms exist, they are just not here.
        print(f'\nAlarmy: nie są przechowywane lokalnie (siem.store_alarms_locally: false).'
              f'\n  Szukaj ich w SIEM-ie — sink={cfg.siem.sink}.')
    else:
        alarms = conn.execute(
            'SELECT started_at, ended_at, reason, peak_ratio, n_scores '
            'FROM alarms WHERE enrollment_id = ? ORDER BY started_at DESC LIMIT 10', [eid]
        ).fetchall()
        print(f'\nAlarmy: {len(alarms)}')
        for started, ended, reason, peak, n in alarms:
            span = f'{(ended - started).total_seconds():.0f}s' if ended else 'trwa'
            print(f'  {started:%Y-%m-%d %H:%M}  powód={reason}  szczyt={peak:.2f}x  '
                  f'czas={span}  ({n} wyników)')

    print('\nCzego tu NIE ma: wskaźników FAR/FRR. Nie da się ich policzyć — system '
          'widział\ndane tylko jednej osoby, więc nie ma z czym ich porównać.\n')
