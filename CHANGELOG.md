# Changelog

## 0.5.11 — the mouse features were wrong, and they were wrong everywhere

**This corrects a claim made in 0.5.10.** That release said the failure to promote was
"a question about the model, not about Windows". It was neither: the mouse feature
extractor was computing the wrong quantities, and on this hardware most stored windows
contained no features at all. Nothing about the autoencoder or the promotion gate needed
changing — both behaved correctly on the data they were handed.

- **The two pointer axes are separate event streams and were being zipped by position.**
  Relative motion reports each axis as its own event and omits an axis that did not move,
  so REL_X and REL_Y have different lengths. They are now regrouped into motion samples
  by timestamp before anything is computed. When one axis was missing entirely the old
  code gave up on the whole window.
- **Movement values are deltas, and were being differenced a second time.** Speed was
  therefore acceleration and the stored acceleration was jerk — off by seven orders of
  magnitude on real data (median 1.3e+12 before, 7.3e+04 after). Distance covered by a
  sample is now the delta itself.
- **Scroll events are no longer read as cursor movement.** The wheel shares the
  relative-event type, and was contributing timestamps with no axis to the speed series.
- **A window whose extractors produced nothing is no longer stored as a row of zeros.**
  This is the one that hid the rest: such a row is indistinguishable from "the user sat
  perfectly still", and it was counted toward active minutes, trained on, and used to
  build the synthetic impostors that guard promotion. On the affected machine 84 % of
  stored windows were that. Because scaling a zero changes nothing, the impostors came
  out identical to the real data and the promotion gate became impossible to satisfy —
  which is exactly what it reported, for 247 consecutive cycles.
- **On Windows, one movement now carries one timestamp.** Its two axes were being stamped
  separately, microseconds apart; evdev gives both the time of the same event frame.
- **None of this was specific to Windows.** evdev omits zero axes too and its values are
  deltas as well. It went unseen because the only exercise of this path was `make demo`,
  whose synthetic input moves both axes evenly and never starves one.
- **The path had no test coverage at all.** It does now: `tests/test_mouse_features.py`
  plus two window-retention tests, each verified to fail against the previous release.

Also worth knowing for anyone diagnosing a stalled enrolment: a machine running an
anti-idle utility ("mouse jiggler") feeds it cursor movement that is not yours. Nothing in
this project yet detects that, and it remains the honest limitation to check first.

## 0.5.10 — what the Windows hardware run actually established

No code changes: the binaries are identical to 0.5.9. This release exists to carry the
documentation, because what it now says about Windows is materially different from what
it said before.

- **Alarms reach the Windows Event Log — confirmed on hardware.** Event id 1000 under
  source `behavioral-auth` in the Application log, lifecycle events as *Information* and
  an alarm as a *Warning* (never an Error — an alarm is not a software fault). The
  payload arrives as two insertion strings, a readable `category.action` and the JSON
  body, carrying verdicts and numbers only: ratio, span, reason, face state, ids. No key
  codes, no coordinates, no feature vectors, exactly as on the syslog path. The spool
  stayed empty throughout, so nothing was held back.
- **Documented rather than hidden:** Event Viewer renders the description as blank,
  because no message resource is registered for the source. Everything is in the event's
  data, which is what a Wazuh eventchannel collector reads — so forwarding is unaffected,
  but a person reading the log directly needs `$_.Properties`, not `$_.Message`.
- **Three rows move into Confirmed**: the full state machine `LEARNING → promotion →
  MONITORING → ALARM`, alarms in the Event Log, and the face channel training from a real
  webcam in a local session.
- **The beta caveat now states the reason that matters.** Two of its claims had stopped
  being true and were simply wrong. In their place: **a pattern has never been promoted
  from real behaviour on Windows.** The walk to ALARM was `dev` mode on synthetic input,
  which proves the plumbing and says nothing about learning a usable pattern from a
  person. A real enrolment cleared every volume gate — 1600+ sequences, 400+ active
  minutes — and still failed the promotion sanity gate, because the model reconstructs
  synthetic impostors about as well as the real thing. That is the gate working as
  designed, and it is a question about the model, not about Windows.

## 0.5.9 — the report reads the config you hand it

- **Fixed: `behavioral-report --config <path>` was accepted and then ignored.** The
  command took no arguments at all and never looked at its command line, so the flag went
  nowhere and the report was built from the *default* database instead. Nothing on screen
  said so, which is the worst shape this failure could take — the numbers are real, they
  simply belong to another machine's pattern. `behavioral-auth` and `behavioral-authd`
  had both taken `--config` all along; the report was the one command that did not.
- **An argument it does not understand is now an error rather than a shrug.** Silently
  discarding the unrecognised is what produced the wrong report in the first place, and
  the next unimplemented flag would have inherited exactly the same trap.
- **Fixed: resuming the learning history could come back one cycle behind.** The last
  recorded cycle was selected by timestamp, and DuckDB's clock resolves to the
  millisecond — two cycles written inside one tie, and the query then returns either.
  Cycles are selected by cycle number now, which cannot tie. A running daemon spaces its
  cycles minutes apart and almost certainly never hit this, but the losing side of a tie
  is a short streak, a stale error shape and a rewound sequence high-water mark — two of
  which make promotion *easier*, the failure mode 0.5.8 existed to close.

## 0.5.8 — cycle history survives a restart

- **Fixed: restarting the daemon threw away its learning-cycle history.** Promotion
  wants several *consecutive* stable cycles, and each cycle needs a fresh batch of
  sequences — so a machine that reboots more often than it can gather all of them never
  promoted at all, however settled the pattern was. Every other gate (sequences, active
  minutes, distinct hours) is derived from the database and survived; the cycle state was
  held only in memory. The database had been recording the streak on every cycle from the
  start and nothing read it back.
- **Two related gates had been silently *disabled* by every restart, and both are now
  restored too.** Without the previous cycle's error shape, threshold drift computes as
  zero, so that gate passed for free — a restart was excusing the next cycle from a check
  it was meant to face. And with the sequence high-water mark at zero, every existing
  sequence counted as new, so a cycle fired immediately on restart over data the previous
  cycle had already judged. Restoring only the streak would have handed promotion an
  easier path, not a fairer one.
- History is resumed per enrollment, so `behavioral-auth reset` still starts from
  genuinely nothing, and an unstable last cycle is resumed as unstable rather than
  laundered into a streak.

## 0.5.7 — a restart no longer holds promotion back

- **Fixed: after any restart mid-enrolment, the face gate reported "face pattern not
  ready" with a perfectly good model on disk.** `face_ready` starts false and was only
  recomputed when resuming an already-frozen pattern, or after *this* process had itself
  finished a calibration — never on the ordinary case of a daemon restarting while still
  learning. Since `face.required_for_promotion` is true in both shipped configs, that
  gate is load-bearing: promotion was blocked until the next calibration happened to
  run. It self-healed, which is why it read as a slow start rather than a stuck one.
- **Documentation now describes a Windows box that was actually watched working**, and is
  explicit about the one thing that does not. A service in Session 0 **captures nothing**:
  measured at 144 bytes/sec of background writes against 16 700 in a logged-in session,
  with an idle control period to prove the difference was not input. So the supported
  shape on Windows is a per-user *Task Scheduler* task at logon, not the service the
  installer registers — README and `docs/USAGE.md` say so plainly now, in both languages.
- Also corrected there: the face channel's OpenCV failures were previously attributed to
  another application holding the camera. They were not. An RDP session has no webcam
  unless camera redirection is enabled, and Session 0 has none either; at the physical
  console the face model trains normally. The channel needs a real local session, and the
  only genuine defect is that **nothing about the failure reaches the daemon's own log**.
- Recorded as a known limitation rather than quietly patched: `behavioral-auth status`
  cannot read its state file under a service install, because the daemon running as
  `LocalSystem` creates the run directory owner-only. That restriction is deliberate —
  control commands including `reset` are delivered through that directory — so the fix is
  a design decision, not a patch. Running in your own session avoids it entirely.

## 0.5.6 — a service has no streams to log to

- **Fixed: the Windows service died in logging setup.** A frozen service process has
  `sys.stderr` set to `None` — there is no console attached — and the logger was
  attached to it unconditionally, so startup raised
  `TypeError: Cannot log to objects of type 'NoneType'` before anything else ran. The
  file sink that a service actually logs through is added a few lines later and never
  got the chance. Visible only under the SCM: `debug` runs in a console where stderr
  exists, so every manual check passed.
- **0.5.4's fix is confirmed working against the real Service Control Manager.** The
  service now connects and reports started, and the failure moved from a 120-second
  timeout (events 7000/7009) to an immediate service-specific error — which is how the
  defect above was found. Three defects sat in a row, each hiding the next; the chain
  is set out in the README.
- **The status console no longer crashes without a console.** A process with no
  `sys.stdout` gets no status block whatever `daemon.console` asks for, rather than
  failing on `None.isatty()` under `auto` or on the first write under `always`. This
  was latent rather than observed, and it matters for running `behavioral-authd.exe`
  from Task Scheduler at logon — the documented fallback where a Session 0 service
  cannot see the desktop, and which has no console window either.

## 0.5.5 — the release that carries both artifacts

No product change whatsoever: the code is identical to 0.5.4. The build check
introduced in 0.5.4 ran the service executable, which is *meant* to fail from a
console, and then inherited that failure as its own result — so the job died after
building and before uploading, and 0.5.4 shipped without a Windows installer. The
step now reports the expected exit code and returns success explicitly.

**If you are on Windows, this is the release to install**; 0.5.4 has no installer.
On Linux 0.5.4 and 0.5.5 are the same software.

## 0.5.4 — the Windows service actually starts

> **Linux only.** No Windows installer was published for this version: the new
> build-time check on the service executable failed the job after the bundle was
> built but before it was uploaded. The check was right and the fix below is in the
> code; only the packaging step was lost. Windows users want **0.5.5**.

- **Fixed: the service executable never talked to the Service Control Manager.** The
  SCM launches it with **no arguments** and waits for it to call
  `StartServiceCtrlDispatcher` and connect back. It instead printed its usage text and
  exited, so the SCM waited out its 120-second timeout and reported events 7000/7009,
  "did not respond to the start signal in time". A one-line omission, and the actual
  reason the service had never once started.
- **Why it survived three releases.** Every invocation a person types — `install`,
  `start`, `stop`, `remove`, `debug` — carries arguments and went down a different code
  path, the only one that existed, and that path worked. The one invocation nobody
  tries by hand is the only one the SCM performs. Along the way the same symptom was
  read as a hang, as the Session 0 limitation, and as a configuration error; each
  reading was checked with a command that took the working path.
- **0.5.3's fix was real, but it was not this.** It removed a second, independent
  defect that sat one step further along — the service could not find its
  configuration — which is why fixing it changed nothing visible. Both are needed.
- The release build now runs the service executable with no arguments and fails if it
  prints usage instead of failing with `StartServiceCtrlDispatcher` error 1063, which
  from a console is what a correctly wired service does.
- **The Linux AppImage is back**, having been missed by 0.5.3.

## 0.5.3 — the Windows service can find its configuration

> **Windows only.** No AppImage was published for this version: a test that passed on
> Windows and failed on Linux stopped the `appimage` job before it built anything, so
> the release carries the installer alone. The defect was in the test, not in the
> shipped code. Linux users should stay on 0.5.2 — nothing here changes Linux
> behaviour — and the AppImage returns with 0.5.4.

- **Fixed: the Windows service could not start at all.** It died resolving its
  configuration before it could report to the Service Control Manager, and the SCM
  has no way to describe that except events 7000/7009 — "did not respond to the start
  signal in time". So it read as a hang, or a slow first import, and was neither. It
  was also **not** the Session 0 limitation: execution never reached the input hook.
- **Two independent causes, either one sufficient.** The config search path had no
  Windows equivalent of `/etc`, so the editable config the installer writes to
  `%PROGRAMDATA%\behavioral-auth` was reachable only through the machine-wide
  `BEHAVIORAL_AUTH_CONFIG` — and **a machine-wide variable set during install is not
  visible to a service until the machine reboots**, because services inherit an
  environment block cached at boot. That path is now searched, so the variable is an
  override again rather than the only way in. Separately, the bundled default config
  was packaged into the bundle under its source name, `config.windows.yaml`, while the
  loader only ever looks for `config/config.yaml` — a PyInstaller `datas` entry cannot
  rename, since its second element is a destination *directory*. The out-of-the-box
  fallback therefore did not exist on Windows at all. The Linux packaging was always
  correct, which is why neither showed up there.
- The search list is built per call rather than frozen at import: `%PROGRAMDATA%` is
  read from an environment a service does not share with an interactive shell.
- **Documentation now reports what was watched working on hardware**, and is careful
  about what was not. Confirmed live: the `pynput` hook capturing real keyboard and
  mouse input, a full learning cycle including the promotion sanity gate, and the
  frozen service host reaching `LEARNING`. Still unconfirmed and said so plainly:
  capture under the SCM — that run was in `debug`, which executes in the interactive
  session and deliberately does not answer the Session 0 question — promotion to
  MONITORING, and an alarm reaching the Event Log.
- **New known limitation: the face channel can fail silently.** Where the camera is
  present but unavailable to OpenCV, every frame grab fails and the daemon's own log
  says nothing — the only trace is library output on stderr, which a service discards.
  Documented in `docs/USAGE.md`; not yet fixed.

## 0.5.2 — Windows could not finish learning

- **Fixed: on Windows a learning cycle died just before promotion.** The Windows
  `torch==2.4.0+cpu` wheel is built against numpy 1.x, so under the pinned numpy 2
  a trained model could not hand its reconstruction errors back as an array
  (`RuntimeError: Numpy is not available`). The model trained to completion and then
  failed on the way out, so the pattern was never frozen. numpy is now pinned per
  platform, the way `evdev` and `pynput` already were. Linux is unaffected and
  unchanged.
- **The training path is now tested at all.** Nothing in the suite called `fit` or
  `reconstruction_errors` on any operating system, which is how the above shipped.
  The new tests assert the full numpy → torch → numpy round trip: the inbound
  direction kept working through the defect, so testing it alone would have passed
  on a build that cannot return a result. They run in the Windows CI job too, which
  previously never imported torch.
- The two SIEM tests that need an `AF_UNIX` socket now skip on Windows instead of
  failing, and the network-sink half of one of them was split out so it still runs
  there. The Windows suite is a clean green rather than two failures to recognise.
- **Documentation now reflects a real Windows box.** The installer and a clean
  uninstall were watched working on hardware; capture and alarms still have not been.
  A **known defect** is recorded in both README and `docs/USAGE.md`: the Windows
  service does not start — the SCM reports 7000/7009, the process never connects
  before the 120-second timeout, and this is distinct from the Session 0 caveat.
  Until it is fixed, run `behavioral-authd.exe` in your own session.

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
