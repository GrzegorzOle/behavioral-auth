"""CLI tool: behavioral-face

Subcommands:
  enroll   – capture face from camera and train/update LBPH model
  verify   – run one verification attempt and print result
  info     – show model status
  delete   – remove trained model
"""

from __future__ import annotations

import argparse
import json
import sys



def _load_cfg():
    from behavioral_auth.config import load_settings
    return load_settings()


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def cmd_enroll(args: argparse.Namespace) -> int:
    cfg = _load_cfg()
    model_path = args.model or cfg.face.model_path
    from behavioral_auth.face.enroll import enroll_face

    ok = enroll_face(
        model_path=model_path,
        camera_index=args.camera,
        n_samples=args.samples,
        show_preview=not args.no_preview,
        update=args.update,
    )
    return 0 if ok else 1


def cmd_verify(args: argparse.Namespace) -> int:
    cfg = _load_cfg()
    model_path = args.model or cfg.face.model_path
    from behavioral_auth.face.verify import opencv_face_score

    camera = args.camera if args.camera is not None else cfg.face.camera_index
    score = opencv_face_score(
        model_path=model_path,
        camera_index=camera,
        confidence_threshold=cfg.face.confidence_threshold,
        success_score=cfg.face.success_score,
        fail_score=cfg.face.fail_score,
        show_preview=args.preview,
    )
    result = {
        "score": round(score, 4),
        "recognised": score <= cfg.face.success_score,
        "threshold": cfg.face.confidence_threshold,
    }
    print(json.dumps(result, indent=2))
    return 0 if result["recognised"] else 1


def cmd_info(args: argparse.Namespace) -> int:
    cfg = _load_cfg()
    model_path = args.model or cfg.face.model_path
    from behavioral_auth.face.recognizer import FaceRecognizer

    rec = FaceRecognizer(model_path)
    info = rec.info()
    info["config"] = {
        "enabled": cfg.face.enabled,
        "backend": cfg.face.backend,
        "camera_index": cfg.face.camera_index,
        "confidence_threshold": cfg.face.confidence_threshold,
        "n_enroll_samples": cfg.face.n_enroll_samples,
    }
    print(json.dumps(info, indent=2))
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    cfg = _load_cfg()
    model_path = args.model or cfg.face.model_path
    from behavioral_auth.face.recognizer import FaceRecognizer

    rec = FaceRecognizer(model_path)
    if not rec.is_trained:
        print("No face model found – nothing to delete.")
        return 0
    if not args.yes:
        ans = input(f"Delete face model at {model_path}? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("Aborted.")
            return 1
    rec.delete()
    print("Face model deleted.")
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="behavioral-face",
        description="OpenCV face enrollment & verification for behavioral-auth",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    # ---- enroll ----
    p_enroll = sub.add_parser("enroll", help="Capture face from camera and train model")
    p_enroll.add_argument("--camera", type=int, default=0, metavar="N",
                          help="Camera device index (default: 0)")
    p_enroll.add_argument("--samples", type=int, default=40, metavar="N",
                          help="Number of face samples to collect (default: 40)")
    p_enroll.add_argument("--update", action="store_true",
                          help="Update existing model instead of retraining from scratch")
    p_enroll.add_argument("--no-preview", action="store_true",
                          help="Disable live camera window (headless mode)")
    p_enroll.add_argument("--model", metavar="PATH", default=None,
                          help="Override model path from config")

    # ---- verify ----
    p_verify = sub.add_parser("verify", help="Run one face verification attempt")
    p_verify.add_argument("--camera", type=int, default=None, metavar="N",
                          help="Camera device index (overrides config)")
    p_verify.add_argument("--preview", action="store_true",
                          help="Show live camera window during verification")
    p_verify.add_argument("--model", metavar="PATH", default=None,
                          help="Override model path from config")

    # ---- info ----
    p_info = sub.add_parser("info", help="Show face model status")
    p_info.add_argument("--model", metavar="PATH", default=None,
                        help="Override model path from config")

    # ---- delete ----
    p_delete = sub.add_parser("delete", help="Remove trained face model")
    p_delete.add_argument("--yes", "-y", action="store_true",
                          help="Skip confirmation prompt")
    p_delete.add_argument("--model", metavar="PATH", default=None,
                          help="Override model path from config")

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_HANDLERS = {
    "enroll": cmd_enroll,
    "verify": cmd_verify,
    "info":   cmd_info,
    "delete": cmd_delete,
}


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(_HANDLERS[args.command](args))

