"""The daemon: one process, one event loop, one DuckDB instance.

Everything that touches the database runs on the loop thread. The two things
that genuinely block — fitting the model, and talking to the camera — run in
single-worker executors and are handed plain data, never a connection. That
rule is what makes a single DuckDB instance safe here, and it is also what
lets collection continue uninterrupted while a model trains, which the old
code could not do: it killed the collector before every scoring cycle to get
the write lock back.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np
from loguru import logger

from behavioral_auth import updates
from behavioral_auth.collector.device_detector import detect_devices
from behavioral_auth.collector.source import SyntheticSource, run_evdev
from behavioral_auth.collector.stack import describe, short_fp
from behavioral_auth.collector.writer import Writer
from behavioral_auth.config import Settings
from behavioral_auth.daemon import commands, control
from behavioral_auth.daemon.console import Console
from behavioral_auth.daemon.learning import LearningController, run_cycle_blocking
from behavioral_auth.daemon.monitoring import MonitorController
from behavioral_auth.daemon.state import State, StateStore
from behavioral_auth.db import open_db
from behavioral_auth.face import calibrate, sampler
from behavioral_auth.face.recognizer import FaceRecognizer
from behavioral_auth.features.pipeline import build_feature_windows, build_sequences
from behavioral_auth.inference import runtime
from behavioral_auth.inference.fusion import FaceState, Verdict, classify, display_score
from behavioral_auth.siem import Category, Forwarder, Severity
from behavioral_auth.training import dataset


class Daemon:
    def __init__(self, cfg: Settings, synthetic: str | None = None,
                 synthetic_speed: float = 20.0):
        self.cfg = cfg
        self.synthetic = synthetic
        self.synthetic_speed = synthetic_speed

        self.conn = None
        self.store: StateStore | None = None
        self.writer: Writer | None = None
        self.console = Console(cfg.daemon.console)
        self.learn = LearningController(cfg)

        self.session_id = str(uuid.uuid4())
        self.siem = Forwarder(cfg, session_id=self.session_id)
        self.monitor = MonitorController(cfg, siem=self.siem)

        self.train_pool = ThreadPoolExecutor(1, thread_name_prefix='train')
        self.face_pool = ThreadPoolExecutor(1, thread_name_prefix='face')
        self.pattern: runtime.Pattern | None = None
        self.tasks: list[asyncio.Task] = []
        self.source: SyntheticSource | None = None

        self._cycle_task: asyncio.Task | None = None
        self._face_task: asyncio.Task | None = None
        self._update_task: asyncio.Task | None = None
        # Seeded from disk so a daemon that restarts often does not ask on every
        # start. Reading it here is safe before the run dir exists — read_status
        # answers None rather than raising.
        self._update_status = updates.read_status(cfg.daemon.run_dir)
        self._last_scored_ns = -1
        self._last_face_check = 0.0
        self._last_face_sample = 0.0
        self._last_heartbeat = 0.0
        self._events_at_last_tick = 0
        self._face_since_train = 0
        self._stopping = False
        self._stack_suspended_on: str | None = None
        # Windows only; see _start_sources. None means "this platform cannot
        # tell", which is not the same as "nothing was injected".
        self.injection = None
        self._injection_warned: set[str] = set()
        # Device path -> reader task, so a re-scan can tell what is already open
        # and notice when a reader has died with its device.
        self._readers: dict[str, asyncio.Task] = {}

    # ── lifecycle ─────────────────────────────────────────────────────────

    async def run(self) -> None:
        pid = control.PidFile(self.cfg.daemon.run_dir)
        if not pid.acquire():
            logger.error('Another behavioral-authd already holds the lock — refusing to start')
            return

        try:
            self.conn = open_db(self.cfg)          # creates + migrates on first run
            self.store = StateStore(self.conn, self.cfg.daemon.run_dir, siem=self.siem)
            self.siem.emit(Category.OPS, 'daemon_started', mode=self.cfg.general.mode)
            self.writer = Writer(self.conn, self.cfg.collector.batch_size)
            self._bootstrap()
            self._start_sources()
            self._install_signals()
            await self._supervise()
        finally:
            await self._shutdown()
            pid.release()

    def _bootstrap(self) -> None:
        """Decide where we are starting from: a blank machine, or a frozen pattern."""
        if self.cfg.general.mode != 'prod':
            logger.warning(
                f'Running in {self.cfg.general.mode!r} mode: the promotion gates are '
                f'lowered ({self.cfg.learning.min_sequences} sequences, '
                f'{self.cfg.learning.min_active_minutes} active minutes). A pattern '
                f'promoted under these gates is a smoke test, not something to rely on. '
                f'Set general.mode: prod for real use.')

        enrollment = self.store.active_enrollment() or self.store.create_enrollment()
        self.store.enrollment_id = enrollment
        self.store.snapshot.enrollment_id = enrollment
        self.store.snapshot.session_id = self.session_id

        self.conn.execute(
            'INSERT INTO sessions (session_id, user_name, host_name, mode, role, metadata) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            [self.session_id, os.getenv('USER', 'unknown'), socket.gethostname(),
             self.cfg.general.mode, 'synthetic' if self.synthetic else 'user', '{}'])

        status = self.conn.execute(
            'SELECT status FROM enrollments WHERE enrollment_id = ?', [enrollment]
        ).fetchone()[0]

        try:
            pattern = runtime.load_pattern(self.cfg)
        except runtime.PatternMismatch as exc:
            logger.error(f'Stored pattern is unusable: {exc}')
            pattern = None

        # Both paths need this, and LEARNING needs it most. face_ready starts False
        # and was previously only recomputed here when resuming a frozen pattern, or
        # after this process had itself finished a calibration — so a daemon that
        # restarted mid-enrolment reported "face pattern not ready" while a perfectly
        # good model and its metadata sat on disk, and held promotion back until the
        # next calibration happened to run. face.required_for_promotion is true by
        # default on both platforms, so that gate is load-bearing.
        self._refresh_face_ready(enrollment)
        self.learn.resume(self.conn, enrollment)

        if status == 'active' and pattern and pattern.enrollment_id == enrollment:
            # Resume straight into monitoring. Do NOT retrain: the pattern is frozen.
            self.pattern = pattern
            self.store.transition(State.MONITORING, 'resumed frozen pattern from disk')
        else:
            self.store.transition(State.LEARNING, 'no usable pattern yet')

    def _start_sources(self) -> None:
        if self.synthetic:
            if self.cfg.general.mode == 'prod':
                raise SystemExit('refusing to inject synthetic input in prod mode')
            self.source = SyntheticSource(
                self.writer, self.session_id, self.synthetic, self.synthetic_speed)
            self.tasks.append(asyncio.create_task(self.source.run()))
            return

        if sys.platform == 'win32':
            # Windows has no evdev; a global pynput hook produces the same event
            # rows (collector/windows_source.py). Imported here so pynput is only
            # touched on Windows and Linux imports stay evdev-only.
            from behavioral_auth.collector.windows_source import (
                InjectionStats,
                run_windows_hook,
            )
            # Only Windows can answer "was this typed or synthesised?" — the
            # low-level hooks carry the flag and evdev has no equivalent. Left
            # None elsewhere rather than faked.
            self.injection = InjectionStats()
            self.tasks.append(asyncio.create_task(
                run_windows_hook(self.writer, self.session_id, self.injection)))
            return

        devices = detect_devices(self.cfg.collector.devices)
        if not devices:
            raise SystemExit(
                'No keyboard or mouse devices found. Is this user in the "input" group? '
                'Try: sudo usermod -aG input "$USER" && newgrp input')
        for path in devices:
            self._open_reader(path)

    def _open_reader(self, path: str) -> None:
        task = asyncio.create_task(run_evdev(path, self.writer, self.session_id))
        self._readers[path] = task
        self.tasks.append(task)

    def _rescan_devices(self) -> None:
        """Follow devices as they come and go.

        Without this the device list was read exactly once at start: a keyboard
        attached through a dock later was never opened, and one that vanished
        took its reader task down with it — silently, because nothing ever
        inspected those tasks, so the daemon went on believing it was watching.

        Polling the device list on the tick rather than subscribing to udev: a
        few milliseconds every tick_sec against a new dependency and a netlink
        socket, for a difference of at most one tick in noticing.
        """
        if self.synthetic or sys.platform == 'win32':
            return

        for path, task in list(self._readers.items()):
            if not task.done():
                continue
            del self._readers[path]
            exc = task.exception() if not task.cancelled() else None
            logger.warning(f'Input device {path} stopped being readable'
                           + (f' ({exc})' if exc else ''))
            self.siem.emit(Category.OPS, 'input_device_removed',
                           severity=Severity.WARNING, dev_path_known=True)

        try:
            found = set(detect_devices(self.cfg.collector.devices))
        except OSError as exc:
            logger.debug(f'Device re-scan failed: {exc}')
            return

        for path in sorted(found - set(self._readers)):
            # A keyboard appearing while the machine is being watched is itself
            # worth telling a SIEM about: attaching one is how input is injected.
            sev = (Severity.WARNING if self.store and self.store.state in
                   (State.MONITORING, State.ALARM) else Severity.INFO)
            logger.info(f'New input device {path} — reading it')
            self.siem.emit(Category.OPS, 'input_device_added', severity=sev)
            try:
                self._open_reader(path)
            except OSError as exc:
                logger.warning(f'Could not open {path}: {exc}')

    def _check_injection(self) -> None:
        """Say once, per channel, when much of the input claims to be synthetic.

        Once and not repeatedly: a machine with a macro tool or an accessibility
        aid on it would otherwise log the same line every five seconds, and a
        warning nobody can silence is a warning everybody learns to skip. The
        live numbers stay in `status` for as long as anyone wants to watch them.

        This never stops collection. Injected input is not by itself illegitimate
        — see InjectionStats — and it is the *enrolment* that should be judged
        with this in hand, by a person.
        """
        if self.injection is None:
            return
        for channel in self.injection.loud_channels():
            if channel in self._injection_warned:
                continue
            self._injection_warned.add(channel)
            share = (self.injection.keyboard_share if channel == 'keyboard'
                     else self.injection.mouse_share)
            logger.warning(
                f'{share:.0%} of {channel} events so far were injected by software, '
                f'not produced by a device. Something is synthesising input on this '
                f'machine — a macro tool, remote support, an anti-idle utility. '
                f'A pattern learned from it is a pattern of that software, not of '
                f'you. Nothing has been dropped and nothing is being refused.')

    # ── update notice ─────────────────────────────────────────────────────

    def _maybe_check_updates(self) -> None:
        """Ask, at most once per interval, whether a newer release exists.

        Notification only, and off unless the operator turned it on: this is the
        only request the daemon makes besides SIEM forwarding, so the guard is
        the first line rather than something buried inside the check. Nothing is
        downloaded and nothing is executed — see behavioral_auth/updates.py.
        """
        if not self.cfg.updates.check_enabled:
            return
        if self._update_task and not self._update_task.done():
            return
        if not updates.due(self._update_status, self.cfg.updates.interval_hours):
            return
        self._update_task = asyncio.create_task(self._check_updates())

    async def _check_updates(self) -> None:
        """Run the blocking request off the loop thread.

        Deliberately on the default executor rather than face_pool or
        train_pool: those have one worker each, and a camera grab queueing
        behind a network timeout would turn a convenience into a hole in the
        face channel.
        """
        loop = asyncio.get_running_loop()
        status = await loop.run_in_executor(None, updates.check, self.cfg)
        self._update_status = status
        updates.write_status(self.cfg.daemon.run_dir, status)

        if status.error:
            # A box with no route out is the normal case for this product, not
            # something to warn about every day.
            logger.debug(f'Update check failed: {status.error}')
        elif status.update_available:
            logger.info(
                f'A newer release is available: {status.latest} '
                f'(running {status.current}). Nothing was downloaded — '
                f'installing it is a manual step.')

    def _install_signals(self) -> None:
        self._loop = asyncio.get_running_loop()
        if sys.platform == 'win32':
            # The Proactor loop has no add_signal_handler; a console Ctrl+C still
            # surfaces as KeyboardInterrupt in main(), and the Windows service
            # stops us via request_stop() instead.
            return
        for sig in (signal.SIGINT, signal.SIGTERM):
            self._loop.add_signal_handler(sig, self._request_stop)

    def _request_stop(self) -> None:
        logger.info('Shutting down…')
        self._stopping = True

    def request_stop(self) -> None:
        """Ask the daemon to shut down from another thread (the Windows service's
        SvcStop runs on an SCM thread, not the loop). Safe to call before the
        loop exists — the supervisor reads the flag either way."""
        loop = getattr(self, '_loop', None)
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._request_stop)
        else:
            self._stopping = True

    async def _shutdown(self) -> None:
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.train_pool.shutdown(wait=False, cancel_futures=True)
        self.face_pool.shutdown(wait=False, cancel_futures=True)

        if self.writer:
            self.writer.flush()
        if self.conn and self.store:
            self.conn.execute(
                'UPDATE sessions SET ended_at = now() WHERE session_id = ?', [self.session_id])
            self.store.mark_stopped()
        self.siem.emit(Category.OPS, 'daemon_stopped')
        self.siem.close()                          # one last drain, then report a backlog
        if self.conn:
            self.conn.close()
        self.console.close()
        logger.info('Stopped cleanly')

    # ── the supervisor tick ───────────────────────────────────────────────

    async def _supervise(self) -> None:
        while not self._stopping:
            try:
                await self._tick()
            except Exception as exc:
                logger.exception(f'Tick failed: {exc}')
                self.store.snapshot.last_error = str(exc)
            await asyncio.sleep(self.cfg.daemon.tick_sec)

    async def _tick(self) -> None:
        self.writer.flush()
        self._handle_control()
        self._rescan_devices()
        self._check_injection()
        self._maybe_check_updates()

        eid = self.store.enrollment_id
        build_feature_windows(self.conn, self.session_id, eid, self.cfg)
        build_sequences(self.conn, self.session_id, eid, self.cfg)

        if self.store.state is State.LEARNING:
            await self._tick_learning(eid)
        elif self.store.state in (State.MONITORING, State.ALARM, State.SUSPENDED):
            await self._tick_monitoring(eid)

        self.siem.flush()
        self._update_snapshot(eid)
        self.store.persist()
        self.console.render(self.store.snapshot)

    def _has_activity(self) -> bool:
        seen = self.writer.total
        active = seen > self._events_at_last_tick
        self._events_at_last_tick = seen
        return active

    # ── LEARNING ──────────────────────────────────────────────────────────

    async def _tick_learning(self, eid: str) -> None:
        active = self._has_activity()

        if self.cfg.face.enabled and self.cfg.face.backend == 'opencv':
            self._maybe_sample_face(eid, active)

        if self._cycle_task and not self._cycle_task.done():
            return   # a model is still training; let it finish

        n_seq = dataset.count_sequences(self.conn, eid)
        if not self.learn.should_run_cycle(n_seq):
            self.learn.gates(self.conn, eid, n_seq)
            return

        X = dataset.load_sequences(self.conn, eid)
        self.learn.seq_at_last_cycle = n_seq
        self._cycle_task = asyncio.create_task(self._run_cycle(eid, X, n_seq))

    async def _run_cycle(self, eid: str, X: np.ndarray, n_seq: int) -> None:
        loop = asyncio.get_running_loop()
        outcome = await loop.run_in_executor(
            self.train_pool, run_cycle_blocking, X, self.cfg, self.learn.prev_shape)
        if outcome is None:
            logger.debug('Not enough sequences to form a train/holdout split yet')
            self.learn.last_cycle_at = time.monotonic()
            return

        result, artifacts = outcome
        unmet = self.learn.gates(self.conn, eid, n_seq)
        # gates() reads stable_streak, which record() is about to advance —
        # so re-check the streak against the result we just got.
        streak = self.learn.stable_streak + 1 if result.stable else 0
        needed = self.cfg.learning.stable_consecutive_cycles
        blocking = [u for u in unmet if not u.startswith('stable cycles')]
        promote = result.stable and streak >= needed and not blocking

        self.learn.record(self.conn, eid, result, promoted=promote)
        if promote:
            self.learn.promote(self.conn, eid, artifacts)
            self.pattern = runtime.load_pattern(self.cfg)
            self.monitor.reset()
            self._last_scored_ns = self._newest_seq_ns()
            self.store.transition(State.MONITORING, 'pattern converged and passed the sanity gate')

    def _maybe_sample_face(self, eid: str, active: bool) -> None:
        if not active:
            return   # no point photographing an empty chair
        if self._face_task and not self._face_task.done():
            return
        if time.monotonic() - self._last_face_sample < self.cfg.face.sample_interval_sec:
            return
        self._last_face_sample = time.monotonic()
        self._face_task = asyncio.create_task(self._sample_face(eid))

    async def _sample_face(self, eid: str) -> None:
        loop = asyncio.get_running_loop()
        recognizer = (FaceRecognizer(self.cfg.face.model_path)
                      if os.path.exists(self.cfg.face.model_path) else None)
        samples = await loop.run_in_executor(
            self.face_pool, sampler.capture, self.cfg, recognizer)

        for s in samples:
            path = sampler.save_sample(s, self.cfg, eid)
            self.conn.execute(
                'INSERT INTO face_samples (enrollment_id, path, width, sharpness, '
                'brightness, self_confidence) VALUES (?, ?, ?, ?, ?, ?)',
                [eid, path, s.width, s.sharpness, s.brightness, s.self_confidence])
        self._face_since_train += len(samples)

        if self._face_since_train >= self.cfg.face.retrain_every_n_samples:
            self._face_since_train = 0
            crops = sampler.load_samples(self.cfg, eid)
            meta = await loop.run_in_executor(
                self.face_pool, calibrate.train_and_calibrate, crops, self.cfg)
            if meta:
                self.conn.execute(
                    'INSERT INTO face_models (model_path, n_samples, backend, notes) '
                    'VALUES (?, ?, ?, ?)',
                    [self.cfg.face.model_path, meta['n_samples'], 'opencv',
                     f'threshold={meta["threshold"]:.1f}'])
                self._refresh_face_ready(eid)

    def _refresh_face_ready(self, eid: str) -> None:
        if not self.cfg.face.enabled:
            self.learn.face_ready = True
            return
        n = self._face_sample_count(eid)
        meta = calibrate.load_face_meta(self.cfg)
        self.learn.face_ready = (
            n >= self.cfg.face.min_samples
            and meta is not None
            and os.path.exists(self.cfg.face.model_path))

    def _face_sample_count(self, eid: str) -> int:
        return self.conn.execute(
            'SELECT count(*) FROM face_samples WHERE enrollment_id = ?', [eid]
        ).fetchone()[0] or 0

    # ── MONITORING / ALARM ────────────────────────────────────────────────

    async def _tick_monitoring(self, eid: str) -> None:
        if not self.pattern:
            self.store.transition(State.LEARNING, 'pattern disappeared from disk')
            return

        self._maybe_check_face()

        # Scoped to the CURRENT session, not the whole enrollment. Monitoring
        # watches what is happening at the keyboard now; replaying the sequences
        # the pattern was *trained* on would feed history into the alarm state
        # machine and pin the counters to stale scores forever.
        rows = self.conn.execute(
            'SELECT seq_end_ns, data_json, stack_fp FROM fused_sequences '
            'WHERE session_id = ? AND seq_end_ns > ? ORDER BY seq_end_ns',
            [self.session_id, self._last_scored_ns],
        ).fetchall()

        # A sequence from hardware the pattern never saw cannot be scored against
        # it: the comparison is meaningless, not suspicious. Those sequences are
        # dropped, and the watermark still advances so they are not retried
        # forever. See collector/stack.py.
        scorable = [r for r in rows if self.pattern.accepts_stack(r[2])]
        if rows and not scorable:
            self._suspend_for_stack(rows[-1][2])
            self._last_scored_ns = int(rows[-1][0])
            return
        if scorable and self.store.state is State.SUSPENDED:
            self._resume_from_stack(scorable[-1][2])

        if scorable:
            X = np.array([json.loads(r[1]) for r in scorable], dtype=np.float32)
            ratios = self.pattern.ratios(X)
            errors = self.pattern.errors(X)
            for (seq_end_ns, _, _), ratio, err in zip(scorable, ratios, errors):
                verdict = classify(float(ratio), self.cfg.alarm.clear_hysteresis)
                self.monitor.observe(verdict, float(ratio), int(seq_end_ns))
                self._record_score(eid, int(seq_end_ns), float(err), float(ratio), verdict)
        if rows:
            self._last_scored_ns = int(rows[-1][0])
        # No new sequence => nobody is typing => the counters hold. Walking away
        # must not clear an alarm.

        if self.store.state is State.SUSPENDED:
            return          # nothing was scored; the alarm machine must not move

        reason = self.monitor.should_raise()
        if reason:
            self.monitor.raise_alarm(self.conn, eid, self.session_id, reason)
            self.store.transition(State.ALARM, f'sustained anomaly ({reason})')
            self._last_heartbeat = time.monotonic()
        elif self.monitor.should_clear():
            self.monitor.clear_alarm(self.conn)
            self.store.transition(State.MONITORING, 'behaviour matches the pattern again')
        elif (self.monitor.alarm
              and time.monotonic() - self._last_heartbeat >= self.cfg.alarm.heartbeat_sec):
            self._last_heartbeat = time.monotonic()
            self.monitor.heartbeat()

    def _suspend_for_stack(self, key: str | None) -> None:
        """Stop scoring because the hardware is not what the pattern was learned on.

        Deliberately not an alarm. "You undocked" is not something the user can
        act on, and raising one would train them to ignore the real thing. The
        daemon keeps collecting — the data is still worth having for a later
        `learn-more` — it simply refuses to judge it.
        """
        if self.store.state is State.SUSPENDED:
            return
        if self.monitor.alarm:
            # An alarm raised on the enrolled stack is not resolved by the user
            # changing hardware; close it honestly rather than letting it linger
            # in a state that no longer scores.
            self.monitor.clear_alarm(self.conn)
        self.monitor.reset()
        self._stack_suspended_on = key
        self.siem.emit(Category.OPS, 'stack_changed', severity=Severity.WARNING,
                       stack_fp=short_fp(key or ''), known=False)
        logger.warning(
            f'Scoring suspended: this is not the hardware the pattern was learned on '
            f'({describe(key or "")}). Nothing is being judged until the enrolled '
            f'hardware returns. To make this stack part of the pattern, run '
            f'"behavioral-auth learn-more" — but note that a pattern spanning two '
            f'stacks is more permissive than one.'
        )
        self.store.transition(State.SUSPENDED, 'unenrolled hardware stack')

    def _resume_from_stack(self, key: str | None) -> None:
        self._stack_suspended_on = None
        self.siem.emit(Category.OPS, 'stack_changed', stack_fp=short_fp(key or ''),
                       known=True)
        logger.info(f'Enrolled hardware is back ({describe(key or "")}) — scoring resumes.')
        self.store.transition(State.MONITORING, 'enrolled hardware stack returned')

    def _record_score(self, eid: str, seq_end_ns: int, err: float,
                      ratio: float, verdict: Verdict) -> None:
        face = self.monitor.face_state
        self.conn.execute(
            'INSERT INTO scores (enrollment_id, session_id, seq_end_ns, error, ratio, '
            'beh_anomalous, face_state, fused, verdict, state) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            [eid, self.session_id, seq_end_ns, err, ratio, ratio > 1.0, face.value,
             display_score(ratio, face), verdict.value, self.store.state.value])

    def _maybe_check_face(self) -> None:
        if not self.cfg.face.enabled:
            return
        if self._face_task and not self._face_task.done():
            return
        if time.monotonic() - self._last_face_check < self.cfg.face.check_interval_sec:
            return
        self._last_face_check = time.monotonic()
        self._face_task = asyncio.create_task(self._check_face())

    async def _check_face(self) -> None:
        from behavioral_auth.face import verify

        loop = asyncio.get_running_loop()
        state, confidence = await loop.run_in_executor(self.face_pool, verify.check, self.cfg)
        self.monitor.observe_face(state)
        self.conn.execute(
            'INSERT INTO face_checks (session_id, backend, label, confidence, score, recognised) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            [self.session_id, self.cfg.face.backend, 0, confidence,
             0.05 if state is FaceState.MATCH else 0.85, state is FaceState.MATCH])

    def _newest_seq_ns(self) -> int:
        """Newest sequence of the current session, so promotion does not score
        the very data it just trained on."""
        row = self.conn.execute(
            'SELECT max(seq_end_ns) FROM fused_sequences WHERE session_id = ?',
            [self.session_id],
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else -1

    # ── control commands ──────────────────────────────────────────────────

    def _handle_control(self) -> None:
        for req in control.poll(self.cfg.daemon.run_dir):
            try:
                ok, msg = self._apply(req)
            except Exception as exc:
                logger.exception(f'Command {req.cmd} failed')
                ok, msg = False, str(exc)
            control.reply(self.cfg.daemon.run_dir, req, ok, msg)

    def _apply(self, req: control.Request) -> tuple[bool, str]:
        cmd = req.cmd
        if cmd == 'reset':
            # The pattern is how the daemon knows who the owner is. Discarding it
            # is the one operation that makes the system forget — a SIEM wants to
            # hear about it whether or not it was the owner who asked.
            self.siem.emit(Category.OPS, 'pattern_reset', severity=Severity.WARNING,
                           purge_data=bool(req.args.get('purge_data', False)),
                           previous_enrollment=self.store.enrollment_id)
            msg = commands.reset(self.conn, self.cfg, req.args.get('purge_data', False))
            self.pattern = None
            self.learn.reset()
            self.monitor.reset()
            self._face_since_train = 0
            self._last_scored_ns = -1
            self.store.enrollment_id = commands.active_enrollment(self.conn) or ''
            self.store.snapshot.enrollment_id = self.store.enrollment_id
            self.store.transition(State.LEARNING, 'reset by user')
            return True, msg

        if cmd == 'learn-more':
            self.siem.emit(Category.OPS, 'learn_more', severity=Severity.WARNING)
            msg = commands.learn_more(self.conn, self.cfg)
            self.learn.stable_streak = 0
            self.learn.last_cycle_at = 0.0
            self.learn.seq_at_last_cycle = dataset.count_sequences(
                self.conn, self.store.enrollment_id)
            self.store.transition(State.LEARNING, 'learn-more requested by user')
            return True, msg

        if cmd == 'pause':
            # Pausing stops scoring, so it is a window in which nothing is watched.
            self.siem.emit(Category.OPS, 'paused', severity=Severity.WARNING)
            self.store.transition(State.PAUSED, 'paused by user')
            return True, 'Wstrzymano. Zbieranie danych trwa, punktacja nie.'

        if cmd == 'resume':
            self.siem.emit(Category.OPS, 'resumed')
            target = State.MONITORING if self.pattern else State.LEARNING
            self.store.transition(target, 'resumed by user')
            return True, f'Wznowiono w stanie {target.value}.'

        if cmd == 'set-profile' and self.source:   # synthetic testing only
            self.source.set_profile(req.args['profile'])
            return True, f'Profil syntetyczny: {req.args["profile"]}'

        return False, f'nieznana komenda: {cmd}'

    # ── snapshot ──────────────────────────────────────────────────────────

    def _update_snapshot(self, eid: str) -> None:
        s = self.store.snapshot
        lc = self.cfg.learning
        s.state = self.store.state.value
        s.enrollment_id = eid
        s.session_id = self.session_id
        # Stays None on platforms that cannot answer the question, so a reader
        # can tell "no injection seen" apart from "never looked".
        s.injection = self.injection.as_dict() if self.injection else None

        if self.store.state is State.LEARNING:
            s.n_sequences = dataset.count_sequences(self.conn, eid)
            s.min_sequences = lc.min_sequences
            s.active_minutes = dataset.active_minutes(self.conn, eid, self.cfg.features.stride_sec)
            s.min_active_minutes = lc.min_active_minutes
            s.distinct_hours = dataset.distinct_hours(self.conn, eid)
            s.min_distinct_hours = lc.min_distinct_hours
            s.face_samples = self._face_sample_count(eid) if self.cfg.face.enabled else 0
            s.face_min_samples = self.cfg.face.min_samples if self.cfg.face.enabled else 0
            s.cycles_done = self.learn.cycle_no
            s.stable_streak = self.learn.stable_streak
            s.stable_needed = lc.stable_consecutive_cycles
            s.next_cycle_in_sec = self.learn.next_cycle_in()
            s.last_cycle = self.learn.last_result.as_dict() if self.learn.last_result else None
            s.blocked_by = self.learn.blocked_by
        else:
            s.last_ratio = self.monitor.last_ratio
            s.recent_ratios = list(self.monitor.recent_ratios)
            s.face_state = self.monitor.face_state.value
            s.consec_anom = self.monitor.anom.count
            s.consec_norm = self.monitor.norm.count
            if self.monitor.alarm:
                s.alarm_since = datetime.fromtimestamp(
                    self.monitor.alarm.started_at, tz=timezone.utc).isoformat()
                s.alarm_reason = self.monitor.alarm.reason
                s.alarm_peak_ratio = self.monitor.alarm.peak_ratio
            else:
                s.alarm_since = s.alarm_reason = s.alarm_peak_ratio = None
            s.pattern_stacks = self.pattern.stacks if self.pattern else []
            s.stack_suspended_on = self._stack_suspended_on
