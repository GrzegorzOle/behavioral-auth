"""SIEM forwarding.

The load-bearing behaviours: forwarding is off unless asked for, a SIEM that is
down cannot stall the daemon, and an event that did not land is kept rather than
quietly dropped.
"""

from __future__ import annotations

import json
import socket
import threading

import pytest

from behavioral_auth.siem.event import Category, Event, Severity
from behavioral_auth.siem.forwarder import Forwarder
from behavioral_auth.siem.sinks import SinkError, SyslogSink, WazuhSink
from behavioral_auth.siem.spool import Spool


@pytest.fixture
def siem_cfg(cfg, tmp_path):
    cfg.siem.enabled = True
    cfg.siem.sink = 'syslog'
    cfg.siem.socket_path = str(tmp_path / 'log.sock')
    cfg.siem.spool_path = str(tmp_path / 'spool.jsonl')
    cfg.siem.flush_interval_sec = 0
    return cfg


class _CollectingSink:
    def __init__(self, fail: bool = False):
        self.sent: list[Event] = []
        self.fail = fail

    def send(self, event: Event) -> None:
        if self.fail:
            raise SinkError('pretend the SIEM is down')
        self.sent.append(event)


# ── off by default ───────────────────────────────────────────────────────────

def test_forwarding_is_off_unless_asked_for(cfg, tmp_path):
    """The daemon's 'no network' promise holds until the user opts in, so a
    disabled forwarder must not even create a spool file."""
    cfg.siem.enabled = False
    cfg.siem.spool_path = str(tmp_path / 'spool.jsonl')

    fwd = Forwarder(cfg)
    fwd.emit(Category.ALARM, 'raised', severity=Severity.ALERT, ratio=4.5)
    fwd.flush(force=True)

    assert fwd.spool is None
    assert not (tmp_path / 'spool.jsonl').exists()
    assert fwd.store_alarms_locally()      # else the alarm would exist nowhere


# ── the spool ────────────────────────────────────────────────────────────────

def test_an_undelivered_event_is_kept_and_resent(siem_cfg):
    fwd = Forwarder(siem_cfg)
    fwd.sink = _CollectingSink(fail=True)

    fwd.emit(Category.ALARM, 'raised', ratio=4.5)
    fwd.flush(force=True)
    assert len(fwd.spool) == 1             # the SIEM was down; the event stayed

    fwd.sink = _CollectingSink(fail=False)
    fwd.flush(force=True)
    assert len(fwd.spool) == 0
    assert fwd.sink.sent[0].detail['ratio'] == 4.5


def test_a_backlog_survives_a_restart(siem_cfg):
    fwd = Forwarder(siem_cfg)
    fwd.sink = _CollectingSink(fail=True)
    fwd.emit(Category.OPS, 'pattern_reset')
    fwd.flush(force=True)

    restarted = Forwarder(siem_cfg)        # reads the spool file back
    restarted.sink = _CollectingSink()
    restarted.flush(force=True)

    assert [e.action for e in restarted.sink.sent] == ['pattern_reset']


def test_the_spool_is_bounded_and_says_so(siem_cfg):
    siem_cfg.siem.spool_max_events = 3
    fwd = Forwarder(siem_cfg)
    fwd.sink = _CollectingSink(fail=True)

    for i in range(5):
        fwd.emit(Category.STATE, 'transition', i=i)

    assert len(fwd.spool) == 3
    kept = [e.detail['i'] for e in fwd.spool.events]
    assert kept == [2, 3, 4]               # the oldest are dropped, not the newest


def test_order_is_preserved_when_the_sink_fails_midway(siem_cfg):
    """A partial drain must not reorder or lose the remainder."""
    fwd = Forwarder(siem_cfg)

    class FlakySink:
        def __init__(self):
            self.sent = []

        def send(self, event):
            if len(self.sent) == 2:
                raise SinkError('link dropped mid-drain')
            self.sent.append(event)

    fwd.sink = FlakySink()
    for i in range(4):
        fwd.emit(Category.STATE, 'transition', i=i)
    fwd.flush(force=True)

    assert [e.detail['i'] for e in fwd.sink.sent] == [0, 1]
    assert [e.detail['i'] for e in fwd.spool.events] == [2, 3]


def test_emit_never_raises_when_the_spool_cannot_be_written(siem_cfg, monkeypatch):
    """Forwarding is a side channel. It must not be able to bring down the daemon."""
    fwd = Forwarder(siem_cfg)
    monkeypatch.setattr(Spool, 'append',
                        lambda *a, **k: (_ for _ in ()).throw(OSError('disk full')))

    fwd.emit(Category.ALARM, 'raised')     # must not raise


# ── what actually goes on the wire ───────────────────────────────────────────

def test_the_payload_is_rfc5424_with_a_json_message(tmp_path):
    sock_path = tmp_path / 'log.sock'
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(str(sock_path))
    received: list[bytes] = []

    def serve():
        received.append(server.recv(65535))

    t = threading.Thread(target=serve)
    t.start()

    SyslogSink(str(sock_path), facility=10, ident='behavioral-auth').send(
        Event(category=Category.ALARM, action='raised', severity=Severity.ALERT,
              detail={'reason': 'behavioral', 'ratio': 4.54}))
    t.join(timeout=5)
    server.close()

    line = received[0].decode()
    assert line.startswith('<81>1 ')       # authpriv(10) * 8 + alert(1)
    assert ' behavioral-auth ' in line
    assert ' alarm.raised ' in line

    payload = json.loads(line[line.index('{'):])
    assert payload['detail'] == {'reason': 'behavioral', 'ratio': 4.54}
    assert payload['category'] == 'alarm'


def test_an_unreachable_sink_raises_rather_than_pretending(tmp_path):
    """The spool can only hold an event back if the sink admits it failed."""
    with pytest.raises(SinkError):
        SyslogSink(str(tmp_path / 'nothing-here.sock'), 10, 'x').send(
            Event(category=Category.OPS, action='daemon_started'))

    with pytest.raises(SinkError):
        WazuhSink('127.0.0.1', 1, 'tcp', 10, 'x', timeout=1.0).send(
            Event(category=Category.OPS, action='daemon_started'))


# ── the Windows Event Log sink (pure parts; pywin32 is not on the dev box) ────

def test_severity_maps_onto_event_log_types():
    """Alarms are warnings, not errors — the daemon working is not a failure."""
    from behavioral_auth.siem.sinks import (
        EVENTLOG_INFORMATION_TYPE,
        EVENTLOG_WARNING_TYPE,
        event_type_for,
    )

    assert event_type_for(Severity.ALERT) == EVENTLOG_WARNING_TYPE
    assert event_type_for(Severity.WARNING) == EVENTLOG_WARNING_TYPE
    assert event_type_for(Severity.NOTICE) == EVENTLOG_INFORMATION_TYPE
    assert event_type_for(Severity.INFO) == EVENTLOG_INFORMATION_TYPE


def test_build_sink_selects_the_event_log_sink(cfg):
    from behavioral_auth.siem.sinks import EventLogSink, build_sink

    cfg.siem.sink = 'eventlog'
    cfg.siem.eventlog_source = 'behavioral-auth'
    sink = build_sink(cfg.siem)
    assert isinstance(sink, EventLogSink)
    assert sink.source == 'behavioral-auth'
    assert sink.log_type == 'Application'


def test_event_log_send_admits_failure_when_pywin32_is_absent(monkeypatch):
    """Without pywin32 the sink must raise, so the spool holds the event back
    rather than pretending it was delivered. Forced here (setting the module to
    None makes `import` raise ImportError) so the assertion holds on Windows CI
    too, where pywin32 is actually installed."""
    import sys

    from behavioral_auth.siem.sinks import EventLogSink

    monkeypatch.setitem(sys.modules, 'win32evtlogutil', None)
    monkeypatch.setitem(sys.modules, 'pywintypes', None)
    with pytest.raises(SinkError):
        EventLogSink('behavioral-auth').send(
            Event(category=Category.OPS, action='daemon_started'))


def test_eventlog_is_an_accepted_sink_and_junk_is_not():
    from behavioral_auth.config import SiemCfg

    assert SiemCfg(sink='eventlog').sink == 'eventlog'
    with pytest.raises(ValueError, match='siem.sink'):
        SiemCfg(sink='nonsense')


# ── what must never leave the machine ────────────────────────────────────────

def test_a_state_transition_forwards_a_closed_list_of_fields(siem_cfg):
    """`StateStore.transition` takes a free-form `details` dict that is written to
    the local database. It must not reach the SIEM: splatting it into the event
    would mean whatever a future caller puts in there — feature vectors, sample
    counts, anything — leaves the machine without anyone revisiting the decision.
    """
    from behavioral_auth.daemon.state import State, StateStore

    fwd = Forwarder(siem_cfg)
    fwd.sink = _CollectingSink()

    store = StateStore.__new__(StateStore)          # no database needed for this
    store.conn = _NullConn()
    store.siem = fwd
    store.state = State.LEARNING
    store.enrollment_id = 'e1'
    store.snapshot = type('S', (), {'state': '', 'since': ''})()

    store.transition(State.MONITORING, 'promoted',
                     details={'raw_keystrokes': 'hunter2', 'feature_vector': [1, 2, 3]})
    fwd.flush(force=True)

    (event,) = fwd.sink.sent
    assert set(event.detail) == {'from_state', 'to_state', 'reason'}
    assert 'hunter2' not in event.to_json()
    assert 'feature_vector' not in event.to_json()


class _NullConn:
    def execute(self, *args, **kwargs):
        return self


# ── local retention ──────────────────────────────────────────────────────────

def test_alarms_stay_local_unless_forwarding_is_on(cfg, tmp_path):
    cfg.siem.enabled = False
    cfg.siem.store_alarms_locally = False   # meaningless without forwarding
    assert Forwarder(cfg).store_alarms_locally()


def test_forwarding_can_take_over_alarm_retention(siem_cfg):
    siem_cfg.siem.store_alarms_locally = False
    assert not Forwarder(siem_cfg).store_alarms_locally()
