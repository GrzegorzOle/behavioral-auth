"""behavioral-auth — control and inspect the daemon.

Every command that changes something is routed through the daemon when one is
running, because it holds DuckDB's write lock and a second process physically
cannot write to the database. When no daemon is running the same code runs
here directly against the database instead — one implementation, two callers.
"""

from __future__ import annotations

import argparse
import os
import sys

from behavioral_auth.config import load_settings
from behavioral_auth.daemon import commands, control
from behavioral_auth.daemon.console import BOLD, DIM, GREEN, RED, RESET, YELLOW, bar, sparkline
from behavioral_auth.daemon.state import State, read_snapshot

_STATE_LABEL = {
    State.LEARNING.value: (f'{DIM}●{RESET}', 'NAUKA — buduje wzorzec'),
    State.MONITORING.value: (f'{GREEN}●{RESET}', 'NADZÓR — wzorzec zamrożony'),
    State.ALARM.value: (f'{RED}●{RESET}', 'ALARM — osoba nie odpowiada wzorcowi'),
    State.PAUSED.value: (f'{DIM}○{RESET}', 'PAUZA'),
    State.BOOTSTRAP.value: (f'{DIM}○{RESET}', 'START'),
}


def _dispatch(cfg, cmd: str, args: dict) -> int:
    """Send to the daemon, or do it here if no daemon holds the lock."""
    run_dir = cfg.daemon.run_dir

    if control.daemon_running(run_dir):
        result = control.send(run_dir, cmd, args)
        print(result.get('message', ''))
        return 0 if result.get('ok') else 1

    from behavioral_auth.db import open_db
    conn = open_db(cfg)
    try:
        if cmd == 'reset':
            print(commands.reset(conn, cfg, args.get('purge_data', False)))
        elif cmd == 'learn-more':
            print(commands.learn_more(conn, cfg))
        else:
            print(f'Demon nie działa — komenda "{cmd}" nie ma na co zadziałać.')
            return 1
    finally:
        conn.close()
    print(f'{DIM}(demon nie działał — zmiana zapisana bezpośrednio w bazie){RESET}')
    return 0


def cmd_status(cfg, args) -> int:
    snap = read_snapshot(cfg.daemon.run_dir)
    if not snap:
        print('Demon nigdy nie był uruchomiony. Start: behavioral-authd')
        return 1

    running = control.daemon_running(cfg.daemon.run_dir)
    dot, label = _STATE_LABEL.get(snap['state'], ('?', snap['state']))

    print()
    print(f'  {dot} {BOLD}{label}{RESET}')
    if not running:
        print(f'  {YELLOW}demon nie działa{RESET} — poniżej ostatni znany stan')
    print(f'  {DIM}wzorzec{RESET} {(snap.get("enrollment_id") or "—")[:8]}')

    if snap['state'] == State.LEARNING.value:
        print()
        print(f'  sekwencje  {snap["n_sequences"]:>5}/{snap["min_sequences"]:<5} '
              f'[{bar(snap["n_sequences"], snap["min_sequences"])}]')
        print(f'  aktywność  {snap["active_minutes"]:>5.0f}/{snap["min_active_minutes"]:<5} min '
              f'[{bar(snap["active_minutes"], snap["min_active_minutes"])}]')
        if snap.get('face_min_samples'):
            print(f'  twarz      {snap["face_samples"]:>5}/{snap["face_min_samples"]:<5} '
                  f'[{bar(snap["face_samples"], snap["face_min_samples"])}]')
        print(f'  cykli {snap["cycles_done"]}, seria stabilnych '
              f'{snap["stable_streak"]}/{snap["stable_needed"]}')
        if snap.get('last_cycle'):
            c = snap['last_cycle']
            print(f'  {DIM}ostatni cykl: pass_rate {c["pass_rate"]:.2f}, '
                  f'err_ratio {c["error_ratio"]:.2f}, separacja {c["separation"]:.1f}x{RESET}')
        if snap.get('blocked_by'):
            print(f'  {DIM}czeka na: {", ".join(snap["blocked_by"])}{RESET}')
    else:
        ratio = snap.get('last_ratio')
        print()
        print(f'  odchylenie {ratio:.2f}x od progu' if ratio is not None
              else '  brak jeszcze wyniku')
        if snap.get('recent_ratios'):
            print(f'  {sparkline(snap["recent_ratios"])}')
        print(f'  twarz: {snap.get("face_state", "unknown")}')
        if snap['state'] == State.ALARM.value:
            print()
            print(f'  {RED}{BOLD}ALARM{RESET}  powód: {snap.get("alarm_reason")}  '
                  f'szczyt {snap.get("alarm_peak_ratio", 0):.2f}x')
            print(f'  {DIM}sesja NIE została zablokowana — ten system tylko ostrzega{RESET}')
    print()
    return 0


def cmd_reset(cfg, args) -> int:
    if not args.yes:
        print('To skasuje wyuczony wzorzec i wszystkie próbki twarzy, po czym '
              'nauka zacznie się od zera.')
        if input('Na pewno? [t/N] ').strip().lower() not in ('t', 'tak', 'y', 'yes'):
            print('Anulowano.')
            return 1
    return _dispatch(cfg, 'reset', {'purge_data': args.purge_data})


def cmd_learn_more(cfg, args) -> int:
    return _dispatch(cfg, 'learn-more', {})


def cmd_pause(cfg, args) -> int:
    return _dispatch(cfg, 'pause', {})


def cmd_resume(cfg, args) -> int:
    return _dispatch(cfg, 'resume', {})


def cmd_db(cfg, args) -> int:
    if control.daemon_running(cfg.daemon.run_dir):
        print('Demon działa i trzyma bazę — zatrzymaj go, żeby migrować ręcznie.')
        return 1
    from behavioral_auth.db import open_db
    conn = open_db(cfg)
    conn.close()
    print(f'Schemat aktualny: {cfg.storage.db_path}')
    return 0


def main() -> None:
    p = argparse.ArgumentParser(
        prog='behavioral-auth',
        description='Sterowanie demonem uwierzytelniania behawioralnego.')
    p.add_argument('--config', metavar='PATH')
    sub = p.add_subparsers(dest='command', required=True)

    sub.add_parser('status', help='pokaż aktualny stan').set_defaults(fn=cmd_status)

    r = sub.add_parser('reset', help='zmiana osoby przed urządzeniem — wzorzec od zera')
    r.add_argument('--yes', '-y', action='store_true', help='bez pytania o potwierdzenie')
    r.add_argument('--purge-data', action='store_true',
                   help='skasuj też zebrane dane behawioralne, nie tylko wzorzec')
    r.set_defaults(fn=cmd_reset)

    sub.add_parser('learn-more', help='doucz istniejący wzorzec (jawnie, nie automatycznie)'
                   ).set_defaults(fn=cmd_learn_more)
    sub.add_parser('pause', help='wstrzymaj punktację').set_defaults(fn=cmd_pause)
    sub.add_parser('resume', help='wznów punktację').set_defaults(fn=cmd_resume)
    sub.add_parser('db', help='utwórz/zmigruj bazę').set_defaults(fn=cmd_db)

    args = p.parse_args()
    if args.config:
        os.environ['BEHAVIORAL_AUTH_CONFIG'] = args.config

    sys.exit(args.fn(load_settings(), args))


if __name__ == '__main__':
    main()
