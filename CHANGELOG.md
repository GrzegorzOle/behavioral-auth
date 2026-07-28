# Changelog

## 0.5.1 — pattern age and drift in the report

- **`behavioral-report` now shows how old the pattern is and whether scoring has
  drifted since** — the promotion date, and the median deviation week by week.
  Behaviour moves over weeks; a pattern is a photograph of how you typed during
  enrolment, not a law. Previously the only way to notice drift was to be
  alarmed by it.
- The report **refuses to interpret the trend**. A rising median is you drifting
  or somebody else at the keyboard, and with no impostor data this system cannot
  tell those apart — it prints the number, says exactly that, and points at
  `learn-more` rather than `reset` for the case where you are sure it is you. A
  week with fewer than 20 scores is not treated as a data point at all.

## 0.5.0 — the pattern is bound to the hardware it was learned on

### The hardware stack

A laptop used with a dock and without it is two different motor contexts: the
built-in keyboard and trackpad against an external keyboard and mouse. Until now
the daemon pooled them.

- **A pattern trained across a mixture of hardware is more permissive**, not
  merely noisier. The mixture has a wider spread, so the calibrated threshold
  lands higher, so more behaviour passes it. Docking halfway through enrolment
  widened the gate an impostor had to clear. This was the actual defect; the
  false alarms after a dock change were the visible symptom.
- **Every feature window now records which `(keyboard, mouse)` pair produced its
  events**, identified by evdev `vendor:product` — not the device path, which
  renumbers across boots and re-plugs. A window straddling a hardware change is
  discarded, and no sequence spans one, for the same reason a sequence may not
  span an idle gap: it is a transition, not a person.
- **New `SUSPENDED` state.** On hardware the pattern was not trained on, the
  daemon stops scoring and says so. It does *not* raise an alarm — "you
  undocked" is not something you can act on, and alarms you cannot act on are
  how a warning system teaches you to ignore it. Collection continues.
- **`behavioral-report` and `status` show which stacks a pattern covers**, and
  say plainly when there is more than one and what that costs.
- Distinct from `PAUSED`, which remains your decision, and from `MONITORING`,
  which while not scoring would have been a lie.

### Input devices

- **Hot-plug works.** The device list used to be read exactly once at startup, so
  a keyboard attached through a dock afterwards was never opened at all.
- **A device that disappears is reported.** Undocking used to kill its reader task
  silently while the daemon went on believing it was watching.
- Both are forwarded: `input_device_added`, `input_device_removed`,
  `stack_changed`. A device attached while the machine is being watched is a
  warning, not an informational note — attaching a keyboard is how input gets
  injected.

### SIEM

- **A Wazuh decoder and ruleset now ship** in `packaging/wazuh/`. The claim that
  the RFC 5424 + JSON framing existed "so a Wazuh decoder can read the fields"
  had nothing behind it: no decoder was ever written, so events reached the
  manager and matched nothing, which in Wazuh means they were dropped silently.
  Rules 100200–100217, with alarms and pattern destruction at level 12.
- The decoder deliberately does not key on the program name. journald's syslog
  parser is RFC 3164, so it never lifts our identifier into `SYSLOG_IDENTIFIER`;
  the name the manager sees falls back to the process name.
- **What the stack events carry is the device type and a truncated fingerprint
  hash — never device names or vendor/product ids.** The forwarding rule stays
  what it was: verdicts and numbers, not an inventory.

### Fixes

- **`make demo` could not run at all.** It passes `--synthetic-input`, which is
  refused in prod mode, and the only way to select dev mode was hand-editing
  `general.mode`. `behavioral-authd` gains `--mode {dev,prod}`, applied before
  the `config.<mode>.yaml` overlay is resolved — applying it after would have
  relabelled the run while leaving the prod gates in place.
- **`behavioral-auth set-profile user|impostor`** exposes a control command the
  daemon already had and nothing could reach; the README previously told you to
  call it through a `python -c` one-liner. Testing affordance only.
- **`journalctl -t behavioral-auth` never worked** and the usage guide recommended
  it. It returns nothing even when forwarding is working correctly. Use
  `journalctl -f SYSLOG_FACILITY=10`.

### Verified, and not

The SIEM spool was exercised against real sockets for the first time: it holds
while the sink is down, drains when it returns, survives a restart, and truncates
loudly when full — dropping the oldest and saying so. The `syslog` sink was run
against the real `/dev/log`, and its datagram→stream fallback against a
stream-only socket. The Wazuh decoder has **not** been run against a real Wazuh
manager, and the Windows path remains unverified on real hardware.

## 0.4.0 — Windows

Not previously written down; recorded here after the fact.

- **A Windows input backend** built on `pynput`, emitting the same numeric event
  rows as the evdev collector, with a VK→evdev keycode map. The evdev path is
  untouched.
- **An Event Log SIEM sink**, the Windows counterpart of `syslog` + agent, and a
  pywin32 service wrapping the same daemon.
- **A signed-nothing installer** built with Inno Setup, alongside the Linux
  AppImage; both are attached to the GitHub release by CI.
- Three portability defects in shared code, all found by the Windows CI before
  any hardware run: the pidfile lock imported the Unix-only `fcntl`
  unconditionally, the CLI's Polish text and `●/○` glyphs crashed on a cp1252
  console, and the build scripts were mis-parsed as CP-1252 because of an em-dash.
- **Windows remains unverified on real hardware**: the service under the SCM, the
  live input hook, an alarm reaching the Event Log, and the installer registering
  and removing the service. Everything up to "the frozen bundle imports and the
  CLI runs" is CI-proven; nothing past that is.

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
