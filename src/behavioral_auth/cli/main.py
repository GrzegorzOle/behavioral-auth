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
import time

from behavioral_auth import __version__, updates
from behavioral_auth.collector.windows_source import (
    INJECTION_MIN_SAMPLE,
    INJECTION_WARN_SHARE,
)
from behavioral_auth.config import load_settings
from behavioral_auth.daemon import commands, control
from behavioral_auth.daemon.console import BOLD, DIM, GREEN, RED, RESET, YELLOW, bar, sparkline
from behavioral_auth.daemon.state import State, read_snapshot

_STATE_LABEL = {
    State.LEARNING.value: (f'{DIM}●{RESET}', 'NAUKA — buduje wzorzec'),
    State.MONITORING.value: (f'{GREEN}●{RESET}', 'NADZÓR — wzorzec zamrożony'),
    State.ALARM.value: (f'{RED}●{RESET}', 'ALARM — osoba nie odpowiada wzorcowi'),
    State.PAUSED.value: (f'{DIM}○{RESET}', 'PAUZA'),
    State.SUSPENDED.value: (f'{YELLOW}○{RESET}', 'ZAWIESZONY — inny sprzęt niż w nauce'),
    State.BOOTSTRAP.value: (f'{DIM}○{RESET}', 'START'),
}


def _set_config(path: str) -> None:
    """Honour --config, or refuse — never silently resolve somewhere else.

    config_path() treats BEHAVIORAL_AUTH_CONFIG as the first *candidate* and
    falls through to the machine-wide file when it does not exist. That fallback
    is deliberate and load-bearing for the installer (a frozen bundle must still
    run when ProgramData has been emptied), but it is wrong for a path a human
    just typed: `--config scratch.yaml` with a typo in it resolved to the real
    ProgramData config, so a command aimed at a throwaway database would operate
    on the live pattern. With `reset` among the commands, that is a foot-gun
    rather than an inconvenience.

    Checked here rather than in config_path(), so the environment keeps the
    fallback and only the explicit argument becomes strict.
    """
    if not os.path.exists(path):
        raise SystemExit(f'--config: nie ma takiego pliku: {path}')
    os.environ['BEHAVIORAL_AUTH_CONFIG'] = path


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


def _print_injection(snap: dict) -> None:
    """How much of the captured input was synthesised rather than typed.

    Shown even at 0 %, unlike the update notice, because here the *absence* of a
    number is the thing a reader would misread: silence would be indistinguishable
    from a platform that never looked. `—` says exactly that, and it is what
    Linux shows, since evdev has no equivalent of the injected flag.
    """
    inj = snap.get('injection')
    if inj is None:
        return
    kbd, ms = inj.get('keyboard_share', 0.0), inj.get('mouse_share', 0.0)
    loud = max(kbd, ms) >= INJECTION_WARN_SHARE and max(
        inj.get('keyboard_total', 0), inj.get('mouse_total', 0)) >= INJECTION_MIN_SAMPLE
    colour = YELLOW if loud else DIM
    print(f'  {colour}wstrzyknięte{RESET} klawiatura {kbd:.1%}, mysz {ms:.1%}'
          + (f'  {YELLOW}← coś syntetyzuje wejście na tej maszynie{RESET}' if loud else ''))


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
    print(f'  {DIM}wzorzec{RESET} {(snap.get("enrollment_id") or "—")[:8]}   '
          f'{DIM}wersja{RESET} {__version__}')
    _print_injection(snap)

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

        stacks = snap.get('pattern_stacks') or []
        if len(stacks) > 1:
            print(f'  {YELLOW}wzorzec uczony na {len(stacks)} zestawach sprzętu — '
                  f'jest przez to bardziej pobłażliwy{RESET}')
        if snap['state'] == State.SUSPENDED.value:
            print()
            print(f'  {YELLOW}{BOLD}NIE PUNKTUJE{RESET}  bieżący sprzęt nie jest tym, '
                  f'na którym wzorzec był uczony')
            print(f'  {DIM}zbieranie danych trwa. "behavioral-auth learn-more" dołączy '
                  f'ten zestaw do wzorca —{RESET}')
            print(f'  {DIM}kosztem tego, że wzorzec obejmujący dwa zestawy jest '
                  f'bardziej pobłażliwy{RESET}')

        if snap['state'] == State.ALARM.value:
            print()
            print(f'  {RED}{BOLD}ALARM{RESET}  powód: {snap.get("alarm_reason")}  '
                  f'szczyt {snap.get("alarm_peak_ratio", 0):.2f}x')
            print(f'  {DIM}sesja NIE została zablokowana — ten system tylko ostrzega{RESET}')

    # Read from disk, never fetched here: `status` must stay a local, instant,
    # offline command. Whatever the daemon last found is what gets shown.
    upd = updates.read_status(cfg.daemon.run_dir)
    notice = updates.describe(upd)
    if notice:
        print()
        print(f'  {YELLOW}{notice}{RESET}')
        if upd.url:
            print(f'  {DIM}{upd.url}{RESET}')
    print()
    return 0


def cmd_check_update(cfg, args) -> int:
    """Ask now whether a newer release exists, and only say so.

    Runs whatever `updates.check_enabled` says. That flag governs the *daemon's*
    unattended check — the one that would otherwise reach the network with
    nobody present — and typing this command is itself the decision it would
    have made on your behalf.

    Nothing is downloaded here. The upgrade is a file you fetch and run
    yourself, which on Windows also means you are present to undo what the
    installer does to the service afterwards.
    """
    status = updates.check(cfg)
    updates.write_status(cfg.daemon.run_dir, status)

    print(f'zainstalowana: {__version__}')
    if status.error:
        print(f'{YELLOW}nie udało się sprawdzić:{RESET} {status.error}')
        return 1
    print(f'najnowsza opublikowana: {status.latest}')
    if not status.update_available:
        print('Nie masz nic do zrobienia.')
        return 0
    if status.url:
        print(f'  {status.url}')
    print('Nic nie zostało pobrane ani uruchomione — instalacja jest ręczna, '
          'celowo.')
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


def cmd_rebuild_features(cfg, args) -> int:
    """Recompute the feature windows from the raw events kept underneath them.

    Not routed through _dispatch, and it refuses while a daemon runs rather than
    asking one to do it: DuckDB's write lock is exclusive, and a rebuild racing a
    live collector would interleave deletes with inserts.
    """
    if control.daemon_running(cfg.daemon.run_dir):
        print('Demon działa i trzyma bazę. Zatrzymaj go najpierw: behavioral-auth stop')
        return 1
    if not args.yes:
        print('To skasuje wyliczone okna cech, sekwencje i cykle nauki bieżącego wzorca,')
        print('po czym policzy je od nowa z zachowanych surowych zdarzeń.')
        print('Zebrane zachowanie NIE ginie — ginie tylko to, co z niego wyliczono.')
        if input('Na pewno? [t/N] ').strip().lower() not in ('t', 'tak', 'y', 'yes'):
            print('Anulowano.')
            return 1

    from behavioral_auth.db import open_db
    conn = open_db(cfg)
    try:
        print(commands.rebuild_features(conn, cfg))
    finally:
        conn.close()
    return 0


def cmd_stop(cfg, args) -> int:
    """Shut the daemon down cleanly, and wait to see that it actually went.

    Not routed through _dispatch: its no-daemon fallback opens the database and
    runs migrations, which is exactly the wrong thing to do on behalf of a
    command whose whole subject is a running process.

    Waiting matters more than it looks. The daemon notices the flag on its next
    tick, so "stop" returns long before the process is gone — and the caller is
    usually about to do the thing the stop was for (connect over RDP, install a
    new build) where a daemon still holding DuckDB's lock is the problem.
    """
    run_dir = cfg.daemon.run_dir
    if not control.daemon_running(run_dir):
        print('Demon nie działa — nie ma czego zatrzymywać.')
        return 1

    result = control.send(run_dir, 'stop', {})
    print(result.get('message', ''))
    if not result.get('ok'):
        return 1

    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if not control.daemon_running(run_dir):
            print(f'{GREEN}Zatrzymany.{RESET}')
            return 0
        time.sleep(0.5)

    print(f'{YELLOW}Nadal działa po {args.timeout:.0f}s.{RESET} Sprawdź '
          f'behavioral-auth.log — zamykanie mogło się zawiesić.')
    return 1


def cmd_pause(cfg, args) -> int:
    return _dispatch(cfg, 'pause', {})


def cmd_resume(cfg, args) -> int:
    return _dispatch(cfg, 'resume', {})


def cmd_set_profile(cfg, args) -> int:
    """Swap the synthetic person mid-run — the only way to reach ALARM in a demo.

    A no-op against a daemon reading real devices: the command needs a
    SyntheticSource, so the daemon answers 'nieznana komenda' without one.
    """
    return _dispatch(cfg, 'set-profile', {'profile': args.profile})


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
    p.add_argument('--version', action='version', version=f'behavioral-auth {__version__}')
    sub = p.add_subparsers(dest='command', required=True)

    sub.add_parser('status', help='pokaż aktualny stan').set_defaults(fn=cmd_status)

    r = sub.add_parser('reset', help='zmiana osoby przed urządzeniem — wzorzec od zera')
    r.add_argument('--yes', '-y', action='store_true', help='bez pytania o potwierdzenie')
    r.add_argument('--purge-data', action='store_true',
                   help='skasuj też zebrane dane behawioralne, nie tylko wzorzec')
    r.set_defaults(fn=cmd_reset)

    sub.add_parser('learn-more', help='doucz istniejący wzorzec (jawnie, nie automatycznie)'
                   ).set_defaults(fn=cmd_learn_more)
    st = sub.add_parser('stop', help='zatrzymaj demona czysto (zbieranie ustaje)')
    st.add_argument('--timeout', type=float, default=30.0, metavar='SEC',
                    help='ile czekać na wyjście procesu (domyślnie 30)')
    st.set_defaults(fn=cmd_stop)
    sub.add_parser('pause', help='wstrzymaj punktację').set_defaults(fn=cmd_pause)
    sub.add_parser('resume', help='wznów punktację').set_defaults(fn=cmd_resume)
    rb = sub.add_parser('rebuild-features',
                        help='przelicz okna cech z surowych zdarzeń (po poprawce '
                             'w ekstrakcji; wymaga zatrzymanego demona)')
    rb.add_argument('--yes', '-y', action='store_true', help='bez pytania o potwierdzenie')
    rb.set_defaults(fn=cmd_rebuild_features)
    sub.add_parser('db', help='utwórz/zmigruj bazę').set_defaults(fn=cmd_db)
    sub.add_parser('check-update',
                   help='sprawdź, czy jest nowsza wersja (tylko informuje — '
                        'nic nie pobiera ani nie instaluje)'
                   ).set_defaults(fn=cmd_check_update)

    sp = sub.add_parser('set-profile',
                        help='podmień profil syntetyczny (tylko --synthetic-input)')
    sp.add_argument('profile', choices=['user', 'impostor'])
    sp.set_defaults(fn=cmd_set_profile)

    args = p.parse_args()
    if args.config:
        _set_config(args.config)

    sys.exit(args.fn(load_settings(), args))


if __name__ == '__main__':
    main()
