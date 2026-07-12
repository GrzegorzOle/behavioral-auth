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
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np
from loguru import logger

from behavioral_auth.collector.device_detector import detect_devices
from behavioral_auth.collector.source import SyntheticSource, run_evdev
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
        self.monitor = MonitorController(cfg)

        self.train_pool = ThreadPoolExecutor(1, thread_name_prefix='train')
        self.face_pool = ThreadPoolExecutor(1, thread_name_prefix='face')

        self.session_id = str(uuid.uuid4())
        self.pattern: runtime.Pattern | None = None
        self.tasks: list[asyncio.Task] = []
        self.source: SyntheticSource | None = None

        self._cycle_task: asyncio.Task | None = None
        self._face_task: asyncio.Task | None = None
        self._last_scored_ns = -1
        self._last_face_check = 0.0
        self._last_face_sample = 0.0
        self._last_heartbeat = 0.0
        self._events_at_last_tick = 0
        self._face_since_train = 0
        self._stopping = False

    # ── lifecycle ─────────────────────────────────────────────────────────

    async def run(self) -> None:
        pid = control.PidFile(self.cfg.daemon.run_dir)
        if not pid.acquire():
            logger.error('Another behavioral-authd already holds the lock — refusing to start')
            return

        try:
            self.conn = open_db(self.cfg)          # creates + migrates on first run
            self.store = StateStore(self.conn, self.cfg.daemon.run_dir)
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

        if status == 'active' and pattern and pattern.enrollment_id == enrollment:
            # Resume straight into monitoring. Do NOT retrain: the pattern is frozen.
            self.pattern = pattern
            self._refresh_face_ready(enrollment)
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

        devices = detect_devices(self.cfg.collector.devices)
        if not devices:
            raise SystemExit(
                'No keyboard or mouse devices found. Is this user in the "input" group? '
                'Try: sudo usermod -aG input "$USER" && newgrp input')
        for path in devices:
            self.tasks.append(asyncio.create_task(
                run_evdev(path, self.writer, self.session_id)))

    def _install_signals(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._request_stop)

    def _request_stop(self) -> None:
        logger.info('Shutting down…')
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

        eid = self.store.enrollment_id
        build_feature_windows(self.conn, self.session_id, eid, self.cfg)
        build_sequences(self.conn, self.session_id, eid, self.cfg)

        if self.store.state is State.LEARNING:
            await self._tick_learning(eid)
        elif self.store.state in (State.MONITORING, State.ALARM):
            await self._tick_monitoring(eid)

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
            'SELECT seq_end_ns, data_json FROM fused_sequences '
            'WHERE session_id = ? AND seq_end_ns > ? ORDER BY seq_end_ns',
            [self.session_id, self._last_scored_ns],
        ).fetchall()

        if rows:
            X = np.array([json.loads(r[1]) for r in rows], dtype=np.float32)
            ratios = self.pattern.ratios(X)
            errors = self.pattern.errors(X)
            for (seq_end_ns, _), ratio, err in zip(rows, ratios, errors):
                verdict = classify(float(ratio), self.cfg.alarm.clear_hysteresis)
                self.monitor.observe(verdict, float(ratio), int(seq_end_ns))
                self._record_score(eid, int(seq_end_ns), float(err), float(ratio), verdict)
            self._last_scored_ns = int(rows[-1][0])
        # No new sequence => nobody is typing => the counters hold. Walking away
        # must not clear an alarm.

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
            msg = commands.learn_more(self.conn, self.cfg)
            self.learn.stable_streak = 0
            self.learn.last_cycle_at = 0.0
            self.learn.seq_at_last_cycle = dataset.count_sequences(
                self.conn, self.store.enrollment_id)
            self.store.transition(State.LEARNING, 'learn-more requested by user')
            return True, msg

        if cmd == 'pause':
            self.store.transition(State.PAUSED, 'paused by user')
            return True, 'Wstrzymano. Zbieranie danych trwa, punktacja nie.'

        if cmd == 'resume':
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
