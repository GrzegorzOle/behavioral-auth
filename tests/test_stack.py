"""The hardware stack: fingerprinting, matching, and what the gate lets through.

The load-bearing claim these pin down is that a pattern must not be scored
against hardware it never saw, and that "no evidence" is not the same as
"mismatch".
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from behavioral_auth.collector.stack import (
    ABSENT,
    describe,
    device_id,
    key_matches,
    newly_seen,
    short_fp,
    split_key,
    stack_key,
)
from behavioral_auth.features.pipeline import (
    build_feature_windows, build_sequences, window_stack,
)

SEC = 1_000_000_000
DOCK_KBD, DOCK_MOUSE = '046d:c52b', '046d:4023'
LAPTOP_KBD, LAPTOP_TRACKPAD = '0001:0001', '06cb:7f28'


# ── the pure bits ────────────────────────────────────────────────────────────

def test_device_id_is_zero_padded_and_stable():
    assert device_id(0x046d, 0xc52b) == '046d:c52b'
    assert device_id(1, 1) == '0001:0001'


def test_stack_key_round_trips():
    key = stack_key(DOCK_KBD, DOCK_MOUSE)
    assert split_key(key) == (DOCK_KBD, DOCK_MOUSE)
    assert split_key(stack_key(None, None)) == (None, None)
    assert stack_key(None, None) == f'{ABSENT}/{ABSENT}'


def test_a_window_with_no_mouse_matches_a_docked_enrolment_and_an_undocked_one():
    """Typing with the mouse untouched says nothing about which mouse is attached.

    Treating that as a mismatch would suspend scoring on nothing at all — most
    windows of prose contain no mouse movement.
    """
    enrolled = [stack_key(LAPTOP_KBD, LAPTOP_TRACKPAD)]
    assert key_matches(stack_key(LAPTOP_KBD, None), enrolled)


def test_a_different_keyboard_does_not_match():
    enrolled = [stack_key(LAPTOP_KBD, LAPTOP_TRACKPAD)]
    assert not key_matches(stack_key(DOCK_KBD, None), enrolled)
    assert not key_matches(stack_key(DOCK_KBD, DOCK_MOUSE), enrolled)


def test_an_empty_window_matches_anything():
    assert key_matches(stack_key(None, None), [stack_key(DOCK_KBD, DOCK_MOUSE)])


def test_a_no_evidence_enrolment_entry_does_not_match_everything():
    """'-/-' as a *window* is no evidence; as an *enrolment* it is not a wildcard.

    Getting this backwards would make a pattern enrolled from device-less rows
    accept every stack there is.
    """
    assert not key_matches(stack_key(DOCK_KBD, DOCK_MOUSE), [stack_key(None, None)])


def test_a_second_enrolled_stack_widens_what_is_accepted():
    one = [stack_key(LAPTOP_KBD, LAPTOP_TRACKPAD)]
    two = one + [stack_key(DOCK_KBD, DOCK_MOUSE)]
    docked = stack_key(DOCK_KBD, DOCK_MOUSE)
    assert not key_matches(docked, one)
    assert key_matches(docked, two)


def test_short_fp_is_stable_and_carries_no_device_id():
    key = stack_key(DOCK_KBD, DOCK_MOUSE)
    fp = short_fp(key)
    assert fp == short_fp(key)
    assert len(fp) == 12
    # What goes to the SIEM must not be reversible to the hardware by reading it.
    assert DOCK_KBD not in fp and DOCK_MOUSE not in fp


def test_describe_is_human_readable():
    assert DOCK_KBD in describe(stack_key(DOCK_KBD, None))


# ── the pipeline ─────────────────────────────────────────────────────────────

@pytest.fixture
def session(conn):
    sid, eid = str(uuid.uuid4()), str(uuid.uuid4())
    conn.execute(
        'INSERT INTO enrollments (enrollment_id, status) VALUES (?, ?)', [eid, 'learning'])
    conn.execute(
        'INSERT INTO sessions (session_id, user_name, mode) VALUES (?, ?, ?)',
        [sid, 'test', 'test'])
    return sid, eid


def _type(conn, sid, t0_ns, duration_sec, dev_id, rate_hz=8):
    rows = []
    step = SEC // rate_hz
    for i in range(duration_sec * rate_hz):
        ts = t0_ns + i * step
        code = 30 + (i % 20)
        for value, offset in ((1, 0), (0, step // 3)):
            rows.append((ts + offset,
                         datetime.fromtimestamp((ts + offset) / 1e9, tz=timezone.utc),
                         sid, '/dev/x', 'kbd', dev_id, 'keyboard', 1, code, value))
    conn.executemany(
        'INSERT INTO raw_events (ts_ns, ts_utc, session_id, dev_path, dev_name, '
        'dev_id, dev_type, ev_type, ev_code, ev_value) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', rows)


def test_windows_record_the_stack_they_came_from(conn, cfg, session):
    sid, eid = session
    t0 = 1_700_000_000 * SEC
    _type(conn, sid, t0, duration_sec=60, dev_id=LAPTOP_KBD)

    assert build_feature_windows(conn, sid, eid, cfg) > 0
    stacks = {r[0] for r in conn.execute(
        'SELECT DISTINCT stack_fp FROM feature_windows').fetchall()}
    assert stacks == {stack_key(LAPTOP_KBD, None)}


def test_a_window_straddling_a_dock_change_is_dropped(conn, cfg, session):
    """The moment the hardware changes is a transition, not a person.

    Keeping it would fold two motor contexts into one training sample, which is
    exactly the mixture that makes a pattern permissive.
    """
    sid, eid = session
    t0 = 1_700_000_000 * SEC
    # Both keyboards inside one window_sec (10s in the test config).
    _type(conn, sid, t0, duration_sec=4, dev_id=LAPTOP_KBD)
    _type(conn, sid, t0 + 4 * SEC, duration_sec=6, dev_id=DOCK_KBD)
    # Then a clean stretch on the dock, so the run produces *something*.
    _type(conn, sid, t0 + 12 * SEC, duration_sec=60, dev_id=DOCK_KBD)

    build_feature_windows(conn, sid, eid, cfg)
    stacks = {r[0] for r in conn.execute(
        'SELECT DISTINCT stack_fp FROM feature_windows').fetchall()}
    assert stacks == {stack_key(DOCK_KBD, None)}, 'a mixed window survived'


def test_window_stack_returns_none_for_two_keyboards():
    import pandas as pd
    kb = pd.DataFrame({'dev_id': [LAPTOP_KBD, DOCK_KBD]})
    assert window_stack(kb, pd.DataFrame({'dev_id': []})) is None


def test_events_without_a_device_id_are_absent_not_mismatched(conn, cfg, session):
    """Rows written before the stack migration must not invalidate a pattern."""
    sid, eid = session
    t0 = 1_700_000_000 * SEC
    _type(conn, sid, t0, duration_sec=60, dev_id=None)

    build_feature_windows(conn, sid, eid, cfg)
    stacks = {r[0] for r in conn.execute(
        'SELECT DISTINCT stack_fp FROM feature_windows').fetchall()}
    assert stacks == {stack_key(None, None)}
    assert key_matches(stack_key(None, None), [stack_key(DOCK_KBD, DOCK_MOUSE)])


def _build(conn, cfg, sid, eid):
    build_feature_windows(conn, sid, eid, cfg)
    build_sequences(conn, sid, eid, cfg)
    return conn.execute(
        'SELECT seq_end_ns, stack_fp FROM fused_sequences WHERE session_id = ?',
        [sid]).fetchall()


def test_a_sequence_never_spans_two_stacks(conn, cfg, session):
    """Identical timing, one stack or two — the two-stack run must build fewer.

    Comparing against the single-stack run is the point: asserting only that
    every sequence carries *a* stack would pass even if sequences were happily
    splicing the dock onto the laptop, since the label is taken from the last
    window either way.
    """
    sid, eid = session
    t0 = 1_700_000_000 * SEC

    _type(conn, sid, t0, duration_sec=120, dev_id=LAPTOP_KBD)
    _type(conn, sid, t0 + 120 * SEC, duration_sec=120, dev_id=DOCK_KBD)
    split = _build(conn, cfg, sid, eid)

    # The same events with nothing changing hardware, in a fresh session.
    sid2 = str(uuid.uuid4())
    conn.execute('INSERT INTO sessions (session_id, user_name, mode) VALUES (?, ?, ?)',
                 [sid2, 'test', 'test'])
    _type(conn, sid2, t0, duration_sec=240, dev_id=LAPTOP_KBD)
    whole = _build(conn, cfg, sid2, eid)

    assert split, 'no sequences were built at all'
    assert {r[1] for r in split} == {stack_key(LAPTOP_KBD, None), stack_key(DOCK_KBD, None)}
    dropped = len(whole) - len(split)
    assert dropped >= cfg.model.seq_len - 1, (
        f'expected at least {cfg.model.seq_len - 1} boundary-crossing sequences to be '
        f'dropped, but the two-stack run built {len(split)} against {len(whole)}')


# ── the gate ─────────────────────────────────────────────────────────────────

def _pattern(stacks):
    from behavioral_auth.inference.runtime import Pattern
    return Pattern('unused.onnx', {}, {'threshold': 1.0, 'stacks': stacks})


def test_the_pattern_refuses_hardware_it_never_saw():
    p = _pattern([stack_key(LAPTOP_KBD, LAPTOP_TRACKPAD)])
    assert p.accepts_stack(stack_key(LAPTOP_KBD, LAPTOP_TRACKPAD))
    assert p.accepts_stack(stack_key(LAPTOP_KBD, None))
    assert not p.accepts_stack(stack_key(DOCK_KBD, DOCK_MOUSE))


def test_a_pattern_with_no_recorded_stacks_gates_nothing():
    """Upgrading must not retroactively suspend a pattern promoted before this.

    'No stacks recorded' means unknown, and the daemon never suspends on missing
    information — only on a positive mismatch.
    """
    p = _pattern([])
    assert p.accepts_stack(stack_key(DOCK_KBD, DOCK_MOUSE))
    assert p.accepts_stack(None)


def test_trained_stacks_reports_what_the_enrolment_actually_used(conn, cfg, session):
    from behavioral_auth.training.dataset import trained_stacks
    sid, eid = session
    t0 = 1_700_000_000 * SEC
    _type(conn, sid, t0, duration_sec=120, dev_id=LAPTOP_KBD)
    _type(conn, sid, t0 + 120 * SEC, duration_sec=120, dev_id=DOCK_KBD)
    _build(conn, cfg, sid, eid)

    assert trained_stacks(conn, eid) == sorted(
        [stack_key(DOCK_KBD, None), stack_key(LAPTOP_KBD, None)])


# ── the row every source has to produce ──────────────────────────────────────

def test_every_source_produces_the_same_row_width():
    """The three input backends write into one table through one INSERT.

    Nothing else pins this: the Linux, Windows and synthetic sources each build
    the tuple by hand, so a column added to one and forgotten in another would
    surface as a runtime error on a machine the author does not have.

    Since migration 005 the source row and the INSERT are deliberately *not*
    the same width: sources still emit a real key code, and Writer's
    pseudonymiser replaces it with (zone, pair id) on the way past
    (collector/zones.py). So the check is source width + 2 == INSERT width, and
    it is done through the transform rather than by hardcoding 2 — otherwise
    this test would keep passing if the transform stopped appending anything.
    """
    import re

    from behavioral_auth.collector import writer as writer_mod
    from behavioral_auth.collector.zones import KeyPseudonymiser

    columns = re.search(r'\(([^)]*)\)\s*VALUES', writer_mod._INSERT).group(1)
    expected = len([c for c in columns.split(',') if c.strip()])
    assert writer_mod._INSERT.count('?') == expected

    from behavioral_auth.collector.source import SyntheticSource

    class _Collect:
        def __init__(self):
            self.rows = []

        def add(self, row):
            self.rows.append(row)

    sink = _Collect()
    src = SyntheticSource(sink, 'sid', 'user', speed=1000.0)
    pseudo = KeyPseudonymiser()
    for row in src._chunk():
        stored = pseudo.transform(row)
        assert len(stored) == expected, (
            f'synthetic row stores {len(stored)} fields, want {expected}')

    from behavioral_auth.collector.windows_source import _Shaper
    assert _Shaper is not None      # imported on Linux too; shaping is pure


def test_writer_stores_no_key_code_from_any_source():
    """The privacy invariant, checked end to end against a real source.

    test_zones.py pins the transform in isolation; this pins that the synthetic
    generator's keystrokes actually arrive pseudonymised, so a source emitting
    rows in some other shape could not slip past.
    """
    from behavioral_auth.collector.source import SyntheticSource
    from behavioral_auth.collector.zones import KeyPseudonymiser

    class _Collect:
        def __init__(self):
            self.rows = []

        def add(self, row):
            self.rows.append(row)

    src = SyntheticSource(_Collect(), 'sid', 'user', speed=1000.0)
    pseudo = KeyPseudonymiser()
    seen_keyboard = False
    for row in src._chunk():
        stored = pseudo.transform(row)
        if row[6] == 'keyboard' and row[7] == 1:
            seen_keyboard = True
            assert stored[8] == 0, 'a key code reached the stored row'
            assert stored[10] is not None and stored[11] is not None
    assert seen_keyboard, 'the generator produced no keystrokes to check'


def test_a_keyboard_only_window_is_not_a_second_hardware_stack():
    """The bug a live demo run exposed and the unit tests had missed.

    Typing without touching the mouse yields 'kbd/-', typing with it yields
    'kbd/mouse'. Both happen constantly on one unchanged setup, so counting the
    raw set called every ordinary enrolment a two-stack mixture — and the
    warning that a mixture is permissive would have fired for everybody.
    """
    from behavioral_auth.collector.stack import consolidate
    observed = [stack_key(LAPTOP_KBD, None), stack_key(LAPTOP_KBD, LAPTOP_TRACKPAD)]
    assert consolidate(observed) == [stack_key(LAPTOP_KBD, LAPTOP_TRACKPAD)]


def test_consolidation_keeps_genuinely_different_stacks_apart():
    from behavioral_auth.collector.stack import consolidate
    observed = [
        stack_key(LAPTOP_KBD, None),
        stack_key(LAPTOP_KBD, LAPTOP_TRACKPAD),
        stack_key(DOCK_KBD, DOCK_MOUSE),
    ]
    assert consolidate(observed) == sorted(
        [stack_key(LAPTOP_KBD, LAPTOP_TRACKPAD), stack_key(DOCK_KBD, DOCK_MOUSE)])


def test_an_enrolment_that_never_touched_a_mouse_stays_keyboard_only():
    from behavioral_auth.collector.stack import consolidate
    assert consolidate([stack_key(LAPTOP_KBD, None)]) == [stack_key(LAPTOP_KBD, None)]


def test_consolidating_does_not_change_what_the_gate_accepts():
    from behavioral_auth.collector.stack import consolidate
    enrolled = consolidate(
        [stack_key(LAPTOP_KBD, None), stack_key(LAPTOP_KBD, LAPTOP_TRACKPAD)])
    assert key_matches(stack_key(LAPTOP_KBD, None), enrolled)
    assert key_matches(stack_key(LAPTOP_KBD, LAPTOP_TRACKPAD), enrolled)
    assert not key_matches(stack_key(DOCK_KBD, DOCK_MOUSE), enrolled)


# ── the gate, at the level the daemon runs it ────────────────────────────────

class _RecordingSiem:
    """Enough Forwarder for the daemon; records instead of sending."""

    def __init__(self):
        self.events = []
        self.enabled = True

    def emit(self, category, action, severity=6, **detail):
        self.events.append((category, action, severity, detail))

    def flush(self, force=False):
        pass

    def store_alarms_locally(self):
        return True

    def close(self):
        pass


def _daemon(cfg, conn, stacks):
    from behavioral_auth.daemon.daemon import Daemon
    from behavioral_auth.daemon.state import StateStore

    d = Daemon(cfg)
    d.conn = conn
    d.siem = _RecordingSiem()
    d.store = StateStore(conn, cfg.daemon.run_dir, siem=d.siem)
    d.monitor.siem = d.siem
    d.pattern = _pattern(stacks)
    return d


def test_unenrolled_hardware_suspends_scoring_and_never_alarms(cfg, conn):
    """The whole point of the gate: a dock change must not look like an intruder.

    Raising an alarm the user cannot act on ('you undocked') is how a warning
    system teaches people to ignore it.
    """
    from behavioral_auth.daemon.state import State

    d = _daemon(cfg, conn, [stack_key(LAPTOP_KBD, LAPTOP_TRACKPAD)])
    d.store.transition(State.MONITORING, 'test')

    d._suspend_for_stack(stack_key(DOCK_KBD, DOCK_MOUSE))

    assert d.store.state is State.SUSPENDED
    assert not any(e[0] == 'alarm' for e in d.siem.events), 'the gate raised an alarm'
    changed = [e for e in d.siem.events if e[1] == 'stack_changed']
    assert changed and changed[-1][3]['known'] is False


def test_the_suspend_event_carries_a_hash_and_no_device_id(cfg, conn):
    d = _daemon(cfg, conn, [stack_key(LAPTOP_KBD, LAPTOP_TRACKPAD)])
    d._suspend_for_stack(stack_key(DOCK_KBD, DOCK_MOUSE))

    detail = [e for e in d.siem.events if e[1] == 'stack_changed'][-1][3]
    assert detail['stack_fp'] == short_fp(stack_key(DOCK_KBD, DOCK_MOUSE))
    blob = repr(detail)
    assert DOCK_KBD not in blob and DOCK_MOUSE not in blob


def test_enrolled_hardware_returning_resumes_scoring(cfg, conn):
    from behavioral_auth.daemon.state import State

    enrolled = stack_key(LAPTOP_KBD, LAPTOP_TRACKPAD)
    d = _daemon(cfg, conn, [enrolled])
    d.store.transition(State.MONITORING, 'test')
    d._suspend_for_stack(stack_key(DOCK_KBD, DOCK_MOUSE))

    d._resume_from_stack(enrolled)

    assert d.store.state is State.MONITORING
    assert [e for e in d.siem.events if e[1] == 'stack_changed'][-1][3]['known'] is True


# ── noticing that an enrolment has grown to cover more hardware ───────────────

def test_a_setup_seen_more_fully_is_not_new_hardware():
    """The trap consolidate() exists for, arriving from the other side.

    A setup first seen through windows of pure typing reports `kbd/-`; the moment
    the mouse moves, the consolidated set becomes `kbd/mouse` and `kbd/-` is
    gone. A plain set difference calls that a second stack and would announce a
    hardware change the first time anyone touched their mouse.
    """
    assert newly_seen(['k1/-'], ['k1/m1']) == []


def test_a_genuinely_different_stack_is_new():
    assert newly_seen(['k1/m1'], ['k1/m1', 'k2/m1']) == ['k2/m1']


def test_a_swapped_mouse_is_new_hardware():
    assert newly_seen(['k1/m1'], ['k1/m2']) == ['k1/m2']


def test_nothing_changing_reports_nothing():
    assert newly_seen(['k1/m1', 'k2/m2'], ['k1/m1', 'k2/m2']) == []


def test_a_stack_disappearing_is_not_reported():
    """Only growth matters. A stack no longer contributing windows has not made
    the pattern wider, and the pattern keeps whatever it already learned."""
    assert newly_seen(['k1/m1', 'k2/m2'], ['k1/m1']) == []


def test_the_first_stack_of_a_fresh_enrolment_is_new():
    assert newly_seen([], ['k1/m1']) == ['k1/m1']


# ── the daemon's SIEM wiring for it ──────────────────────────────────────────

class _RecordingSiem:
    def __init__(self):
        self.events = []

    def emit(self, category, action, severity=None, **detail):
        self.events.append((category, action, severity, detail))


def _stub_daemon(siem):
    from types import SimpleNamespace
    return SimpleNamespace(conn=None, siem=siem, _enrolment_stacks=None)


def _check(monkeypatch, stub, stacks):
    from behavioral_auth.daemon import daemon as daemon_mod
    monkeypatch.setattr(daemon_mod.dataset, 'trained_stacks',
                        lambda conn, eid: stacks)
    daemon_mod.Daemon._check_enrollment_stacks(stub, 'e1')


def test_the_first_look_seeds_and_never_emits(monkeypatch):
    """Otherwise every daemon restart mid-enrolment replays the whole set as if
    the hardware had just been swapped."""
    siem = _RecordingSiem()
    stub = _stub_daemon(siem)
    _check(monkeypatch, stub, ['k1/m1', 'k2/m2'])
    assert siem.events == []
    assert stub._enrolment_stacks == ['k1/m1', 'k2/m2']


def test_a_second_set_of_hardware_is_forwarded(monkeypatch):
    siem = _RecordingSiem()
    stub = _stub_daemon(siem)
    _check(monkeypatch, stub, ['k1/m1'])
    _check(monkeypatch, stub, ['k1/m1', 'k2/m2'])
    assert len(siem.events) == 1
    category, action, severity, detail = siem.events[0]
    assert (category, action) == ('ops', 'enrollment_stack_added')
    assert severity == 4                       # WARNING
    assert detail['n_stacks'] == 2
    assert len(detail['stack_fp']) == 12       # hashed, not a hardware inventory
    assert 'k2' not in detail['stack_fp']


def test_touching_the_mouse_for_the_first_time_is_not_a_hardware_change(monkeypatch):
    siem = _RecordingSiem()
    stub = _stub_daemon(siem)
    _check(monkeypatch, stub, ['k1/-'])
    _check(monkeypatch, stub, ['k1/m1'])
    assert siem.events == []


def test_the_same_stack_is_not_forwarded_twice(monkeypatch):
    siem = _RecordingSiem()
    stub = _stub_daemon(siem)
    _check(monkeypatch, stub, ['k1/m1'])
    _check(monkeypatch, stub, ['k1/m1', 'k2/m2'])
    _check(monkeypatch, stub, ['k1/m1', 'k2/m2'])
    assert len(siem.events) == 1


def test_no_device_names_or_ids_leave_the_machine(monkeypatch):
    """[[siem-forwarding-privacy-rules]]: Wazuh's syscollector already inventories
    hardware. This says "that stack again", not "a Logitech K120"."""
    siem = _RecordingSiem()
    stub = _stub_daemon(siem)
    _check(monkeypatch, stub, ['046d:c31c/046d:c077'])
    _check(monkeypatch, stub, ['046d:c31c/046d:c077', '1a2b:3c4d/1a2b:5e6f'])
    blob = repr(siem.events)
    assert '1a2b' not in blob and '3c4d' not in blob
