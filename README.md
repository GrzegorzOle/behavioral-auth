# behavioral-auth

A local-only daemon that learns how **you** type and move the mouse, freezes that
pattern, and then warns you when the person at the keyboard stops matching it.

It **never locks the session and never logs anyone out.** The strongest thing it
does is write a warning to the log, the console, and (optionally) a desktop
notification. That is a deliberate design constraint, not an unfinished feature.

Everything runs on your machine: DuckDB on disk, ONNX on the CPU, no network.

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
```

The pattern only ever changes when **you** say so — `behavioral-auth reset`
(someone else is going to use this machine) or `behavioral-auth learn-more`
(refine what you have). There is no automatic adaptation anywhere in the code.

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

## Install

Requires Python 3.11+, and membership of the `input` group (to read the keyboard)
and `video` group (for the camera).

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
clock (refused in `prod` mode):

```bash
make demo        # behavioral-authd --synthetic-input user --synthetic-speed 40
```

Watch it learn, converge, promote, and switch to MONITORING. Then, in another
terminal, put a different person at the keyboard:

```bash
python -c "from behavioral_auth.daemon import control; \
           control.send('/var/lib/behavioral-auth/run','set-profile',{'profile':'impostor'})"
```

The deviation climbs past the threshold, and once it *stays* there, ALARM.

---

## Commands

| Command | What it does |
|---|---|
| `behavioral-authd` | The daemon. Learns, then watches. |
| `behavioral-auth status` | Current state and progress. Works while the daemon runs. |
| `behavioral-auth reset` | **Somebody else will use this machine.** Destroys the pattern and all face crops, starts learning from zero. |
| `behavioral-auth learn-more` | Refine the existing pattern with more data. Explicit, never automatic. |
| `behavioral-auth pause` / `resume` | Stop/start scoring (collection continues). |
| `behavioral-report` | Learning cycles, scores, alarms. No FAR/FRR — see above. |
| `behavioral-face info` / `verify` | Inspect and test the face pattern the daemon built. |

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

**Linux only.** Collection is built on `evdev`, and without collection there is
nothing for the rest of the system to do — the face model can only be enrolled by
the daemon while it learns, so a face-only Windows install would have no pattern
to verify against. The previous partial Windows support was removed rather than
left in place to mislead. Use Linux, or WSL2 with passthrough to `/dev/input`.

---

## Layout

```
src/behavioral_auth/
├── daemon/      state machine, learning controller, alarm logic, control channel
├── collector/   evdev capture + a synthetic source for testing
├── features/    incremental window and sequence extraction
├── models/      Conv1D autoencoder with a bottleneck
├── training/    dataset scoping, fitting, promotion gates, threshold calibration
├── inference/   ONNX scoring, behavioural/face channel rules
├── face/        silent LBPH enrolment, quality gates, calibration
├── reporting/   what was observed
└── db/          DuckDB access + schema migrations
```

## License

See [LICENSE](LICENSE).
