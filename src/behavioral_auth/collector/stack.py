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

#: What the Windows backend reported before transports were distinguished.
#: pynput exposes a single global hook with no device identity, so every event
#: claimed one stack and the gate was inert. Rows and patterns written by those
#: builds still carry it, so it survives as a **legacy marker meaning "this build
#: could not tell"** — see :func:`_is_legacy`.
WINDOWS_DEVICE_ID = 'win:global'

#: Windows still has no per-device identity (that needs RawInput / WM_INPUT), but
#: it can say whether the session is at the physical console or delivered over
#: the network. RDP is not different hardware, it is a different transport, and
#: behaviour captured through it is the owner plus the link — see
#: collector/transport.py. Putting the transport in the stack key is what makes
#: the machinery already built here apply to it: a pattern learned at the console
#: simply does not accept RDP windows, so scoring suspends and says why instead
#: of inventing a verdict, and the SIEM hears about the gap.
WINDOWS_CONSOLE_ID = 'win:console'
WINDOWS_REMOTE_ID = 'win:rdp'

#: Halves that say the input arrived over a remote display protocol. Sequences
#: carrying one are never trained on: that is the whole point.
REMOTE_IDS = frozenset({WINDOWS_REMOTE_ID})


def windows_device_id(transport: str) -> str:
    """The device identity a Windows event claims, given its session transport."""
    from behavioral_auth.collector import transport as _t
    if transport == _t.CONSOLE:
        return WINDOWS_CONSOLE_ID
    if transport == _t.REMOTE:
        return WINDOWS_REMOTE_ID
    return WINDOWS_DEVICE_ID


def is_remote(key: str) -> bool:
    """Did either half of this stack arrive over a remote display protocol?"""
    return any(half in REMOTE_IDS for half in split_key(key))


def _is_legacy(half: str | None) -> bool:
    """Was this half written by a build that could not tell what it was?

    `win:global` carries no information at all, which is a different thing from
    ABSENT (`-`), and it needs the opposite treatment. ABSENT is deliberately not
    a wildcard on the *enrolled* side, or a pattern learned from device-less rows
    would accept everything. `win:global` has to be a wildcard in **both**
    directions, because every row and every pattern written before this change
    carries it: without that, upgrading would make an existing enrolment look
    like a second hardware stack and suspend scoring on the owner's own machine.

    It cannot make anything more permissive than it already was — a pattern made
    entirely of `win:global` accepted everything before this change too — and it
    disappears from a machine as soon as one fresh enrolment is made.
    """
    return half == WINDOWS_DEVICE_ID


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
        if kbd is not None and not _is_legacy(kbd) and not _is_legacy(e_kbd) \
                and kbd != e_kbd:
            continue
        if mouse is not None and not _is_legacy(mouse) and not _is_legacy(e_mouse) \
                and mouse != e_mouse:
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
    # A legacy half says nothing, so a `win:global` key is subsumed by any real
    # one. That is what stops an upgrade from turning one enrolment into a
    # two-stack (and therefore more permissive) pattern the moment the first
    # window carrying a real transport is written.
    if g_kbd is not None and not _is_legacy(g_kbd) and g_kbd != s_kbd:
        return False
    if g_mouse is not None and not _is_legacy(g_mouse) and g_mouse != s_mouse:
        return False
    return general != specific


def consolidate(keys: Iterable[str]) -> list[str]:
    """Collapse an observed set of stack keys to the distinct stacks in it.

    Without this, counting the set would report a laptop that was never docked
    as "trained across 2 hardware stacks" — because typing without touching the
    mouse produces its own key — and the warning about mixtures being permissive
    would fire for everybody, which is the fastest way to make it ignored.

    **Two keys can subsume each other**, and dropping both would be a disaster.
    `win:global/win:global` and `-/-` each say nothing, so since the legacy half
    became a two-way wildcard each subsumes the other; the first version of this
    dropped the pair and returned an EMPTY list for an enrolment made entirely of
    pre-upgrade rows. That reached production: the daemon read "no stacks yet",
    treated the first ordinary window as new hardware, and told the user to
    consider `reset`. Worse was waiting behind it — a pattern promoted with an
    empty stack list is entitled to judge nothing, so `key_matches` rejects every
    window and the frozen pattern suspends for ever.

    So a key is dropped only when another is *strictly* more specific, and a set
    of mutually equivalent keys keeps one representative. A non-empty input can
    no longer produce an empty output; a test pins that.
    """
    uniq = sorted(set(keys))
    kept: list[str] = []
    for k in uniq:
        if any(subsumes(k, other) and not subsumes(other, k) for other in uniq):
            continue                      # something says strictly more
        if any(subsumes(k, other) and subsumes(other, k) for other in kept):
            continue                      # an equivalent one is already kept
        kept.append(k)
    return kept


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


#: Windows identities are transports, not vendor:product pairs, so they read as
#: gibberish in a log unless they are spelled out.
_HUMAN = {
    WINDOWS_CONSOLE_ID: 'physical console',
    WINDOWS_REMOTE_ID: 'remote session (RDP)',
    WINDOWS_DEVICE_ID: 'Windows, transport not recorded',
}


def describe(key: str) -> str:
    """A one-line, human-readable form for logs and `status`."""
    kbd, mouse = split_key(key)
    if kbd is not None and kbd == mouse and kbd in _HUMAN:
        return _HUMAN[kbd]
    return (f'keyboard {_HUMAN.get(kbd, kbd) or "—"}, '
            f'mouse {_HUMAN.get(mouse, mouse) or "—"}')
