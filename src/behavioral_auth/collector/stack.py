"""Which hardware produced a window of behaviour.

The same person types differently on a laptop's built-in keyboard than on an
external one through a dock, and moves differently with a trackpad than with a
mouse. A pattern learned across a *mixture* of both has a wider spread, so its
threshold lands higher, so it is **more permissive** than a pattern learned on
either stack alone — mixing hardware does not merely cause false alarms after a
dock, it widens the gate an impostor has to pass. So a window has to remember
which stack it came from, and scoring has to refuse to compare across stacks.

Identity comes from evdev's ``vendor:product``, not from the device path
(``/dev/input/eventN`` renumbers between boots and re-plugs) and not from the
name (two identical keyboards would collide, which is harmless here, and a
renamed device would look new, which is not).

The fingerprint covers the devices that **contributed events to the window**,
not the devices *attached* to the machine. Fingerprinting what is attached would
make a docked laptop with the lid open a different stack from the same laptop
with the lid shut, for no behavioural reason at all.

Either half may be absent: a window of pure typing says nothing about which
mouse was plugged in, so it must not be judged on one. :func:`key_matches`
treats an absent half as "no evidence" rather than as a mismatch.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

#: Stands in for a modality that produced no events in the window.
ABSENT = '-'

#: What the Windows backend reports. pynput exposes a single global hook with no
#: device identity at all, so every event on Windows claims the same stack. The
#: gate is therefore inert there — see the note in the daemon.
WINDOWS_DEVICE_ID = 'win:global'


def device_id(vendor: int, product: int) -> str:
    """Stable identity for one input device."""
    return f'{vendor:04x}:{product:04x}'


def stack_key(keyboard: str | None, mouse: str | None) -> str:
    """The stack a window came from, as a storable string."""
    return f'{keyboard or ABSENT}/{mouse or ABSENT}'


def split_key(key: str) -> tuple[str | None, str | None]:
    """Inverse of :func:`stack_key`; absent halves come back as None."""
    kbd, _, mouse = key.partition('/')
    return (None if kbd == ABSENT else kbd,
            None if mouse == ABSENT else mouse)


def key_matches(key: str, enrolled: Iterable[str]) -> bool:
    """Is *key* consistent with any stack the pattern was trained on?

    A window matches when some enrolled stack agrees on every half the window
    actually has. Typing with no mouse movement matches a docked enrolment and
    an undocked one alike, because it carries no evidence either way — and
    suspending scoring over that would make the gate fire on nothing.
    """
    kbd, mouse = split_key(key)
    if kbd is None and mouse is None:
        return True                       # an empty window; nothing to disagree with
    for other in enrolled:
        e_kbd, e_mouse = split_key(other)
        if kbd is not None and kbd != e_kbd:
            continue
        if mouse is not None and mouse != e_mouse:
            continue
        return True
    return False


def subsumes(general: str, specific: str) -> bool:
    """Does *specific* say everything *general* says, and more?

    ``kbd/-`` and ``kbd/mouse`` are not two hardware stacks. They are the same
    stack seen through a window of pure typing and a window that also moved the
    mouse, and both occur constantly on any single unchanged setup.
    """
    g_kbd, g_mouse = split_key(general)
    s_kbd, s_mouse = split_key(specific)
    if g_kbd is not None and g_kbd != s_kbd:
        return False
    if g_mouse is not None and g_mouse != s_mouse:
        return False
    return general != specific


def consolidate(keys: Iterable[str]) -> list[str]:
    """Collapse an observed set of stack keys to the distinct stacks in it.

    Without this, counting the set would report a laptop that was never docked
    as "trained across 2 hardware stacks" — because typing without touching the
    mouse produces its own key — and the warning about mixtures being permissive
    would fire for everybody, which is the fastest way to make it ignored.
    """
    uniq = sorted(set(keys))
    return [k for k in uniq if not any(subsumes(k, other) for other in uniq)]


def newly_seen(previous: Iterable[str], current: Iterable[str]) -> list[str]:
    """Stacks in *current* that are genuinely new, not merely better observed.

    The trap this exists to avoid is the one :func:`consolidate` was written for,
    arriving from the other direction. A setup first seen through windows of pure
    typing reports ``kbd/-``; the moment the mouse moves, the consolidated set
    becomes ``kbd/mouse`` and ``kbd/-`` disappears. A plain set difference calls
    that a second hardware stack and would announce a change of hardware every
    time somebody touched their mouse for the first time in a session.

    So a key counts as new only when no previously known key is the same stack
    seen less completely.
    """
    prev = list(previous)
    return [k for k in current
            if not any(k == p or subsumes(p, k) for p in prev)]


def short_fp(key: str) -> str:
    """A stable, short handle for a stack — what goes to the SIEM.

    Hashed on purpose: a SIEM needs to correlate "this stack again" without the
    daemon turning into a hardware inventory. Wazuh's syscollector already does
    inventory, and this project's forwarding rule is verdicts and numbers.
    """
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def describe(key: str) -> str:
    """A one-line, human-readable form for logs and `status`."""
    kbd, mouse = split_key(key)
    return f'keyboard {kbd or "—"}, mouse {mouse or "—"}'
