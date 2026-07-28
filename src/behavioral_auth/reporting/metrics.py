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

from datetime import datetime, timezone

from behavioral_auth.collector.stack import describe
from behavioral_auth.config import load_settings
from behavioral_auth.daemon.console import sparkline
from behavioral_auth.db import open_db

# Below this, a week's median says more about the sample than about the person.
_MIN_SCORES_PER_WEEK = 20


def _promotion(conn, enrollment_id: str):
    """When this enrolment's pattern was promoted, and as which version."""
    row = conn.execute(
        'SELECT version, created_at FROM model_registry WHERE notes = ? '
        'ORDER BY version DESC LIMIT 1', [f'enrollment {enrollment_id}'],
    ).fetchone()
    return row


def _print_age_and_drift(conn, enrollment_id: str) -> None:
    """How old the pattern is, and whether scoring has drifted since.

    Behaviour moves over weeks — a pattern is a photograph of how someone typed
    during enrolment, not a law. This surfaces the trend so it can be seen rather
    than guessed at from a single alarm.

    What it deliberately does NOT do is tell you *why* the trend moved. A rising
    median is the owner drifting or somebody else at the keyboard, and this system
    has no impostor data with which to tell those apart. It shows the number and
    says so; the judgement is the reader's.
    """
    promo = _promotion(conn, enrollment_id)
    weeks = conn.execute(
        "SELECT date_trunc('week', ts_utc) AS wk, count(*), median(ratio) "
        'FROM scores WHERE enrollment_id = ? GROUP BY wk ORDER BY wk',
        [enrollment_id],
    ).fetchall()
    if not promo and not weeks:
        return

    print('\nWiek wzorca i dryf:')

    if promo:
        version, created = promo
        days = (datetime.now(timezone.utc) - created).days
        print(f'  promowany {created:%Y-%m-%d %H:%M} — {days} dni temu (wersja {version})')
    else:
        print('  brak zapisu o promocji (wzorzec sprzed tej wersji albo jeszcze w nauce)')

    solid = [(wk, n, med) for wk, n, med in weeks if n >= _MIN_SCORES_PER_WEEK]
    if len(solid) < 2:
        if weeks:
            print(f'  za mało danych na trend (potrzeba 2 tygodni po '
                  f'{_MIN_SCORES_PER_WEEK}+ wyników)')
        return

    print(f'  mediana odchylenia od progu, tydzień po tygodniu  '
          f'{sparkline([m for _, _, m in solid])}')
    for wk, n, med in solid[-8:]:
        print(f'    {wk:%Y-%m-%d}  {med:.2f}x  ({n} wyników)')

    first, last = solid[0][2], solid[-1][2]
    if first > 0:
        change = (last - first) / first * 100
        arrow = '↑' if change > 0 else '↓'
        print(f'  pierwszy tydzień {first:.2f}x → ostatni {last:.2f}x  '
              f'({arrow} {abs(change):.0f}%)')
        if change >= 25:
            print('  Rosnąca mediana to dryf zachowania ALBO inna osoba przy klawiaturze —')
            print('  ten system nie odróżnia jednego od drugiego i nie udaje, że potrafi.')
            print('  Jeśli masz pewność, że to ty: "behavioral-auth learn-more" (nie reset).')


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
        # The full metadata is written atomically at promotion; a file that
        # lacks these keys is from an older format (a pre-0.3.0 leftover), not a
        # half-written one. Don't crash on it — it is overwritten at the next
        # promotion anyway.
        if all(k in meta for k in ('threshold', 'n_train', 'n_holdout', 'separation')):
            print(f'  próg anomalii {meta["threshold"]:.4f}  '
                  f'(trening {meta["n_train"]} sekwencji, holdout {meta["n_holdout"]})')
            print(f'  separacja od syntetycznych negatywów: {meta["separation"]:.1f}x')
        else:
            print('  (metadane modelu w starym formacie — pominięto; '
                  'zostaną nadpisane przy następnej promocji)')

        # Which hardware the pattern is entitled to judge. More than one stack is
        # not a richer pattern — it is a wider one, and a wider pattern accepts
        # more. Absent for patterns promoted before stacks were recorded.
        stacks = meta.get('stacks')
        if stacks:
            print(f'  zestawy sprzętu w nauce: {len(stacks)}')
            for s in stacks:
                print(f'    • {describe(s)}')
            if len(stacks) > 1:
                print('    UWAGA: wzorzec obejmujący więcej niż jeden zestaw ma szerszy '
                      'rozrzut,')
                print('    więc wyższy próg — jest BARDZIEJ pobłażliwy niż wzorzec '
                      'uczony na jednym.')
        elif 'stacks' in meta:
            print('  zestawy sprzętu w nauce: brak zapisu (wzorzec sprzed tej wersji) '
                  '— bramka sprzętowa nieaktywna')

    _print_age_and_drift(conn, eid)

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
