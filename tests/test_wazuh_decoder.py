"""The Wazuh decoders are shipped configuration, so they are tested like code.

Neither decoder has ever run against a real manager, which makes it all the more
important that the part we *can* check here stays checked: that the XML parses,
that the prematch anchors on a real event, and that every field the ruleset reads
is actually extracted from a real payload.

The payloads below are verbatim captures — the Linux one from a live syslog
frame, the Windows ones from the Application log on GRZEGORZ-STN — not invented
examples. An invented payload would let the decoder and the test drift together.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

WAZUH = Path(__file__).resolve().parents[1] / 'packaging' / 'wazuh'

# One alarm, one promotion, one transition into ALARM. Between them they cover
# every field the Windows ruleset matches on.
ALARM = ('{"action":"raised","category":"alarm","detail":{"alarm_id":"679ff609-55b8",'
         '"face_state":"unknown","ratio":6.555,"reason":"behavioral","span_sec":60.0,'
         '"summary":"behaviour deviates from the learned pattern for 60s"},'
         '"enrollment_id":"53169787","host":"GRZEGORZ-STN","session_id":"905658d2",'
         '"severity":1,"ts":"2026-07-31T09:34:10.720693+00:00"}')

TO_ALARM = ('{"action":"transition","category":"state","detail":{"from_state":"MONITORING",'
            '"reason":"sustained anomaly (behavioral)","to_state":"ALARM"},'
            '"enrollment_id":"53169787","host":"GRZEGORZ-STN","session_id":"905658d2",'
            '"severity":1,"ts":"2026-07-31T09:34:10.735198+00:00"}')

PROMOTION = ('{"action":"transition","category":"state","detail":{"from_state":"LEARNING",'
             '"reason":"pattern converged and passed the sanity gate","to_state":"MONITORING"},'
             '"enrollment_id":"53169787","host":"GRZEGORZ-STN","session_id":"905658d2",'
             '"severity":5,"ts":"2026-07-31T09:29:15.331161+00:00"}')

# How the agent wraps a Windows event. Only the provider name and the payload
# matter to us; the rest is Wazuh's own envelope.
ENVELOPE = ('{"win":{"system":{"providerName":"behavioral-auth","eventID":"1000",'
            '"channel":"Application","computer":"GRZEGORZ-STN"},'
            '"eventdata":{"data":"%s"}}}')


def _load(name: str):
    """Parse a Wazuh XML fragment, which has several roots rather than one."""
    src = (WAZUH / name).read_text(encoding='utf-8')
    return ET.fromstring('<root>' + src + '</root>')


def _win_decoder():
    root = _load('0911-behavioral-auth-windows_decoders.xml')
    prematch, fields = None, {}
    for d in root.findall('decoder'):
        pm = d.find('prematch')
        if pm is not None:
            prematch = pm.text
        rx, order = d.find('regex'), d.find('order')
        if rx is not None and order is not None:
            fields[order.text] = rx.text
    return prematch, fields


@pytest.mark.parametrize('name', [
    '0910-behavioral-auth_decoders.xml',
    '0910-behavioral-auth_rules.xml',
    '0911-behavioral-auth-windows_decoders.xml',
    '0911-behavioral-auth-windows_rules.xml',
])
def test_shipped_xml_is_well_formed(name):
    _load(name)


def test_windows_prematch_anchors_on_the_provider_name():
    """It must key on something structural, not on the payload.

    The provider name lives in the agent's envelope and survives whatever the
    manager does to the body — which is the one thing that cannot be confirmed
    without a manager.
    """
    prematch, _ = _win_decoder()
    assert re.search(prematch, ENVELOPE % ALARM)
    # And it must not fire on somebody else's Application-log event.
    other = ENVELOPE.replace('behavioral-auth', 'Microsoft-Windows-Security-Auditing')
    assert not re.search(prematch, other % ALARM)


@pytest.mark.parametrize('payload,expected', [
    (ALARM, {'ba_category': 'alarm', 'ba_action': 'raised', 'ba_reason': 'behavioral',
             'ba_ratio': '6.555', 'ba_span_sec': '60.0', 'ba_severity': '1'}),
    (TO_ALARM, {'ba_category': 'state', 'ba_action': 'transition',
                'ba_from_state': 'MONITORING', 'ba_to_state': 'ALARM',
                'ba_reason': 'sustained anomaly (behavioral)'}),
    (PROMOTION, {'ba_category': 'state', 'ba_action': 'transition',
                 'ba_from_state': 'LEARNING', 'ba_to_state': 'MONITORING',
                 'ba_severity': '5'}),
])
@pytest.mark.parametrize('escaped', [False, True], ids=['plain', 'escaped'])
def test_windows_fields_extract_from_real_payloads(payload, expected, escaped):
    """Every field the ruleset matches on must come out of a real event.

    Run twice: as-is and backslash-escaped. Whether the manager hands the nested
    body over escaped is exactly what cannot be settled from here, so the
    regexes are written to tolerate both and this pins that they do.
    """
    body = payload.replace('"', '\\"') if escaped else payload
    line = ENVELOPE % body
    _, fields = _win_decoder()
    for field, want in expected.items():
        m = re.search(fields[field], line)
        assert m, f'{field} did not match ({"escaped" if escaped else "plain"})'
        assert m.group(1) == want


def test_the_two_transports_agree_on_levels():
    """Same event, same severity, whichever OS raised it.

    If these drift apart an analyst comparing two machines is comparing the
    tooling instead of the behaviour. Matched by the action each rule keys on,
    since the ids and field names differ by design.
    """
    def levels(rules_file, prefix):
        """action -> every level assigned to it, sorted.

        Collecting all of them rather than the last one matters: several rules
        share an action and differ only in a further field, so a last-wins map
        would compare 100214 with 100244 and never look at 100213 or 100243.
        """
        root = _load(rules_file)
        out: dict[str, list[str]] = {}
        for r in root.iter('rule'):
            for f in r.findall('field'):
                if f.get('name') == f'{prefix}action':
                    out.setdefault(f.text, []).append(r.get('level'))
        return {k: sorted(v) for k, v in out.items()}

    syslog = levels('0910-behavioral-auth_rules.xml', '')
    evtlog = levels('0911-behavioral-auth-windows_rules.xml', 'ba_')

    shared = set(syslog) & set(evtlog)
    assert len(shared) >= 11, 'the two rulesets stopped covering the same events'
    mismatched = {k: (syslog[k], evtlog[k]) for k in shared if syslog[k] != evtlog[k]}
    assert not mismatched, f'levels drifted between transports: {mismatched}'

    # And neither transport should quietly stop covering an event the other has.
    assert not set(syslog) ^ set(evtlog), (
        f'actions covered by only one transport: {set(syslog) ^ set(evtlog)}')
