"""behavioral-auth as a Windows service (pywin32).

The Linux build autostarts via a user systemd unit; this is the Windows
counterpart. It wraps the *same* :class:`~behavioral_auth.daemon.daemon.Daemon`
the CLI runs — no second copy of the lifecycle — and lets the Service Control
Manager start and stop it.

Shape:

  * The daemon is an asyncio program, so it runs on its own thread
    (``asyncio.run`` in ``_run``). The SCM's SvcStop callback fires on a
    different thread and asks the daemon to shut down with
    :meth:`Daemon.request_stop`, which hops back onto the loop thread. Nothing
    else crosses the thread boundary.
  * ``--synthetic-input`` and the live status console make no sense for a
    service, so they are not wired up here: the service always reads real input
    and logs to a file (``general.log_file`` in the config).

This file lives under packaging/, not in the importable package, because it is
Windows-only glue that imports pywin32 (absent on the Linux dev box) and is only
ever run frozen on Windows. It is deliberately outside ``ruff check src tests``.

Manage it from an **elevated** shell (the frozen exe forwards to here — see
Planned work, Stage 2 step 4):

    behavioral-auth-service install     # register with the SCM (auto-start)
    behavioral-auth-service start
    behavioral-auth-service stop
    behavioral-auth-service remove

When running from source rather than frozen:

    python packaging/windows/service.py install
"""

from __future__ import annotations

import threading

import servicemanager
import win32event
import win32service
import win32serviceutil


def _build_daemon():
    """Construct the daemon exactly as behavioral-authd's main() does, minus the
    console/synthetic options a service never uses."""
    import asyncio

    from behavioral_auth.config import load_settings
    from behavioral_auth.daemon.daemon import Daemon
    from behavioral_auth.daemon.main import setup_logging

    cfg = load_settings()
    cfg.daemon.console = 'never'          # no live status block under the SCM
    daemon = Daemon(cfg)
    setup_logging(cfg, daemon.console)
    return asyncio, daemon


class BehavioralAuthService(win32serviceutil.ServiceFramework):
    _svc_name_ = 'behavioral-auth'
    _svc_display_name_ = 'behavioral-auth (behavioural authentication)'
    _svc_description_ = ('Learns how you type and move, then warns when live '
                         'behaviour stops matching. Never locks the session.')

    def __init__(self, args):
        super().__init__(args)
        # Signalled by the SCM stop path so SvcDoRun can wait on it too.
        self._stop_evt = win32event.CreateEvent(None, 0, 0, None)
        self._daemon = None
        self._thread: threading.Thread | None = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        if self._daemon is not None:
            self._daemon.request_stop()          # threadsafe: hops to the loop
        win32event.SetEvent(self._stop_evt)

    def SvcDoRun(self):
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ''))
        asyncio, self._daemon = _build_daemon()

        def _run():
            try:
                asyncio.run(self._daemon.run())
            except Exception:                    # noqa: BLE001 - log then let the thread die
                servicemanager.LogErrorMsg('behavioral-auth daemon crashed')

        self._thread = threading.Thread(target=_run, name='daemon', daemon=True)
        self._thread.start()
        # Block SvcDoRun until stop is requested; then let the daemon drain.
        win32event.WaitForSingleObject(self._stop_evt, win32event.INFINITE)
        self._thread.join(timeout=30)


def main() -> None:
    win32serviceutil.HandleCommandLine(BehavioralAuthService)


if __name__ == '__main__':
    main()
