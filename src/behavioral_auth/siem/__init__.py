"""Optional forwarding of security-relevant events to a SIEM (syslog / Wazuh).

Off by default. Nothing in here opens a socket unless `siem.enabled` is set, so
the daemon's "no network" property holds until the user chooses otherwise.
"""

from behavioral_auth.siem.event import Category, Event, Severity
from behavioral_auth.siem.forwarder import Forwarder, NullForwarder

__all__ = ['Category', 'Event', 'Forwarder', 'NullForwarder', 'Severity']
