# Changelog

## 0.6.0 — it stops recording which key you pressed

The database no longer stores the key code of a keystroke. It stores a keyboard **zone**
— hand and row, plus space, backspace and modifiers — and a short-lived pairing number
that exists only so a press can be matched to its release.

Minor rather than patch: the on-disk schema changes, and so does what the product keeps
about the person using it.

### Why this cost nothing

`ev_code` had one consumer in the whole source tree, and in the keyboard path it was used
for exactly two things: pairing a press with its release, which needs only *equality*, and
recognising backspace, which is one bit. Nothing computed anything per key —
`f_ks_entropy`, despite the name, is the entropy of the **dwell-time** histogram, not of
the key distribution.

So a full keylog was being written to disk to produce eight numbers, none of which knows
what was typed. A test extracts the same keystrokes twice, once in the old shape and once
in the new, and asserts all eight features match: this changes what is stored, not what is
measured.

### Details worth knowing

- **A zone alone cannot pair a press with its release.** Typing "as" quickly gives a-down,
  s-down, a-up, s-up, and both letters are on the left home row — a zone-keyed map would
  mispair a-up with s-down and report a nonsense dwell. Rollover is ordinary fluent typing,
  so the pairing number is not optional. There is a test for that exact sequence.
- **Backspace keeps a zone of its own** rather than joining an "edit" class with Delete.
  `f_ks_backspace_ratio` counts backspace and nothing else, and widening it would quietly
  redefine a feature that existing patterns are built on.
- **Hashing the key code was rejected.** With about 256 possible values a lookup table is
  instant, and the salt would have to be stored for pairing to survive a restart. It would
  have looked like protection without being any.
- The rewrite happens in the writer, the single point every event passes on its way to the
  database, so "no key code reaches disk" is a property of one file rather than a claim
  about the three input backends.

### Upgrading

Migration `005_zones.sql` adds two nullable columns and **rewrites nothing**. Events
captured by earlier versions keep their real key codes and are still read that way, chosen
per row — so a window spanning the upgrade works, and `rebuild-features` still works over
older history. Existing key codes are not purged: that is the material `rebuild-features`
recomputes from, so removing it is a decision for its owner rather than for a migration.

Said plainly, because it is not nothing: a stream of zones and timings is a very lossy
transcript, not a blank one. With enough text it narrows what might have been typed. It
does not reveal it.

## 0.5.14 — the mouse features were wrong in the tails, and a set could vanish

Two defects found on the running machine within hours of 0.5.13, plus the command that
turns a feature-extraction defect into a recomputation instead of a reset.

### Mouse speed could reach 4.3 million pixels per second

`_motion_samples` started a new sample whenever an axis repeated — **including when the
timestamp had not moved**. Two `REL_X` reports carrying one clock tick therefore became two
samples separated by zero, and `dt` was floored at `1e-6 s`: a millionfold amplifier that
bounded the damage without preventing it.

- Measured on real captured data: `f_ms_speed_mean` up to **4 306 000 px/s** against a
  median of 1 208, and `f_ms_acc_mean` spanning −9.7e10 to **4.4e12**. After the fix, the
  same events give a maximum of 97 580 px/s and 9.2e7 — while the medians barely move
  (1 208 → 1 133), which is what a fix that removes an artefact rather than the signal looks
  like.
- Two reports in one tick now **sum** into one sample: within a tick they are not resolvable
  in time, and the device did move the sum of them. The floor is now 1 ms — the fastest any
  consumer mouse reports — instead of 1 µs.
- **`behavioral-auth rebuild-features`**, so a defect in feature *extraction* costs a
  recomputation rather than a reset. The raw events are never discarded; this recomputes the
  windows and sequences from them, and deletes the enrolment's learning cycles with them,
  because a cycle's `shape` is meaningless once the feature space it was measured in is gone.
  Verified on a copy of the production database: 5 656 → 5 658 windows, 1 492 → 1 492
  sequences, nothing lost.
- **This did not make promotion possible, and that is worth saying plainly.** The threshold
  multiplier moved 512× → 471×; detection stayed at 0 %. The outliers were real and worth
  removing, but they were not what makes the gate unsatisfiable here.

- **`consolidate()` could empty a non-empty set, and it did so in production.** Once
  `win:global` became a two-way wildcard in 0.5.13, it and `-/-` subsumed each other and
  both were dropped — so an enrolment made entirely of pre-upgrade rows reported *no*
  hardware stacks. The daemon read that as "nothing known yet", treated the first ordinary
  window after the upgrade as a second set of hardware, and advised `reset` on 1113 perfectly
  good sequences. Observed on the real box within eight minutes of installing 0.5.13.
- **The worse failure was still latent.** `trained_stacks()` feeds a promoted pattern's
  `stacks`, and a pattern entitled to judge nothing rejects every window: had promotion
  happened before new-transport windows accumulated, it would have frozen and then suspended
  for ever. Narrow — it needs the gates to be met at the moment of upgrade — but real.
- A key is now dropped only when another is **strictly** more specific, and a set of mutually
  equivalent keys keeps one representative. Nine tests, including one that pins the exact
  production shape and one asserting no non-empty input can consolidate to nothing.

## 0.5.13 — it knows what it is, what it is looking at, and when it stops looking

Four things, all asked for in one session and all pointing the same way — the daemon should
be able to say what it is, what it is looking at, and when it stops looking.

An update mechanism, answered as a notice and never a download. The cheap half of "is this
input human?", once the mouse jiggler on this machine turned out to be a USB device rather
than software. A SIEM signal for the enrolment's hardware set changing, which was silent
exactly where it mattered most. And the two things that were being worked around by hand
daily: no way to stop the daemon cleanly, and a `--config` that ignored what you told it.

### RDP no longer poisons the pattern

Nothing in the product defended against this, and it had already cost one enrolment. RDP is
not different hardware, it is a different transport: the same person with the link's latency
and batching written into the keystroke timings the model reads.

- **The session transport goes into the hardware-stack key** — `win:global` becomes
  `win:console` or `win:rdp`. That one change makes the whole mechanism already built for
  docks apply: a console pattern does not accept RDP windows, so scoring suspends, `status`
  explains why, and `stack_changed` reaches the SIEM at level 7.
- **Remote sequences are excluded from training and from every promotion gate**, counters
  included. A volume gate satisfied by data the model is not allowed to see would be
  satisfied on false evidence — the same defect as the inflated `active_minutes` that let an
  all-zero-window enrolment sail through its gates.
- **Suspension, not scoring.** Scoring RDP input against a console pattern would alarm at the
  legitimate owner every time they worked remotely. That is the failure the `SUSPENDED` state
  exists for: the comparison is meaningless, not suspicious. `learn-more` is deliberately not
  suggested here, unlike a real hardware change.
- **Detection uses `GetSystemMetrics(SM_REMOTESESSION)` and `WTSClientProtocolType`, and
  either saying "remote" is enough.** The asymmetry is deliberate: erring toward remote costs
  a gap in coverage, erring toward console silently poisons a pattern. Never
  `%SESSIONNAME%` — measured on this box, a shell created during an RDP session still
  reported `RDP-Tcp#0` while sitting on the console, and the mirror case would *permit*
  collection over RDP.
- **The upgrade path is the risky part and is handled explicitly.** `win:global` survives as
  a legacy marker meaning "that build could not tell", and is a wildcard in both directions —
  otherwise installing this would make an existing enrolment look like foreign hardware and
  suspend scoring on the owner's own machine. It is subsumed rather than merely matched, so
  an enrolment that continues across the upgrade stays **one** stack instead of becoming a
  wider, more permissive two. It cannot grant more reach than the old build already had, and
  it disappears at the first fresh enrolment. Four tests cover exactly this.
- 18 new tests (235 → 253).

### `behavioral-auth stop`, and `--config` stops lying

- **There was no way to stop a session daemon.** `pause` stops scoring, not collection, so
  keeping data out meant `taskkill` — which a hidden console app cannot answer, so it died
  without its `Stopped cleanly` line and DuckDB replayed the WAL on the next start.
  Survivable, but it was being relied on daily to keep RDP out of an enrolment. `stop` goes
  through the existing control channel: the caller is answered first, then the ordinary
  shutdown runs — writer flushed, session row closed, SIEM spool drained, database closed.
- **It waits for the process to actually go**, up to `--timeout` (30 s). Returning as soon
  as the daemon was *asked* would be useless to the caller, who is usually about to do the
  thing the stop was for and needs DuckDB's lock released.
- **`daemon_stopped` now carries `by_command`**, so a gap in coverage can be read as
  "somebody asked for this" rather than "it died". No new rule: the action is unchanged.
- **`--config` with a path that does not exist is now refused instead of ignored.**
  `config_path()` treats the variable as a *candidate* and falls through to the machine-wide
  file — deliberate, and load-bearing for a frozen bundle on a box with an empty
  ProgramData. But for a path a human just typed it meant a typo silently operating on the
  live pattern; measured, a nonexistent `--config` resolved to the real ProgramData config
  and delivered its command to the running daemon. With `reset` in the command set that is a
  foot-gun. Only the explicit argument became strict, in all three CLIs; the environment
  variable keeps its fallback and a test pins that it does.
- 10 new tests (225 → 235).

### A SIEM now hears when the pattern's hardware set changes

`stack_changed` only ever fired against a **frozen** pattern. The enrolment-time case was
silent, and it is the one that matters more: a pattern learned across two sets of input
devices has a wider spread, so a higher threshold, so it accepts **more**. Swapping the
keyboard or the mouse half-way through enrolment permanently widens the gate an impostor
has to pass, and it happened with nothing forwarded and nobody watching.

- **`ops.enrollment_stack_added`** — WARNING, while LEARNING, when the enrolment starts
  covering hardware it did not. Rules 100218 / 100248, level 7: while the pattern is still
  learning an operator can still undo this with a reset; once frozen they cannot.
- **`ops.pattern_promoted`** — carries `n_stacks` and a `multi_stack` boolean, so a SIEM
  learns not just that a pattern was frozen but **how wide** it is. Rules 100219 / 100249 at
  7 for a mixed pattern, 100220 / 100250 at 3 for a single-stack one. The boolean exists
  because Wazuh's `<field>` matches a regex, not a magnitude — "n_stacks > 1" is not
  something a rule can ask, so the daemon answers it. A test pins that.
- **Growth is detected with `newly_seen()`, not a set difference.** A setup first seen
  through windows of pure typing reports `kbd/-`; the moment the mouse moves the
  consolidated set becomes `kbd/mouse` and the old key is gone. A plain difference would
  have announced new hardware the first time anyone touched their mouse — the same trap
  `consolidate()` was written for, arriving from the other direction.
- **Seeded on first look, never replayed.** Restarting the daemon mid-enrolment does not
  re-announce every stack it already knew about; the promotion event carries the final
  count for exactly the changes that happened while it was down.
- **Still hashed fingerprints and counts only.** No device names, no vendor/product ids —
  Wazuh's syscollector already inventories hardware. A test asserts the ids do not appear.
- **Inert on Windows**, like the rest of the stack machinery: one global hook means one
  apparent device, so the set never grows. The promotion event is *not* inert and reports a
  single-stack pattern there today.
- 19 new tests (206 → 225), including six that pin the daemon's wiring rather than the
  helper alone.

### Injected input is now counted, on Windows

The first thing in this project that asks whether its input was *human*. Half an answer,
and the cheap half — but it is measured rather than inferred.

- **Both Windows low-level hooks flag events synthesised by `SendInput`**, and pynput hands
  the whole hook structure to a filter before dispatching. The flag was already reaching
  Python and was being dropped. `InjectionStats` counts it per channel; `status` shows the
  share; the daemon warns once per channel when it is both large and well-sampled.
- **Nothing is dropped and nothing is refused.** Accessibility tools, on-screen keyboards,
  remote support and KVM software all inject legitimately, and discarding their input would
  blind the collector exactly when a user needs it most. It warns, like the rest of this
  product.
- **It answers only half the question, and says so.** A *hardware* jiggler on a USB port
  produces genuine HID events with the flag clear. Seeing one needs per-device identity —
  RawInput / `WM_INPUT` — which pynput does not expose and which this is not.
- **`win32_event_filter`, not `event_filter`.** pynput drops an option that lacks the
  platform prefix silently: no error, and a listener that still reports `running`. Measured
  on Windows, the unprefixed name gave zero filter calls while input kept flowing — a
  detector reporting a clean machine forever. A test pins the prefixed name, because
  nothing else would have caught it.
- **Verified in both directions on real hardware**, not just unit-tested: 60 moves injected
  through `SendInput` were counted 60/60 as injected with the cursor never moving, and 449
  genuine physical mouse events were counted 0/449. No false positives, no false negatives.
- 11 new tests (195 → 206).

### Version and update notice

- **`behavioral_auth.__version__` is the single source of truth.** Nothing in `src/` knew
  which build it was: the version lived in `pyproject.toml`, in the git tag and in
  `installer.iss`, and attributing a log file to a build meant matching line numbers in
  tracebacks. `pyproject.toml` now reads the attribute instead of carrying its own copy,
  all four executables take `--version`, and the daemon logs it as its first line.
- **The release workflow refuses a tag that disagrees with it**, in both jobs and before
  anything is built. A binary that misreports its own version would tell every user an
  upgrade is available forever, since that string is exactly what the new check compares.
- **`updates.check_enabled`, off in both shipped configs.** While it is false the daemon
  makes no request at all. Switched on, it asks once a day, caches the answer beside
  `state.json`, and surfaces it in `behavioral-auth status` and `behavioral-report` —
  neither of which touches the network. `behavioral-auth check-update` asks on demand
  regardless of the flag: that setting governs the unattended check, and typing the
  command is itself the decision.
- **There is no download path, and that is the design.** This daemon reads every keystroke
  on the machine and starts at logon, so a channel able to fetch and execute new code here
  would be the single most valuable thing on the box to subvert. `assets` and
  `browser_download_url` are not read; a test feeds a payload containing them and asserts
  they never reach the status.
- **What the request discloses: nothing about the machine.** One HTTPS GET, a constant
  `User-Agent` with no version in it, no query string, no token. `updates.url` must be
  https — over plain http the interesting attack is not a forged version but suppression,
  answering "you are up to date" and hiding a security fix — and it can be repointed at an
  internal mirror.
- **Versions are compared as integer tuples, never as strings.** `'0.5.2' > '0.5.12'`
  lexicographically, and this project has shipped both; a string comparison would announce
  a downgrade at every status call. Tags with a suffix (`v0.6.0-rc1`, and the
  `v0.3.0-citest3` this repository has actually published) are treated as not comparable
  rather than truncated, so a pre-release is never offered to anybody.
- **A failed check waits the full interval.** An unreachable network is the normal case
  for this product, not a reason to retry on every five-second tick or to warn daily.

- 47 new tests (148 → 195). The version test found this checkout's editable install three releases
  stale at 0.5.9 on its first run, which is the failure it was written for.

## 0.5.12 — the Windows Event Log path gets a Wazuh decoder, and it ships

No change to the daemon: the binaries behave exactly as 0.5.11. What changes is that
forwarding to a SIEM from Windows is now something you can actually set up.

- **A decoder and ruleset for the Event Log path** (`packaging/wazuh/0911-*`). The syslog
  pair could never serve Windows — there is no RFC 5424 frame there at all. The daemon
  writes event id 1000 under provider `behavioral-auth` to the Application channel, and
  the agent wraps the whole Windows event in Wazuh's own JSON envelope, so this is a second
  decoder beside the first rather than an edit to it.
- **Rules 100230–100247 mirror 100200–100217 with identical levels**, deliberately: same
  events, same meanings, different transport. If they drifted, one incident would read as
  two different severities depending on which operating system raised it. A test pins that
  they stay equal.
- **Both pairs are now attached to the release** as `behavioral-auth-wazuh.zip`. They are
  manager-side configuration — installed on the SIEM, not on the machine running the
  daemon — so they cannot ride inside the AppImage or the installer, and until now the only
  way to get them was to clone the repository.
- **`docs/USAGE.md` explains the Windows forwarding path properly**, in both languages:
  how to install the decoder, how to confirm from the agent's own counters that the
  Application channel is already being collected (usually it is, which means events are
  reaching the manager and matching no rule — a silent drop in Wazuh), why Event Viewer
  shows a blank description and what to read instead, and why not to narrow a collector
  that somebody else's detection may depend on.
- **The decoders' first test coverage.** Twelve tests run the prematch and every field
  regex against payloads captured verbatim from a real Application log, in both escaped
  and unescaped form. Invented samples were avoided on purpose: they would let a decoder
  and its test drift together.

Unchanged and still true: **neither pair has been verified against a real Wazuh manager.**
`packaging/wazuh/README.md` carries the `wazuh-logtest` procedure that closes it, and says
plainly not to install these on a shared manager without asking first.

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
