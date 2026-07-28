# behavioral-auth → Wazuh

A decoder and a ruleset so a Wazuh manager reads the daemon's forwarded events as
fields instead of as opaque text.

Both files go on the **manager**, not on the agent. Decoding is manager-side; the
agent only ships the line.

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

Rule ids live in the user range (≥ 100000). Renumber if 100200–100212 is taken.
