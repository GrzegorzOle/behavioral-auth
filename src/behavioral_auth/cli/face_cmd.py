"""behavioral-face — inspect and test the face pattern by hand.

The daemon enrols the face on its own, silently, while it learns. These
subcommands exist for checking what it built and for testing the camera; they
are not part of the normal path.
"""

from __future__ import annotations

import argparse
import json
import sys

from behavioral_auth.config import load_settings


def cmd_verify(args: argparse.Namespace) -> int:
    """Run one face check and print what the daemon would have concluded."""
    from behavioral_auth.face.verify import check
    from behavioral_auth.inference.fusion import FaceState

    cfg = load_settings()
    if args.camera is not None:
        cfg.face.camera_index = args.camera

    state, confidence = check(cfg)
    print(json.dumps({
        'state': state.value,
        'confidence': round(confidence, 1) if confidence is not None else None,
        'meaning': {
            FaceState.MATCH: 'rozpoznano wzorcową osobę',
            FaceState.STRANGER: 'to nie jest wzorcowa osoba',
            FaceState.UNKNOWN: 'brak twarzy w kadrze lub brak modelu — to NIE jest dowód niczego',
        }[state],
    }, indent=2, ensure_ascii=False))
    return 0 if state is FaceState.MATCH else 1


def cmd_info(args: argparse.Namespace) -> int:
    from behavioral_auth.face.calibrate import load_face_meta
    from behavioral_auth.face.recognizer import FaceRecognizer

    cfg = load_settings()
    info = FaceRecognizer(cfg.face.model_path).info()
    meta = load_face_meta(cfg)
    info['calibration'] = meta or 'nie skalibrowano'
    info['config'] = {
        'enabled': cfg.face.enabled,
        'backend': cfg.face.backend,
        'camera_index': cfg.face.camera_index,
        'min_samples': cfg.face.min_samples,
    }
    print(json.dumps(info, indent=2, ensure_ascii=False))
    return 0


def main() -> None:
    p = argparse.ArgumentParser(
        prog='behavioral-face',
        description='Podgląd i test wzorca twarzy zbudowanego przez demona.')
    sub = p.add_subparsers(dest='command', required=True)

    v = sub.add_parser('verify', help='jednorazowe sprawdzenie twarzy z kamery')
    v.add_argument('--camera', type=int, default=None, metavar='N')
    v.set_defaults(fn=cmd_verify)

    sub.add_parser('info', help='stan modelu twarzy i jego kalibracja').set_defaults(fn=cmd_info)

    args = p.parse_args()
    sys.exit(args.fn(args))
