"""RDP is a different transport, and the pattern must not be learned from it.

The keystroke dwell and flight times the model reads are rewritten by network
latency and input batching, so behaviour captured remotely is the owner *plus the
link* — and the link varies minute to minute. The hardware-stack gate was built
for exactly this shape of problem; putting the transport into the stack key is
what makes it apply.
"""

from __future__ import annotations

import sys
import uuid

import pytest

from behavioral_auth.collector import transport
from behavioral_auth.collector.stack import (
    WINDOWS_CONSOLE_ID,
    WINDOWS_DEVICE_ID,
    WINDOWS_REMOTE_ID,
    consolidate,
    is_remote,
    key_matches,
    newly_seen,
    stack_key,
    windows_device_id,
)

_CONSOLE = stack_key(WINDOWS_CONSOLE_ID, WINDOWS_CONSOLE_ID)
_RDP = stack_key(WINDOWS_REMOTE_ID, WINDOWS_REMOTE_ID)
_LEGACY = stack_key(WINDOWS_DEVICE_ID, WINDOWS_DEVICE_ID)

# enrollment_id and session_id are UUID columns.
EID = str(uuid.uuid4())
SID = str(uuid.uuid4())


# ── detection ────────────────────────────────────────────────────────────────

def test_the_transport_maps_to_a_device_identity():
    assert windows_device_id(transport.CONSOLE) == WINDOWS_CONSOLE_ID
    assert windows_device_id(transport.REMOTE) == WINDOWS_REMOTE_ID


def test_an_undetermined_transport_falls_back_to_the_legacy_marker():
    """"Could not tell" must not masquerade as "console". It is the legacy value,
    which is treated as no evidence rather than as evidence of being local."""
    assert windows_device_id(transport.UNKNOWN) == WINDOWS_DEVICE_ID


def test_detection_never_raises():
    assert transport.current() in (transport.CONSOLE, transport.REMOTE, transport.UNKNOWN)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows session APIs')
def test_windows_answers_console_or_rdp_not_unknown():
    """If this ever returns UNKNOWN on a real Windows desktop the detection has
    broken, and the daemon would silently go back to learning from RDP."""
    assert transport.current() in (transport.CONSOLE, transport.REMOTE)


def test_linux_has_nothing_to_detect():
    """evdev reads the kernel's local devices; a remote session does not deliver
    input through them at all, so there is no question to answer."""
    if sys.platform != 'win32':
        assert transport.current() == transport.UNKNOWN


# ── what the stack gate then does with it ────────────────────────────────────

def test_remote_input_is_recognisable():
    assert is_remote(_RDP) is True
    assert is_remote(_CONSOLE) is False
    assert is_remote(_LEGACY) is False


def test_a_console_pattern_does_not_accept_rdp_windows():
    """The whole mechanism. Scoring suspends and says why, instead of inventing a
    verdict by comparing against something the pattern never saw."""
    assert key_matches(_RDP, [_CONSOLE]) is False


def test_a_console_pattern_accepts_console_windows():
    assert key_matches(_CONSOLE, [_CONSOLE]) is True


def test_rdp_and_console_are_two_distinct_stacks():
    assert consolidate([_CONSOLE, _RDP]) == sorted([_CONSOLE, _RDP])


# ── the upgrade path, which is where this could do real damage ───────────────

def test_a_legacy_pattern_still_accepts_everything_it_did_before():
    """Every row and pattern written before this change carries `win:global`.
    Without a two-way wildcard, installing this build would make an existing
    enrolment look like foreign hardware and suspend scoring on the owner's own
    machine — a self-inflicted outage on upgrade."""
    assert key_matches(_CONSOLE, [_LEGACY]) is True
    assert key_matches(_RDP, [_LEGACY]) is True


def test_a_legacy_window_is_accepted_by_a_new_pattern():
    assert key_matches(_LEGACY, [_CONSOLE]) is True


def test_upgrading_mid_enrolment_does_not_manufacture_a_second_stack():
    """The dangerous case, and the reason legacy is subsumed rather than merely
    matched: an enrolment collected as `win:global` that continues as
    `win:console` must stay ONE stack. Two would make the frozen pattern wider,
    so more permissive, and would fire the enrollment_stack_added warning at
    somebody who changed nothing."""
    assert consolidate([_LEGACY, _CONSOLE]) == [_CONSOLE]
    assert newly_seen([_LEGACY], [_CONSOLE]) == []


def test_legacy_cannot_make_anything_more_permissive_than_it_already_was():
    """A pattern made entirely of win:global accepted every window before this
    change too, so the shim adds no reach. It disappears at the first fresh
    enrolment."""
    assert key_matches(_CONSOLE, [_LEGACY]) is key_matches(_RDP, [_LEGACY])


# ── nothing remote may reach training, or a promotion gate ───────────────────

def _seq_row(conn, eid, ns, stack):
    import json
    conn.execute(
        'INSERT INTO fused_sequences (enrollment_id, session_id, seq_end_ns, '
        'seq_len, data_json, dedup_key, stack_fp) VALUES (?, ?, ?, ?, ?, ?, ?)',
        [eid, SID, ns, 4, json.dumps([[0.0] * 21] * 4), ns, stack])


def _window_row(conn, eid, ns, stack):
    conn.execute(
        'INSERT INTO feature_windows (enrollment_id, session_id, window_start_ns, '
        'window_end_ns, source, stack_fp) VALUES (?, ?, ?, ?, ?, ?)',
        [eid, SID, ns, ns + 1, 'test', stack])


def test_remote_sequences_are_not_trained_on(conn):
    from behavioral_auth.training import dataset
    _seq_row(conn, EID, 1, _CONSOLE)
    _seq_row(conn, EID, 2, _RDP)
    _seq_row(conn, EID, 3, _CONSOLE)
    assert len(dataset.load_sequences(conn, EID)) == 2


def test_remote_sequences_do_not_count_toward_the_volume_gate(conn):
    """A gate satisfied by data the model is never allowed to see would be
    satisfied on false evidence — the same defect as the inflated active_minutes
    that let an all-zero-window enrolment pass its volume gates."""
    from behavioral_auth.training import dataset
    _seq_row(conn, EID, 1, _CONSOLE)
    for ns in range(2, 12):
        _seq_row(conn, EID, ns, _RDP)
    assert dataset.count_sequences(conn, EID) == 1


def test_remote_windows_do_not_count_toward_active_minutes(conn, cfg):
    from behavioral_auth.training import dataset
    _window_row(conn, EID, 1, _CONSOLE)
    _window_row(conn, EID, 2, _RDP)
    _window_row(conn, EID, 3, _RDP)
    only_console = dataset.active_minutes(conn, EID, cfg.features.stride_sec)
    _window_row(conn, EID, 4, _CONSOLE)
    assert dataset.active_minutes(conn, EID, cfg.features.stride_sec) > only_console


def test_a_frozen_pattern_never_claims_a_remote_stack(conn):
    """trained_stacks is what the pattern is entitled to judge. Listing RDP there
    would let it score remote windows against a model that never saw one."""
    from behavioral_auth.training import dataset
    _seq_row(conn, EID, 1, _CONSOLE)
    _seq_row(conn, EID, 2, _RDP)
    assert dataset.trained_stacks(conn, EID) == [_CONSOLE]


def test_legacy_rows_are_still_trained_on(conn):
    """The shim must not accidentally exclude every pre-upgrade row and empty the
    running enrolment."""
    from behavioral_auth.training import dataset
    _seq_row(conn, EID, 1, _LEGACY)
    _seq_row(conn, EID, 2, _LEGACY)
    assert len(dataset.load_sequences(conn, EID)) == 2


# ── consolidate() must never empty a non-empty set ───────────────────────────
#
# This reached production. Once `win:global` became a two-way wildcard it and
# `-/-` subsumed each other, consolidate dropped BOTH, and an enrolment made
# entirely of pre-upgrade rows reported no stacks at all. The daemon then read
# the first ordinary window as new hardware and advised `reset` — on 1113
# sequences that were perfectly fine.

def test_two_keys_that_say_nothing_collapse_to_one_not_to_none():
    out = consolidate([_LEGACY, '-/-'])
    assert len(out) == 1, f'a mutually-subsuming pair vanished: {out}'


@pytest.mark.parametrize('keys', [
    [_LEGACY, '-/-'],
    [_LEGACY, '-/-', 'win:global/-', '-/win:global'],
    ['-/-'],
    [_CONSOLE, _LEGACY, '-/-'],
    [_CONSOLE, '-/win:console', _LEGACY, '-/-', 'win:global/-'],
])
def test_a_non_empty_set_never_consolidates_to_nothing(keys):
    assert consolidate(keys), f'consolidate({keys}) emptied the set'


def test_the_seed_of_a_pre_upgrade_enrolment_is_not_empty():
    """The exact production shape: every sequence written by a build that could
    not name the transport. If this is empty, the next real window looks like new
    hardware."""
    previous = consolidate([_LEGACY, 'win:global/-', '-/win:global', '-/-'])
    assert previous
    assert newly_seen(previous, consolidate([_LEGACY, '-/win:console'])) == []


def test_an_empty_stack_list_would_make_a_frozen_pattern_judge_nothing():
    """Why the above matters beyond a noisy log line. trained_stacks() feeds the
    promoted pattern's `stacks`, and a pattern entitled to judge nothing rejects
    every window and suspends for ever."""
    assert key_matches(_CONSOLE, []) is False


def test_a_strictly_more_specific_key_still_wins():
    """The behaviour consolidate existed for in the first place must survive the
    fix: kbd/- and kbd/mouse are one stack, not two."""
    assert consolidate(['k1/-', 'k1/m1']) == ['k1/m1']
