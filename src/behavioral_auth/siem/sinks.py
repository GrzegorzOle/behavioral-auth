"""Where forwarded events actually go.

Three sinks, chosen in the config:

  * `syslog` writes to the local syslog socket. If a wazuh-agent is installed it
    picks the line up from there, which means retries, buffering and TLS to the
    manager are the agent's job and not ours. Prefer this wherever an agent
    exists — it is the sink with no network code of our own. (Linux.)

  * `eventlog` is the Windows counterpart of `syslog`: it writes to the box's
    own Windows Event Log, and a Wazuh agent's windows-eventchannel collector
    forwards it on — again no network code of ours. `/dev/log` does not exist on
    Windows, so this is the local-agent sink there.

  * `wazuh` talks straight to the manager's syslog listener, for a box with no
    agent. We own the socket, so we own its failures. (Either OS.)

All raise on failure rather than swallowing it. That is the whole point: the
spool needs to know an event did not land.

The syslog/wazuh payload is RFC 5424 with a JSON message, so a Wazuh decoder can
read the fields without parsing prose. The Event Log sink carries the same JSON
as the event's message string.
"""

from __future__ import annotations

import socket

from behavioral_auth.siem.event import Event, Severity

_RFC5424_VERSION = 1
_NIL = '-'


class SinkError(RuntimeError):
    """The event did not reach the SIEM. It stays in the spool."""


def _frame(event: Event, facility: int, ident: str) -> str:
    pri = facility * 8 + event.severity
    msgid = f'{event.category}.{event.action}'
    return (f'<{pri}>{_RFC5424_VERSION} {event.ts} {event.host} {ident} '
            f'{_NIL} {msgid} {_NIL} {event.to_json()}')


class SyslogSink:
    """The local syslog socket (/dev/log)."""

    def __init__(self, socket_path: str, facility: int, ident: str):
        self.socket_path = socket_path
        self.facility = facility
        self.ident = ident

    def send(self, event: Event) -> None:
        payload = _frame(event, self.facility, self.ident).encode('utf-8')
        # Datagram first — that is what /dev/log normally is. Journald and some
        # rsyslog builds present a stream socket instead, so fall back to it
        # rather than reporting a healthy SIEM as unreachable.
        for kind, terminator in ((socket.SOCK_DGRAM, b''), (socket.SOCK_STREAM, b'\n')):
            try:
                with socket.socket(socket.AF_UNIX, kind) as s:
                    s.settimeout(2.0)
                    s.connect(self.socket_path)
                    s.sendall(payload + terminator)
                return
            except OSError as exc:
                last = exc
        raise SinkError(f'{self.socket_path}: {last}') from last


class WazuhSink:
    """The Wazuh manager's syslog listener, over the network."""

    def __init__(self, host: str, port: int, protocol: str, facility: int,
                 ident: str, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.protocol = protocol
        self.facility = facility
        self.ident = ident
        self.timeout = timeout

    def send(self, event: Event) -> None:
        payload = _frame(event, self.facility, self.ident).encode('utf-8')
        kind = socket.SOCK_DGRAM if self.protocol == 'udp' else socket.SOCK_STREAM
        try:
            with socket.socket(socket.AF_INET, kind) as s:
                s.settimeout(self.timeout)
                s.connect((self.host, self.port))
                s.sendall(payload + (b'' if kind is socket.SOCK_DGRAM else b'\n'))
        except OSError as exc:
            raise SinkError(f'{self.host}:{self.port}: {exc}') from exc
        # UDP "succeeds" whether or not anyone is listening. The spool cannot
        # protect an event sent over UDP, and the config documents that.


# Windows Event Log event types (winnt.h; the values are fixed ABI constants, so
# we can name them without importing pywin32 — which does not exist off Windows).
EVENTLOG_ERROR_TYPE = 0x0001
EVENTLOG_WARNING_TYPE = 0x0002
EVENTLOG_INFORMATION_TYPE = 0x0004


def event_type_for(severity: int) -> int:
    """Map our syslog severity onto a Windows Event Log type.

    The Event Log has only Error / Warning / Information, and a behavioural alarm
    is *not* a software error — the daemon is doing exactly what it should — so
    ALERT and WARNING both land on WARNING_TYPE, and NOTICE / INFO on
    INFORMATION_TYPE. Nothing we emit is EVENTLOG_ERROR_TYPE; that is reserved
    for the daemon actually breaking, which the SIEM path does not report.
    """
    if severity <= Severity.WARNING:              # ALERT (1) or WARNING (4)
        return EVENTLOG_WARNING_TYPE
    return EVENTLOG_INFORMATION_TYPE


class EventLogSink:
    """The Windows Event Log — the box's own log, picked up by a Wazuh agent.

    pywin32 is imported inside :meth:`send`, so this module still imports on
    Linux (where pywin32 is not installed) and only touches the Win32 API when an
    event is actually delivered on Windows.
    """

    # A single event id under pywin32's generic message DLL. The real content is
    # the JSON in the event strings; the id only picks the "%1 %2 ..." template.
    _EVENT_ID = 1000

    def __init__(self, source: str, log_type: str = 'Application'):
        self.source = source
        self.log_type = log_type
        self._registered = False

    def _ensure_registered(self, win32evtlogutil) -> None:
        """Point the source at pywin32's generic message DLL, so Event Viewer
        renders our strings instead of "the description cannot be found".

        Idempotent and best-effort: the first registration writes under
        HKLM and needs admin (the installer does it), but ReportEvent still
        delivers the event even if the source is unregistered — it just shows
        the raw strings with a boilerplate preamble."""
        if self._registered:
            return
        try:
            win32evtlogutil.AddSourceToRegistry(self.source, eventLogType=self.log_type)
        except Exception:                         # already present, or no privilege
            pass
        self._registered = True

    def send(self, event: Event) -> None:
        try:
            import pywintypes
            import win32evtlogutil
        except ImportError as exc:                # not Windows, or pywin32 missing
            raise SinkError(f'Windows Event Log unavailable: {exc}') from exc
        self._ensure_registered(win32evtlogutil)
        # Two strings: a human-readable "category.action" and the JSON body a
        # Wazuh windows-eventchannel decoder reads. Same JSON the syslog sinks send.
        strings = [f'{event.category}.{event.action}', event.to_json()]
        try:
            win32evtlogutil.ReportEvent(
                self.source, self._EVENT_ID,
                eventType=event_type_for(event.severity), strings=strings)
        except pywintypes.error as exc:
            raise SinkError(f'Event Log ReportEvent failed: {exc}') from exc


def build_sink(cfg) -> SyslogSink | WazuhSink | EventLogSink:
    if cfg.sink == 'syslog':
        return SyslogSink(cfg.socket_path, cfg.facility, cfg.ident)
    if cfg.sink == 'eventlog':
        return EventLogSink(cfg.eventlog_source, cfg.eventlog_log)
    if cfg.sink == 'wazuh':
        if not cfg.host:
            raise ValueError('siem.sink is "wazuh" but siem.host is empty')
        return WazuhSink(cfg.host, cfg.port, cfg.protocol, cfg.facility, cfg.ident)
    raise ValueError(f'unknown siem.sink: {cfg.sink!r}')
