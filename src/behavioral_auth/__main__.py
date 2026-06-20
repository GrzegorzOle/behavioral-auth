"""Entry point for ``python -m behavioral_auth <command>``.

Available commands mirror the installed CLI entry points:
  collector  – capture keyboard/mouse events
  features   – extract feature windows and sequences
  train      – train the ONNX autoencoder
  infer      – run one inference cycle
  report     – print decision metrics
  face       – face enrollment / verification (OpenCV LBPH)
  status     – pipeline status dashboard
  verify     – live behavioural verification session
"""

import sys
from behavioral_auth.cli.collector_cmd import main as collector_main
from behavioral_auth.cli.features_cmd import main as features_main
from behavioral_auth.cli.train_cmd import main as train_main
from behavioral_auth.cli.infer_cmd import main as infer_main
from behavioral_auth.cli.report_cmd import main as report_main
from behavioral_auth.cli.face_cmd import main as face_main
from behavioral_auth.cli.status_cmd import main as status_main
from behavioral_auth.cli.verify_cmd import main as verify_main

COMMANDS = {
    'collector': collector_main,
    'features':  features_main,
    'train':     train_main,
    'infer':     infer_main,
    'report':    report_main,
    'face':      face_main,
    'status':    status_main,
    'verify':    verify_main,
}

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'status'
    if cmd in COMMANDS:
        COMMANDS[cmd]()
    else:
        print('usage: python -m behavioral_auth '
              '[collector|features|train|infer|report|face|status|verify]')
