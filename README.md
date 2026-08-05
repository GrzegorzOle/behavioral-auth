# behavioral-auth

A local-only daemon that learns how **you** type and move the mouse, freezes that
pattern, and then warns you when the person at the keyboard stops matching it.

It **never locks the session and never logs anyone out.** The strongest thing it
does is write a warning to the log, the console, and (optionally) a desktop
notification. That is a deliberate design constraint, not an unfinished feature.

Everything runs on your machine: DuckDB on disk, ONNX on the CPU, and no network
unless you switch on SIEM forwarding or the update check yourself — see [What
leaves the machine](#what-leaves-the-machine). Both are off in the shipped
configuration, and **there is no auto-update**: the daemon can tell you a newer
release exists, and that is the whole of it. [Why it stops
there](#updates-are-a-notice-never-a-download).

**Linux and Windows.** Every release ships a Windows installer and a Linux
AppImage, both self-contained — see [Install](#install). Linux is the older and
more thoroughly exercised of the two; the Windows build is newer, and
[Platform support](#platform-support) sets out exactly which parts of it have
been confirmed and which have not.

---

## What it actually does

```
first start, empty machine
        │
        ▼
   ┌─────────┐   collects keystroke + mouse behaviour,
   │  NAUKA  │   silently photographs your face in the background,
   │LEARNING │   retrains every so often and checks whether the
   └────┬────┘   pattern has stopped moving
        │
        │  ... enough data + N stable cycles in a row + sanity gate
        ▼
   ┌────────────┐   pattern is FROZEN. Scores live behaviour against it.
   │   NADZÓR   │   Nothing retrains here — a stranger cannot teach the
   │ MONITORING │   system to accept them just by using the computer.
   └────┬───────┘
        │  ... behaviour deviates, and keeps deviating
        ▼
   ┌─────────┐   logs, prints, notifies. Locks nothing.
   │  ALARM  │   Clears itself when normal behaviour returns.
   └─────────┘

   ┌─────────────┐   you docked, or swapped keyboards. The pattern was learned
   │ ZAWIESZONY  │   on other hardware, so there is nothing meaningful to
   │  SUSPENDED  │   compare against — it stops scoring and says so. Never an
   └─────────────┘   alarm. Collection continues.
```

The pattern only ever changes when **you** say so — `behavioral-auth reset`
(someone else is going to use this machine) or `behavioral-auth learn-more`
(refine what you have). There is no automatic adaptation anywhere in the code.

Nothing expires either: a pattern is still valid after a two-week holiday, and
you do not need to refresh it before coming back. But behaviour does move over
weeks, so `behavioral-report` shows the pattern's age and the median deviation
week by week. If the trend is climbing, that is you drifting **or** somebody else
at the keyboard — this system cannot tell those apart and does not pretend to.
The report prints the number and leaves the judgement to you.

### The pattern belongs to one set of hardware

You do not type the same way on a laptop keyboard as on an external one through a
dock, and you do not move a trackpad like a mouse. So a pattern is bound to the
hardware it was learned on, and behaviour from other hardware is **not scored at
all** rather than scored badly.

This is not politeness about false alarms. A pattern trained across a *mixture* of
two setups has a wider spread, so its threshold sits higher, so it accepts more —
mixing hardware during enrolment makes the system **less** able to notice a
stranger, not just more likely to nag you. If you enrol docked and then undock,
the daemon suspends scoring and tells you; `learn-more` will fold the second setup
in, and will warn you at that moment about exactly this cost.

### It notices when input is being synthesised — on Windows, and only half of it

A behavioural biometric enrols whatever moves the cursor. Point an anti-idle jiggler or a
macro tool at the machine during enrolment and the pattern it freezes is a pattern of that
software, not of you — and the same trick is available to an attacker on purpose.

On Windows the low-level hooks flag events that came from `SendInput` rather than from a
device, so the daemon counts them. `behavioral-auth status` shows the share per channel,
and the log says so once, loudly, when a channel is mostly synthetic:

```
  wstrzyknięte klawiatura 0.0%, mysz 67.4%  ← coś syntetyzuje wejście na tej maszynie
```

**Nothing is dropped and nothing is refused.** Screen readers, on-screen keyboards, remote
support and KVM software all inject legitimately; discarding their input would blind the
collector exactly when someone most needs it. This tells you, and the judgement about
whether to trust the enrolment is yours.

**It is half an answer, and the half it misses matters.** A *hardware* jiggler on a USB
port produces genuine HID events with the flag clear — nothing here can see one. Catching
that needs per-device identity (RawInput / `WM_INPUT`), which is not implemented; on Linux
the hardware-stack fingerprint above would show it, on Windows every event still claims one
global device. Linux has no equivalent of the injected flag at all, so `status` there shows
nothing rather than a reassuring zero.

---

## Be clear about what this can and cannot tell you

This matters more than any feature, so it comes before the install instructions.

**There is no impostor data.** The system only ever sees one person: you. That
has hard consequences:

- **No false-accept rate can be measured.** Not by this system, not by any
  amount of tuning. `behavioral-report` deliberately refuses to print FAR/FRR
  figures — an earlier version computed them from your own scores, which made a
  meaningless number look like a security metric.
- What the promotion gate *does* verify: (1) the pattern has **converged** — the
  model reconstructs fresh, never-trained-on behaviour as well as it does its
  training data, and the threshold has stopped moving; (2) the model is **not
  degenerate** — it reliably flags synthetic impostors built by distorting your
  own data. That second check is not a formality. An autoencoder handed its own
  target learns to copy it, scores a beautiful, stable, low error for *every
  human alive*, and would never fire on anyone. The gate exists to catch exactly
  that, and during development it caught it.
- The promotion message says all of this out loud, including which kinds of
  difference the trained model turned out to be **blind** to.

**Face recognition is a corroborating signal, not a gate.** LBPH is trained with
a single label, so it can only answer "how confident am I that this is the
enrolled person" — the calibrated confidence cut-off is the entire decision. An
unknown face is treated as *no evidence*, never as evidence of an intruder.

Treat this as a tripwire that notices when something has changed. Do not treat it
as an access-control mechanism.

---

## What leaves the machine

**By default: nothing.** With `siem.enabled: false` and `updates.check_enabled:
false` — which are the shipped settings — no code path in this tree opens a
socket. The behaviour you record, the model trained on it and the photographs of
your face stay in `/var/lib/behavioral-auth` and are read by nothing but this
daemon.

Exactly two things can change that, and each takes a deliberate act: SIEM
forwarding (below) and the [update notice](#updates-are-a-notice-never-a-download).

You can turn on forwarding to a SIEM (local syslog, or straight to a Wazuh
manager). If you do, this is the complete list of what is sent:

| Sent | Never sent |
|---|---|
| Alarms: raised and cleared, with `reason`, `ratio` (a number like `4.54`), how long they ran | Key codes, key names, anything you typed |
| State transitions: `LEARNING → MONITORING → ALARM`, and why | Mouse coordinates or movement |
| Operations: daemon start/stop, `pause`, `learn-more`, and `reset` | Photographs of your face, or any frame from the camera |
| Whether the camera matched, as the word `match`, `stranger` or `unknown` | Feature vectors, sequences, model weights, the scaler, the threshold |
| Input devices attached or lost, and changes of hardware stack, as a **hashed** fingerprint | Device names, vendor/product ids — anything amounting to a hardware inventory |
| The hostname, and the enrolment/session UUIDs | Per-sequence scores — at a 5 s stride that is hundreds an hour |

The event carries a **verdict and a number, never the behaviour they were computed
from.** `StateStore.transition` takes a free-form `details` dict for the local
database; it is deliberately *not* forwarded, so that a field added there later
cannot start leaving the machine without someone deciding that it should.

### The local copy is not as gone as it looks

`siem.store_alarms_locally: false` stops alarms being written to DuckDB. It does
**not** make them stop existing locally:

- With `sink: syslog`, the event is handed to `/dev/log`, so it also lands in
  **journald or rsyslog on this same machine**, and stays there for as long as
  your system's log retention says. You removed one local copy, not all of them.
- Undelivered events wait in the **disk spool** (`siem.spool_path`) until the SIEM
  acknowledges them. If the SIEM is unreachable for a day, that is a day of alarms
  sitting in a file. They are removed once delivered.

If you want no local trace of alarms at all, `sink: wazuh` sends them over the
network without going through the local syslog — and even then the spool holds
whatever could not be delivered. There is no configuration in which an event both
survives a broken link and leaves no local trace; those two wishes contradict each
other, and this daemon picks *not losing the event*.

### Updates are a notice, never a download

**There is no auto-update, and this is a decision rather than a missing feature.**
Switched on, the daemon asks once a day whether a newer release exists and tells
you. It does not fetch it, does not verify it, does not run an installer. The code
to accept a binary is not disabled behind a flag — it is absent.

The reason is what this program is. It reads every keystroke on the machine and
starts by itself at logon. A channel that could pull new code and execute it here
would be the most valuable thing on the box to subvert: compromise the release
account once and every installation becomes a keylogger, with nobody present to
notice. Weigh that against the convenience of not visiting a download page a few
times a year.

```yaml
updates:
  check_enabled: false     # shipped off; no request is made at all while it is
  url: "https://api.github.com/repos/GrzegorzOle/behavioral-auth/releases/latest"
  interval_hours: 24
  timeout_sec: 5.0
```

What the request contains: nothing about you. One HTTPS GET, a constant
`User-Agent: behavioral-auth` with **no version in it** — telling a third party
which build of a security tool a given address runs is a disclosure, not a
feature — and no query string, no token, no identifier. The answer is read for two
strings, the release tag and the release page URL, and the rest is dropped. The
URL must be `https`; over plain http anyone on the path could answer *you are up
to date* and quietly suppress a security fix. Repoint it at an internal mirror if
you have one: anything that answers with a JSON object carrying `tag_name` works.

`behavioral-auth check-update` asks immediately, whatever `check_enabled` says.
That flag governs the *unattended* check — the one that would reach the network
with nobody around — and typing the command is itself the decision it would
otherwise be making for you.

Where the answer shows up: a line in `behavioral-auth status` and a section in
`behavioral-report`, both read from a small cache file next to `state.json`.
Neither command ever touches the network.

If you would rather your organisation decided when this upgrades, do not enable
any of it — package the release for winget, Chocolatey or your Linux repository
and let the existing policy handle it, the same as any other software on the box.

---

## Install

Three ways to get it. Every tagged release carries a ready-to-run build for both
operating systems — nothing needs compiling.

### Windows — installer

Download **`behavioral-auth-setup-<version>.exe`** from the
[releases](../../releases) and run it as administrator. It installs to
`C:\Program Files\behavioral-auth\`, drops an editable config in
`C:\ProgramData\behavioral-auth\`, and registers an auto-starting service.
Uninstall through *Apps & features*. Full walkthrough in
[docs/USAGE.md](docs/USAGE.md).

Works today: the suite installs, the CLI and the report run, the service
registers. **Two things to know before you rely on it.** Nobody has yet confirmed
capture from a service in *Session 0* — if `status` sits in `LEARNING` with no
sequences while you type, run `behavioral-authd.exe` in your own session instead,
which is the documented fallback. And the hardware binding described above does
not apply on Windows: `pynput` is one global hook and cannot tell keyboards
apart. See [Platform support](#platform-support).

### Linux — AppImage

Download **`behavioral-auth-x86_64.AppImage`**, `chmod +x` it, and run it as a
multi-call binary:

```bash
./behavioral-auth-x86_64.AppImage authd      # also: auth, report, face
```

You still need the `input` and `video` groups, a writable data directory, and
FUSE 2 — the AppImage bundles the application, not the system setup. The
prerequisites are three commands, listed in [docs/USAGE.md](docs/USAGE.md).

### Linux — from source

The tested path, and the one to use if you want the systemd unit and udev rules
set up for you. Requires Python 3.11+.

```bash
git clone <repo-url> behavioral-auth && cd behavioral-auth
make venv

sudo usermod -aG input,video "$USER"   # then log out and back in
```

Fedora/RHEL and Ubuntu/Debian system installers live in `src/scripts/`.

---

## Run

```bash
behavioral-authd          # that's it — creates the database, starts learning
```

No schema step, no manual enrolment, no pipeline to run by hand. On a machine
with nothing on it, the daemon creates the database, applies its migrations,
opens an enrollment and starts collecting. The console shows where it is:

```
╭─ behavioral-auth ───────────────────────────── NAUKA ─╮
│ wzorzec 3f9a1c2b   czas 01:42:07                      │
│ sekwencje   842/1200  [████████████░░░░░░]            │
│ aktywność    64m/90m  [█████████████░░░░░]  godzin 2/3│
│ twarz        48/60    [███████████████░░░]            │
│ cykl 3  seria stabilnych 1/3                          │
│ ✓ pass_rate 0.94  err_ratio 1.31  separacja 4.2x      │
╰───────────────────────────────────────────────────────╯
```

Under systemd there is no console and everything goes to the journal:

```bash
systemctl --user enable --now behavioral-authd
journalctl --user -fu behavioral-authd
```

### Try the whole thing in two minutes

Learning normally takes hours of real use, and testing the alarm would take a
second person. So there is a synthetic input source that runs on an accelerated
clock (refused in `prod` mode — hence `--mode dev`, which also merges
`config.dev.yaml` and shrinks every promotion gate):

```bash
make demo        # behavioral-authd --mode dev --synthetic-input user --synthetic-speed 40
```

Watch it learn, converge, promote, and switch to MONITORING — about a minute.
Then, in another terminal, put a different person at the keyboard:

```bash
make demo-impostor        # behavioral-auth set-profile impostor
```

The deviation climbs past the threshold, and once it *stays* there, ALARM.

A pattern promoted under `--mode dev` is a smoke test, not a pattern to rely on;
`behavioral-auth reset` clears it. The demo writes to the same `data_dir` as a
real run, so point `BEHAVIORAL_AUTH_CONFIG` at a scratch config if you would
rather not touch it.

---

## Commands

| Command | What it does |
|---|---|
| `behavioral-authd` | The daemon. Learns, then watches. |
| `behavioral-auth status` | Current state and progress. Works while the daemon runs. |
| `behavioral-auth reset` | **Somebody else will use this machine.** Destroys the pattern and all face crops, starts learning from zero. |
| `behavioral-auth learn-more` | Refine the existing pattern with more data. Explicit, never automatic. |
| `behavioral-auth pause` / `resume` | Stop/start scoring (collection continues). |
| `behavioral-auth set-profile user\|impostor` | Swap the synthetic person mid-run. Only does anything against a daemon started with `--synthetic-input`. |
| `behavioral-auth check-update` | Ask whether a newer release exists. **Tells you; downloads nothing.** Works regardless of `updates.check_enabled`. |
| `behavioral-report` | Learning cycles, scores, alarms, pattern age and week-by-week drift. No FAR/FRR — see above. |
| `behavioral-face info` / `verify` | Inspect and test the face pattern the daemon built. |

Every executable also takes `--version`, and the daemon writes its version as the
first line of the log — which build produced a log file used to be answerable only
by matching line numbers in tracebacks.

The daemon holds DuckDB's single write lock for its whole life, so the CLI talks
to it through a control spool rather than the database. When no daemon is
running, the same commands operate on the database directly.

---

## Configuration

`config/config.yaml`, with a `config.<mode>.yaml` overlay merged on top. Point
`BEHAVIORAL_AUTH_CONFIG` at a file to override the search path.

`general.mode` defaults to **`prod`** — the gates below, which take hours of real
use to satisfy. Setting it to `dev` merges `config.dev.yaml` and shrinks every one
of them so the whole path runs in minutes; a pattern promoted under those gates is
a smoke test, not something to rely on, and the daemon says so loudly on startup.

The knobs that decide behaviour:

```yaml
learning:
  min_sequences: 1200           # roughly 2-4 h of real, active use
  min_active_minutes: 90        # summed window coverage, not wall-clock
  min_distinct_hours: 3         # you must be seen across the day, not one burst
  stable_consecutive_cycles: 3
  stability:
    false_alarm_max: 0.02       # never promote a pattern that would flag YOU
    sanity_detection_min: 0.90  # ...or one that detects nobody at all

alarm:
  enter_consecutive: 16         # a burst of scores is not a sustained anomaly:
  enter_min_span_sec: 120       # adjacent sequences overlap, so span matters too
  clear_consecutive: 16
  clear_min_span_sec: 120

face:
  enabled: true
  confidence_threshold: auto    # calibrated from your own held-out crops
  keep_samples: true            # crops stay in face_samples/, 0700, wiped on reset
```

There is no `lock_cmd` and no `enforce` mode. They were removed from the code,
not just disabled in the config.

### Sending events to a Wazuh manager

`packaging/wazuh/` holds a decoder and a ruleset. They install on the **manager**,
not on this machine — decoding is manager-side. Without them a Wazuh manager
receives the events and matches no rule, which means it drops them silently: no
alert, and no archive unless `logall_json` is on.

If a Wazuh agent on this box already collects journald — the Fedora default — the
events reach the manager with no agent-side change at all. Verify with
`journalctl -f SYSLOG_FACILITY=10`; **not** with `journalctl -t behavioral-auth`,
which finds nothing even when forwarding works, because journald parses RFC 3164
and these events are framed as RFC 5424.

The decoder and rules have been checked for well-formedness and against captured
event frames, but have **not** been run on a real Wazuh manager. `wazuh-logtest`
is the one-minute way to confirm them on yours; `packaging/wazuh/README.md` has
the procedure.

---

## Privacy

Everything stays on the machine. Two things are worth knowing:

- **Keystroke *codes* are recorded**, along with their timings — enough to know
  you pressed key 30, not which character your layout maps it to, but treat
  `behavior.duckdb` as sensitive anyway.
- **Face crops are stored** in `face_samples/<enrollment>/` (0700, 150×150
  greyscale) so the confidence threshold can be recalibrated without a fresh
  enrolment. Set `face.keep_samples: false` to keep only the trained model, or
  `face.enabled: false` to never open the camera. `behavioral-auth reset` deletes
  them.

---

## Platform support

**Linux — built, run and verified here.** Collection is `evdev`; the release ships
a self-contained AppImage.

**Windows — shipped since 0.4.0, and a beta.** The suite is complete: a `pynput`
input backend producing the same event rows as the evdev collector, an Event Log
SIEM sink, a Windows service, and an installer, all built by CI on every tagged
release and downloadable now.

Rather than a vague status, here is the actual split:

| Confirmed | Not yet confirmed |
|---|---|
| The bundle builds and freezes on a Windows runner | Promotion to MONITORING **on real behaviour** — see below |
| `behavioral-auth.exe` and the report run | The Wazuh agent decoding an Event Log alarm |
| The installer compiles and produces a working `.exe` | |
| The OS-agnostic logic (keycode map, event shaping) is unit-tested | |
| **The installer installs on a real box** — Program Files layout, an editable config in ProgramData, the machine-wide `BEHAVIORAL_AUTH_CONFIG`, the service registered auto-start | |
| **Uninstall is clean** — the service deregisters and `C:\ProgramData\behavioral-auth\` survives, as intended | |
| **The `pynput` hook captures real keyboard and mouse input** in a user session | |
| **A full learning cycle completes** on Windows — training, scoring and the promotion sanity gate | |
| **The service starts and runs under the SCM** — `Running`, no error events | |
| **A Session 0 service captures nothing** — measured, see below | |
| **The whole state machine runs**: LEARNING → promotion → MONITORING → ALARM | |
| **Alarms reach the Windows Event Log** — right severity, full payload, empty spool | |
| **The face channel trains** from a real webcam in a local session | |

All of this was watched on a live Windows box on 2026-07-29/31; everything unmarked runs
in CI on every release.

**The Event Log path, in detail, because it is the one people ask about.** With
`siem.enabled: true` the daemon writes event id **1000** under source `behavioral-auth` in
the Application log. Severity maps as designed — lifecycle events are *Information*, an
alarm is a *Warning*, never an Error, because an alarm is not a software fault. The
payload arrives as two insertion strings: a readable `category.action` and the same JSON
body the syslog sinks send, carrying verdict and numbers only — ratio, span, reason,
face state, ids. No key codes, no coordinates, no feature vectors. The spool stayed empty
throughout, so nothing was held back.

One rough edge: Event Viewer renders the **message body as blank**, because no message
resource is registered for the source. The data is all in `EventData`, which is what a
Wazuh eventchannel collector reads, so forwarding is unaffected — but a human reading the
log directly sees an empty description and has to look at the event's data tab.

### Run it in your own session, not as a service

**The Session 0 limitation is real, and it has now been measured rather than assumed.**
The service starts correctly under the SCM and its input hook installs and says so in the
log — and then receives nothing at all from the interactive desktop. Windows isolates
services in Session 0; a low-level input hook installed there does not see input delivered
to a user's session.

How it was measured, because "the hook installed" is not evidence of capture: database
growth over a few minutes of real typing and mouse movement was compared against an
equally long period with nobody touching the machine. The idle period grew **slightly
more** (165 vs 144 bytes/sec) — a flat background rate with no input-driven component.
Mouse movement alone emits hundreds of events per second, so genuine capture would have
been megabytes, not a rate indistinguishable from idle.

**So install it to run in your own logged-in session** — a per-user *Task Scheduler* task
"at log on" is the shape that works. Measured the same way at the physical keyboard on the
same machine: **16 700 bytes/sec against the service's 144**, a hundredfold difference.
`behavioral-auth status` works in this shape too, because the state file is then created
by your own account rather than by `LocalSystem`.

The installer still registers and starts the service; stop and disable it if you use the
scheduled task, because DuckDB takes an **exclusive** lock and two daemons cannot share
the database — the second one just loses.

**The Windows service could not start at all before 0.5.6 — three defects in a row, each
hiding the next.** Every one of them only ever showed up under the Service Control
Manager, because every command a person types (`install`, `start`, `stop`, `debug`) takes
a different code path, and those paths worked.

1. The SCM starts the executable with **no arguments** and waits for it to connect back.
   It printed its usage text and exited instead, so the SCM waited out its 120-second
   timeout and logged events 7000/7009, "did not respond to the start signal in time" —
   which reads like a hang and was a process that had already exited cleanly. *Fixed in
   0.5.4.*
2. The service could not find its configuration: no Windows equivalent of `/etc` in the
   search path, and a bundled fallback config packaged under a name nothing looked for.
   *Fixed in 0.5.3*, one step past defect 1, so on its own it changed nothing visible.
3. A frozen service has **no standard streams** — `sys.stderr` is `None` — and the logger
   was attached to it unconditionally, so startup died with `TypeError` inside logging
   setup. *Fixed in 0.5.6.*

**Install 0.5.6.** All three fixes are confirmed against the real SCM: the service now
starts, stays `Running`, and logs no error events. Each defect only became visible once
the one before it was out of the way.

**It stays a beta, and for one reason that matters more than the rest: a pattern has never
been promoted from real behaviour on Windows.** The whole state machine has been walked
through to an alarm, but only in `dev` mode on synthetic input — which proves the
plumbing, not that this machine can learn a usable pattern from a person. A real enrolment
here cleared every volume gate (1600+ sequences, 400+ active minutes) and still failed the
promotion sanity gate: the model reconstructs synthetic impostors nearly as well as the
real thing, so it is not discriminating. That is the gate doing its job, and it is not a
platform problem — it is the open question about the model.

Two smaller reasons: the install shape the installer sets up (a service) is the one that
cannot capture, and the Wazuh decoding path has never run on a real manager.
`docs/USAGE.md` lists what to check.

Two further Windows-specific limits worth knowing before you rely on it:

- **No per-device identity.** `pynput` is a single global hook and cannot say
  which keyboard produced a keystroke, so the hardware-stack binding described
  above does not apply there — it is inert, not enforced.
- **`behavioral-auth status` fails under a service install.** The daemon running as
  `LocalSystem` creates its run directory owner-only, deliberately — that directory is
  how control commands including `reset` are delivered, so it must not be writable by
  anyone else. The side effect is that an unprivileged CLI cannot read the state file it
  reports from. Running in your own session, as above, avoids this entirely.

WSL2 with passthrough to `/dev/input` is a third option, and is Linux as far as
the daemon is concerned.

---

## Layout

```
src/behavioral_auth/
├── daemon/      state machine, learning controller, alarm logic, control channel
├── collector/   evdev + pynput capture, hardware-stack identity, synthetic source
├── features/    incremental window and sequence extraction
├── models/      Conv1D autoencoder with a bottleneck
├── training/    dataset scoping, fitting, promotion gates, threshold calibration
├── inference/   ONNX scoring, behavioural/face channel rules
├── face/        silent LBPH enrolment, quality gates, calibration
├── reporting/   what was observed
├── siem/        optional forwarding: syslog, Windows Event Log, Wazuh
└── db/          DuckDB access + schema migrations

packaging/
├── wazuh/       decoder + ruleset for a Wazuh manager (install there, not here)
├── windows/     PyInstaller spec, service, Inno Setup installer
└──              AppImage and one-folder Linux bundle
```

## License

See [LICENSE](LICENSE).
