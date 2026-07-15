"""Where forwarded events actually go.

Two sinks, chosen in the config:

  * `syslog` writes to the local syslog socket. If a wazuh-agent is installed it
    picks the line up from there, which means retries, buffering and TLS to the
    manager are the agent's job and not ours. Prefer this wherever an agent
    exists — it is the sink with no network code of our own.

  * `wazuh` talks straight to the manager's syslog listener, for a box with no
    agent. We own the socket, so we own its failures.

Both raise on failure rather than swallowing it. That is the whole point: the
spool needs to know an event did not land.

The payload is RFC 5424 with a JSON message, so a Wazuh decoder can read the
fields without parsing prose.
"""

from __future__ import annotations

import socket

from behavioral_auth.siem.event import Event

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


def build_sink(cfg) -> SyslogSink | WazuhSink:
    if cfg.sink == 'syslog':
        return SyslogSink(cfg.socket_path, cfg.facility, cfg.ident)
    if cfg.sink == 'wazuh':
        if not cfg.host:
            raise ValueError('siem.sink is "wazuh" but siem.host is empty')
        return WazuhSink(cfg.host, cfg.port, cfg.protocol, cfg.facility, cfg.ident)
    raise ValueError(f'unknown siem.sink: {cfg.sink!r}')
