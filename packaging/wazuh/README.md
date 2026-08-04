# behavioral-auth → Wazuh

A decoder and a ruleset so a Wazuh manager reads the daemon's forwarded events as
fields instead of as opaque text.

Both files go on the **manager**, not on the agent. Decoding is manager-side; the
agent only ships the line.

**There are two pairs, one per transport, and they are not interchangeable:**

| files | transport | agent side |
|---|---|---|
| `0910-*` | Linux: RFC 5424 → `/dev/log` → journald | journald collector |
| `0911-*` | Windows: Event Log id 1000 → eventchannel | Application channel collector |

Nothing about the syslog framing survives on Windows — there is no frame at all —
so a single decoder cannot serve both. Rule ids are separate blocks
(100200–100217 and 100230–100247) but **the levels are deliberately identical**,
so the same incident reads the same whichever machine raised it.

## Why this is needed at all

The daemon frames its events as **RFC 5424** with a JSON body, deliberately: a
decoder can read the fields without parsing prose. journald's syslog parser, on
the other hand, is **RFC 3164**. It accepts our frame and decodes the priority
correctly, but it never lifts our APP-NAME into `SYSLOG_IDENTIFIER`.

Two things follow, and they are why the decoder looks the way it does:

- `journalctl -t behavioral-auth` finds nothing even when forwarding works. Use
  `journalctl -f SYSLOG_FACILITY=10` instead.
- The program name the manager sees comes from the process's `_COMM`, which is
  `python` from a source checkout and `behavioral-auth` from the installed
  bundle. **A decoder must not key on it.** This one keys on the RFC 5424 MSGID
  (`<category>.<action>`) followed by the NIL structured-data field, which is
  stable in every event the daemon emits.

## Install

```bash
sudo cp 0910-behavioral-auth_decoders.xml /var/ossec/etc/decoders/
sudo cp 0910-behavioral-auth_rules.xml    /var/ossec/etc/rules/
sudo chown root:wazuh /var/ossec/etc/decoders/0910-behavioral-auth_decoders.xml \
                      /var/ossec/etc/rules/0910-behavioral-auth_rules.xml
sudo chmod 660 /var/ossec/etc/decoders/0910-behavioral-auth_decoders.xml \
               /var/ossec/etc/rules/0910-behavioral-auth_rules.xml
sudo systemctl restart wazuh-manager
```

Nothing changes on the agent. If the agent already has
`<log_format>journald</log_format>` with `<location>journald</location>` — the
Fedora default — the events are already reaching the manager and were simply
matching no rule.

## Validate before trusting it

`wazuh-logtest` on the manager is the whole verification. Paste a sample line and
read what it says:

```bash
sudo /var/ossec/bin/wazuh-logtest
```

A real `alarm.raised`, in the shape the manager receives it:

```
Jul 28 19:23:14 gofedora behavioral-auth[12345]: 1 2026-07-28T17:23:14.216827+00:00 gofedora behavioral-auth - alarm.raised - {"action":"raised","category":"alarm","detail":{"alarm_id":"b64b9fe9-8bab-44a2-90b8-02f7c3e25478","face_state":"unknown","ratio":4.274,"reason":"behavioral","span_sec":152.0,"summary":"behaviour deviates from the learned pattern for 152s (ratio up to 4.27x threshold)"},"enrollment_id":"971e8f8e-d611-4fd5-9c49-fb481c646b79","host":"gofedora","session_id":"0a1d34af-7243-428f-ae54-c475512569a2","severity":1,"ts":"2026-07-28T17:23:14.216827+00:00"}
```

You want three things in the output:

1. `decoder: 'behavioral-auth'` — the prematch anchored.
2. Fields split out, including the nested ones: `category`, `action`,
   `detail.ratio`, `detail.reason`, `detail.span_sec`.
3. `Rule: 100201 (level 12)`.

If the decoder matches but the fields are missing, the prematch is not ending
exactly where the JSON begins — check for a stray character between the second
` - ` and the `{`.

If nothing matches at all, get the **real** line rather than guessing at the
wrapper: turn on `<logall_json>yes</logall_json>` in the manager's `ossec.conf`
briefly, restart, let one event through, and read it from
`/var/ossec/logs/archives/archives.json`. The wrapper that the journald collector
puts in front differs between agent versions; everything this decoder keys on
comes after it, but the sample above is only as accurate as one collector.

Sample lines for the other events, if you want to check the rest of the ruleset,
differ only in the MSGID and the JSON — e.g. ` - ops.pattern_reset - {"action":
"pattern_reset","category":"ops",...}` should land on rule 100204.

## What the levels mean

The daemon never locks a session; it only warns. So no level here means "the box
defended itself" — they say how much a human should care.

| Level | Rules | What |
|---|---|---|
| 12 | 100201, 100202 | someone other than the enrolled person appears to be at the keyboard |
| 12 | 100204 | the enrolled pattern was destroyed — the system no longer knows who the owner is |
| 7 | 100206, 100208 | monitoring is knowingly blind (paused, or the daemon stopped) |
| 5 | 100205 | the pattern is being deliberately retrained |
| 3 | the rest | routine lifecycle |

`pattern_reset` sits at 12 next to the alarms on purpose. It is how someone makes
the system forget who the owner is, and it is the first thing an attacker who
understands the tool would do. Whether the owner asked for it is a question for
the analyst, not for the rule.

Rules 100201 and 100202 are kept apart because the daemon keeps the face channel
out of the behavioural verdict. Folding a camera verdict into a behavioural one
was a real bug in this project once; do not merge them here either.

Rule ids live in the user range (≥ 100000). Renumber if 100200–100217 (syslog)
or 100230–100247 (Event Log) is taken — and renumber both together, so the two
transports stay easy to read side by side.

## Windows: the eventchannel path (`0911-*`)

```bash
sudo cp 0911-behavioral-auth-windows_decoders.xml /var/ossec/etc/decoders/
sudo cp 0911-behavioral-auth-windows_rules.xml    /var/ossec/etc/rules/
sudo systemctl restart wazuh-manager
```

**Nothing to install or change on the agent.** The stock Windows agent already
collects the Application channel, and `EventLogSink` writes there. If the
collector is present, the events are *already* arriving at the manager and being
silently dropped for want of a rule — the same failure mode as journald on
Linux, and the reason to confirm arrival before touching anything.

**Do not narrow that collector** to `Provider[@Name='behavioral-auth']` to spare
the manager volume. On a corporate box it is somebody else's agent doing
somebody else's detection on a shared manager, and narrowing a channel it
already collects would silently blind whatever else depends on it. If volume
ever becomes a real problem, that is a manager-side conversation.

### What the event looks like

Captured from the Application log on real hardware:

```xml
<Provider Name='behavioral-auth'/>
<EventID Qualifiers='0'>1000</EventID>
<Channel>Application</Channel>
<EventData>
  <Data>alarm.raised</Data>
  <Data>{"action":"raised","category":"alarm","detail":{...},"severity":1,...}</Data>
</EventData>
```

Two **unnamed** `<Data>` elements: a readable `category.action`, then the JSON
body — the same payload the syslog sinks send, carrying verdicts and numbers
only. Because they are unnamed, Wazuh has no meaningful field names to give
them, and our JSON ends up nested inside the agent's own JSON envelope. That is
why `0911-*` extracts fields with regexes instead of handing the body to
`JSON_Decoder` the way `0910-*` does: a nested, possibly escaped body is not
something `JSON_Decoder` can be pointed at.

Every regex tolerates an optional backslash before each quote, so it matches
whether or not the manager delivers the payload escaped. Both forms were tested
against three real captured payloads (an alarm, a promotion, and the transition
into ALARM); all ten fields extract in both.

### Confirming the field path — do this first

**This is the one thing that cannot be settled without a manager**, and it is
worth one minute before trusting any of it. Read the field names off a real
event rather than from this file:

1. Provoke an event on the Windows box. The cheapest is a lifecycle one — start
   the daemon with `siem.enabled: true` and `sink: eventlog`. For an alarm, use a
   scratch config with `--mode dev --synthetic-input user` and then
   `behavioral-auth set-profile impostor`; `--synthetic-input` **replaces** the
   input hook, so it captures nothing real and can run beside a live daemon.
2. Confirm it reached the log, remembering that Event Viewer renders the
   description blank (no message resource is registered for the source):

   ```powershell
   Get-WinEvent -LogName Application -FilterXPath "*[System[Provider[@Name='behavioral-auth']]]" -MaxEvents 1 |
     ForEach-Object { $_.ToXml() }
   ```

   Use `.ToXml()` or `$_.Properties`, **never** `$_.Message`.
3. On the manager, confirm arrival before writing rules — turn on `logall_json`
   or read `archives.json`, and look for an event from the Windows agent.
4. Paste that raw line into `/var/ossec/bin/wazuh-logtest` and read which field
   holds the body. If it is not `win.eventdata.data`, the prematch in
   `0911-*_decoders.xml` still works (it keys on `providerName`, which is
   structural), but say so in a comment there so the next person is not misled.

### Status

**`0911-*` has never met a real manager.** Its XML is well-formed, its prematch
and all ten field regexes were verified against real captured payloads in both
escaped and unescaped form, and every event shape the code can emit hits a rule.
That is where the evidence stops — exactly the same position `0910-*` is in.
`wazuh-logtest` against a captured line is the check that closes it.

Agent names on this network, since every manager-side query needs one:
`GRZEGORZ-STN` is the Windows box, `grzegorz-legion` the Linux one, and the
manager is `192.168.88.4`. The agent name alone tells you which decoder should
have handled an event.

### Handing off the last step

Everything up to the manager is confirmed on real hardware: the daemon writes event 1000,
the Windows agent's Application collector picks it up (`wazuh-logcollector.state` shows
83 876 events and **0 drops**), and the manager is reachable. What has never happened is
the manager *decoding* it.

Whoever has manager access can close this in about a minute. Two routes:

**Over SSH:**

```bash
# 1. Get a REAL line rather than a constructed one -- the agent's envelope is
#    the whole question here.
sudo sed -i 's|<logall_json>no</logall_json>|<logall_json>yes</logall_json>|' /var/ossec/etc/ossec.conf
sudo systemctl restart wazuh-manager
#    ... provoke or wait for one behavioral-auth event on the Windows box ...
sudo grep -m1 behavioral-auth /var/ossec/logs/archives/archives.json

# 2. Feed exactly that line to logtest.
sudo /var/ossec/bin/wazuh-logtest

# 3. Put logall_json back -- it is expensive on a busy manager.
```

**Over the API** (port 55000 is open from the Windows box):

```bash
TOKEN=$(curl -sk -u '<user>:<pass>' -X POST \
  'https://192.168.88.4:55000/security/user/authenticate?raw=true')
curl -sk -X PUT 'https://192.168.88.4:55000/logtest' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"event":"<the raw line>","log_format":"syslog","location":"eventchannel"}'
```

What to read from the output, in order:

1. **Which field holds our JSON body.** This is the only thing that cannot be settled off
   the manager. If it is not `win.eventdata.data`, nothing in the decoder actually breaks
   — the prematch keys on `providerName`, which is structural — but correct the comment in
   `0911-*_decoders.xml` so the next person is not misled.
2. `decoder: 'behavioral-auth-win'`.
3. The `ba_*` fields split out: `ba_category`, `ba_action`, and for an alarm `ba_reason`,
   `ba_ratio`, `ba_span_sec`.
4. `Rule: 100231 (level 12)` for a behavioural alarm.

**Do not install these files on a shared manager without asking.** They are additive — new
files, an unused id range, a prematch narrow enough that it was tested not to fire on
another provider's Application events — but it is still somebody else's production
detection, and the `Security` channel alone carries 1.5 GB through it.
