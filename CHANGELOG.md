# Changelog

## 0.3.0 — self-bootstrapping daemon

The project stops being a set of scripts you run in order and becomes one daemon
with an explicit state machine: **LEARNING → MONITORING → ALARM**.

### Behaviour

- **`behavioral-authd`** — on a machine with no database, no model and no face
  pattern, it creates everything and starts learning. No schema step, no manual
  enrolment, no pipeline to drive by hand.
- **Learning is cyclic.** It retrains as data accumulates and checks each time
  whether the pattern has stopped moving. Promotion requires a run of stable
  cycles, enough data, and a sanity gate (below).
- **The pattern is frozen after promotion.** Nothing retrains on its own — a
  stranger cannot teach the system to accept them by using the computer. Only
  `behavioral-auth reset` (a different person will use this machine) or
  `behavioral-auth learn-more` changes it.
- **Alarms warn; they never lock.** `actions.lock_cmd`, `fusion.lock_threshold`
  and `general.mode: enforce` are **gone from the code**, not merely disabled.
  Nothing in the tree invokes `loginctl`.
- **An alarm needs a sustained anomaly**, measured as both a count of anomalous
  scores and the wall-clock span they cover. Adjacent sequences share most of
  their windows, so a burst of scores is not by itself evidence.
- **An idle machine holds its state.** Scores only arrive when someone is typing,
  so walking away no longer quietly clears an alarm.
- **Face enrolment happens silently in the background** during learning, with
  quality gates (one face in frame, sharp, sensibly lit) and a poisoning guard so
  a colleague walking past the camera cannot be enrolled alongside you.

### Honesty about what is measured

- **`behavioral-report` no longer prints FAR/FRR/EER.** They were computed from
  the user's own scores. With no impostor data those numbers have no referent;
  they looked like security metrics and were not.
- **New sanity gate.** Each cycle builds synthetic impostors by distorting the
  user's own held-out data, and a model that cannot flag any of them is refused
  promotion. This caught a real defect: the autoencoder was being handed the very
  vector it was asked to predict, so it learned the identity map — converging
  beautifully with a low, stable error for *any* human, and detecting nobody.
- The promotion message now states plainly what was *not* measured, and which
  kinds of difference the trained model turned out to be blind to.

### Fixes

- **Feature windows were duplicated on every run.** `build_feature_windows()`
  rebuilt the whole session from its first event, with no watermark and no unique
  key. A daemon ticking every few seconds would have multiplied the database
  without bound. Now incremental, with a unique index; the migration rebuilds the
  table to remove existing duplicates.
- **Sequences could span idle gaps.** Low-activity windows are dropped, so stored
  windows are not time-contiguous, and a sequence could splice Monday morning onto
  Tuesday evening. Rejected now via `features.max_seq_gap_sec`.
- **The scaler could manufacture intruders.** `std + 1e-6` meant a feature that
  never varied during enrolment (`is_weekend`, if you enrol on weekdays)
  multiplied its first real deviation by ~10⁶ and sent the reconstruction error to
  the moon. Fixed with `features.std_floor`; the wall-clock context features are
  also no longer fed to the model at all, since they say nothing about *who* is
  typing and only fire when you work at an unusual hour.
- **The threshold was estimated from a tail percentile** of a few hundred samples
  and lurched between cycles. Replaced with a robust log-space median+MAD
  estimator.
- **Training loaded every session**, including test and impostor ones. Now scoped
  to the active enrollment.
- **An unavailable camera pushed the system toward an alarm** — a neutral 0.5 was
  fused in at weight 0.3. The channels are now independent: an unknown face is no
  evidence at all.
- `ort.InferenceSession` was rebuilt on every score (50–200 ms a tick); now cached.
- The runtime refuses to score a model whose `seq_len` disagrees with the config,
  instead of silently producing nonsense.
- Migrations are actually applied and version-tracked; `bootstrap_db.py` used to
  ignore `db/migrations/` entirely.
- Clean shutdown: the writer flushes and the session is closed, replacing a bare
  `os._exit(0)`.

### Architecture

- One process, one asyncio loop, one DuckDB instance. Collection, feature
  extraction, training and scoring coexist; `behavioral-verify` used to **kill the
  collector before every scoring cycle** just to release the write lock.
- The autoencoder reconstructs the whole sequence through a narrow bottleneck.
- Control channel: the daemon holds the DB lock, so the CLI reaches it through a
  spool directory, degrading to direct database access when no daemon runs.
- A synthetic input source on an accelerated clock makes the whole
  LEARNING → MONITORING → ALARM path testable in minutes (refused in prod).

### Removed

`behavioral-verify`, `behavioral-infer`, `behavioral-collector`,
`behavioral-features`, `behavioral-train`, `behavioral-status`,
`inference/decision.py`, the `enforce` mode, the systemd collector/feature/timer
units, and the `scikit-learn` and `polars` dependencies.

Windows support is also gone. It was only ever partial, and with enrolment now
happening inside the daemon (which needs `evdev`) a Windows install would have had
no pattern to verify against — it would have looked supported without being usable.

## 0.2.0
- Structured runnable package.
