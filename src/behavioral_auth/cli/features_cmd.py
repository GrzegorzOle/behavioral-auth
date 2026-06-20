"""behavioral-features  –  extract feature windows and sequences from raw events."""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="behavioral-features",
        description="Extract feature windows and fused sequences from raw events",
    )
    parser.add_argument(
        "--session", metavar="UUID", default=None,
        help="Process only a specific session (default: all sessions)",
    )
    args = parser.parse_args()

    from behavioral_auth.features.pipeline import run_pipeline
    run_pipeline(session_filter=args.session)
