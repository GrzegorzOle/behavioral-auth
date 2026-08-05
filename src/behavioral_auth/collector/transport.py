"""Is this session at the physical console, or delivered over the network?

RDP is not different hardware, it is a different **transport**, and that is the
whole reason it needed its own answer. The model's signal is keystroke dwell and
flight times; RDP interposes network latency and input batching between the
physical keypress and the timestamp the daemon records, so behaviour captured
remotely is the owner *plus the link*, and the link varies minute to minute. The
hardware-stack gate was built for exactly this class of problem — "you are
comparing against something the pattern never saw" — but on Windows every event
claimed one global device, so RDP walked straight past it.

**Do not use `%SESSIONNAME%` or `%CLIENTNAME%` for this.** Those are snapshots
taken when a process is *created* and they survive a session being reconnected
between console and RDP. Measured on this project's Windows box: a shell created
during an RDP session still reported ``SESSIONNAME=RDP-Tcp#0`` while sitting on
the console, and the three APIs below all correctly said console. The mirror case
is the dangerous one — a process created at the console and later taken over by
RDP keeps claiming ``Console``, so a guard built on the variable would *permit*
collection over RDP, which is precisely what it exists to stop.

Everything here is Windows-specific. On Linux the collector reads evdev, i.e. the
kernel's local input devices; a remote X or RDP session does not deliver input
through them at all, so there is nothing to detect and nothing to exclude.
"""

from __future__ import annotations

import sys

CONSOLE = 'console'
REMOTE = 'rdp'
#: The transport could not be determined — a different thing from "local", and
#: never treated as evidence of either.
UNKNOWN = 'unknown'

# GetSystemMetrics
_SM_REMOTESESSION = 0x1000
# WTSQuerySessionInformation info class; the value is 0 console, 1 ICA, 2 RDP.
_WTS_CLIENT_PROTOCOL_TYPE = 16
_WTS_CURRENT_SESSION = 0xFFFFFFFF


def current() -> str:
    """The transport of the session this process is running in.

    Two independent APIs are consulted and **either** saying "remote" is enough.
    They answer subtly different questions — `SM_REMOTESESSION` asks whether the
    session is remote, `WTSClientProtocolType` asks which protocol a client is
    attached with — and for a decision that only ever *withholds* judgement,
    erring toward "remote" costs a gap in coverage while erring toward "console"
    would quietly poison a pattern. The asymmetry is the point.

    Never raises: an unavailable API answers UNKNOWN, and UNKNOWN is not treated
    as remote anywhere.
    """
    if sys.platform != 'win32':
        return UNKNOWN

    import ctypes
    from ctypes import wintypes

    try:
        if ctypes.windll.user32.GetSystemMetrics(_SM_REMOTESESSION):
            return REMOTE
    except (AttributeError, OSError):        # pragma: no cover - needs a broken host
        pass

    try:
        buf = ctypes.c_void_p()
        size = wintypes.DWORD()
        ok = ctypes.windll.wtsapi32.WTSQuerySessionInformationW(
            None, _WTS_CURRENT_SESSION, _WTS_CLIENT_PROTOCOL_TYPE,
            ctypes.byref(buf), ctypes.byref(size))
        if ok:
            protocol = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ushort)).contents.value
            ctypes.windll.wtsapi32.WTSFreeMemory(buf)
            # 0 console, 1 ICA (Citrix), 2 RDP. Anything that is not the console
            # is somebody's remote display protocol.
            return CONSOLE if protocol == 0 else REMOTE
    except (AttributeError, OSError):        # pragma: no cover - needs a broken host
        pass

    return UNKNOWN
