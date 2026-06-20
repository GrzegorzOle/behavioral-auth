"""behavioral-infer  –  run one inference cycle or loop forever."""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="behavioral-infer",
        description="Run behavioral anomaly detection",
    )
    parser.add_argument(
        "--loop", action="store_true",
        help="Run continuously (interval set in config inference.interval_sec)",
    )
    parser.add_argument(
        "--interval", type=float, default=None, metavar="SEC",
        help="Override inference interval in seconds (implies --loop)",
    )
    args = parser.parse_args()

    from behavioral_auth.inference.decision import run_once, loop_forever
    from behavioral_auth.config import load_settings

    if args.interval is not None:
        import time
        interval = args.interval
        print(f"Running inference loop every {interval}s  (Ctrl-C to stop)")
        try:
            while True:
                run_once()
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\nStopped.")
    elif args.loop:
        cfg = load_settings()
        print(f"Running inference loop every {cfg.inference.interval_sec}s  (Ctrl-C to stop)")
        try:
            loop_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
    else:
        run_once()
