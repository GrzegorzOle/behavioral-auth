"""Live console block.

Redrawn in place, so the current state is always visible without scrolling.
Log lines are printed *above* the block: the console renderer and loguru both
write to the same terminal, and without a shared lock they interleave into
garbage. Under systemd (no TTY) this whole module is a no-op and everything
goes to the journal instead.
"""

from __future__ import annotations

import shutil
import sys
import threading
import time

from behavioral_auth.daemon.state import Snapshot, State

GREEN, YELLOW, RED, BLUE, DIM, BOLD, RESET = (
    '\033[92m', '\033[93m', '\033[91m', '\033[94m', '\033[2m', '\033[1m', '\033[0m')

_STATE_STYLE = {
    State.BOOTSTRAP.value: (BLUE, 'START'),
    State.LEARNING.value: (BLUE, 'NAUKA'),
    State.MONITORING.value: (GREEN, 'NADZÓR'),
    State.ALARM.value: (RED, 'ALARM'),
    State.PAUSED.value: (DIM, 'PAUZA'),
}

_SPARK = '▁▂▃▄▅▆▇█'


def bar(value: float, total: float, width: int = 18) -> str:
    frac = 0.0 if total <= 0 else max(0.0, min(value / total, 1.0))
    filled = int(frac * width)
    return '█' * filled + '░' * (width - filled)


def sparkline(values: list[float], scale: float = 2.0) -> str:
    if not values:
        return ''
    out = []
    for v in values[-30:]:
        i = int(min(max(v, 0.0), scale) / scale * (len(_SPARK) - 1))
        out.append(_SPARK[i])
    return ''.join(out)


def _hms(seconds: float) -> str:
    s = int(seconds)
    return f'{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}'


class Console:
    def __init__(self, mode: str = 'auto'):
        # A process with no console has sys.stdout set to None: a frozen Windows
        # service, or behavioral-authd started from Task Scheduler at logon,
        # which is the recommended fallback where a Session 0 service cannot see
        # the desktop. There is nothing to draw the status block on, so the
        # console is off whatever the mode asks for — `auto` would otherwise
        # crash on None.isatty(), and `always` on the first write.
        usable = sys.stdout is not None
        self.enabled = usable and (mode == 'always'
                                   or (mode == 'auto' and sys.stdout.isatty()))
        self.lock = threading.RLock()
        self.lines = 0
        self.started = time.monotonic()

    def _clear(self) -> None:
        if self.lines:
            sys.stdout.write(f'\033[{self.lines}A')
            sys.stdout.write('\033[J')
            self.lines = 0

    def emit_log(self, message: str) -> None:
        """loguru sink: wipe the block, print the line, leave the block to redraw.

        Without this the cursor-up in _clear() would swallow log lines that
        landed underneath the block.
        """
        with self.lock:
            self._clear()
            sys.stdout.write(message)
            sys.stdout.flush()

    def render(self, snap: Snapshot) -> None:
        if not self.enabled:
            return
        with self.lock:
            self._clear()
            body = (self._learning(snap) if snap.state == State.LEARNING.value
                    else self._monitoring(snap))
            colour, label = _STATE_STYLE.get(snap.state, (DIM, snap.state))

            width = min(shutil.get_terminal_size((80, 24)).columns, 72)
            head = '─ behavioral-auth '
            tail = f' {label} ─'
            fill = max(1, width - len(head) - len(tail) - 2)
            out = [f'{colour}╭{head}{"─" * fill}{BOLD}{tail}{RESET}{colour}╮{RESET}']
            for line in body:
                out.append(f'{colour}│{RESET} {line}')
            out.append(f'{colour}╰{"─" * (width - 2)}╯{RESET}')

            sys.stdout.write('\n'.join(out) + '\n')
            sys.stdout.flush()
            self.lines = len(out)

    def _learning(self, s: Snapshot) -> list[str]:
        up = _hms(time.monotonic() - self.started)
        lines = [
            f'{DIM}wzorzec{RESET} {s.enrollment_id[:8] or "—"}   {DIM}czas{RESET} {up}',
            f'sekwencje {s.n_sequences:>5}/{s.min_sequences:<5} [{bar(s.n_sequences, s.min_sequences)}]',
            f'aktywność {s.active_minutes:>4.0f}m/{s.min_active_minutes:<4}m '
            f'[{bar(s.active_minutes, s.min_active_minutes)}]  godzin {s.distinct_hours}/{s.min_distinct_hours}',
        ]
        if s.face_min_samples:
            lines.append(
                f'twarz     {s.face_samples:>5}/{s.face_min_samples:<5} '
                f'[{bar(s.face_samples, s.face_min_samples)}]')
        lines.append(
            f'cykl {s.cycles_done}  seria stabilnych {s.stable_streak}/{s.stable_needed}'
            + (f'  następny za {s.next_cycle_in_sec}s' if s.next_cycle_in_sec else ''))
        if s.last_cycle:
            c = s.last_cycle
            mark = f'{GREEN}✓{RESET}' if c.get('stable') else f'{YELLOW}·{RESET}'
            lines.append(
                f'{mark} pass_rate {c["pass_rate"]:.2f}  err_ratio {c["error_ratio"]:.2f}  '
                f'separacja {c["separation"]:.1f}x')
        if s.blocked_by:
            lines.append(f'{DIM}czeka na: {", ".join(s.blocked_by[:3])}{RESET}')
        return lines

    def _monitoring(self, s: Snapshot) -> list[str]:
        up = _hms(time.monotonic() - self.started)
        ratio = f'{s.last_ratio:.2f}x' if s.last_ratio is not None else '—'
        lines = [
            f'{DIM}wzorzec{RESET} {s.enrollment_id[:8] or "—"}   {DIM}czas{RESET} {up}   '
            f'{DIM}twarz{RESET} {s.face_state}',
            f'odchylenie {ratio} od progu   {sparkline(s.recent_ratios)}',
        ]
        if s.state == State.ALARM.value:
            held = f' od {s.alarm_since}' if s.alarm_since else ''
            lines.append(
                f'{RED}{BOLD}OSOBA PRZY KLAWIATURZE NIE ODPOWIADA WZORCOWI{RESET}')
            lines.append(
                f'{RED}powód: {s.alarm_reason}{held}  '
                f'szczyt {s.alarm_peak_ratio:.2f}x{RESET}'
                if s.alarm_peak_ratio else f'{RED}powód: {s.alarm_reason}{RESET}')
            lines.append(f'{DIM}sesja NIE jest blokowana — to tylko ostrzeżenie{RESET}')
        else:
            lines.append(
                f'{GREEN}zachowanie zgodne ze wzorcem{RESET}   '
                f'{DIM}anomalie z rzędu {s.consec_anom}{RESET}')
        return lines

    def close(self) -> None:
        if self.enabled:
            with self.lock:
                sys.stdout.write('\n')
                sys.stdout.flush()
