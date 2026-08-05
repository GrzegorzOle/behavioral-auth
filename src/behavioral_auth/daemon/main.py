"""behavioral-authd — the continuous behavioural authentication daemon."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from loguru import logger

from behavioral_auth import __version__
from behavioral_auth.config import load_settings
from behavioral_auth.daemon.daemon import Daemon


def setup_logging(cfg, console) -> None:
    logger.remove()

    if console.enabled:
        # Route log lines through the console so they print *above* the status
        # block instead of being eaten by its redraw.
        logger.add(console.emit_log, level=cfg.general.log_level,
                   format='<green>{time:HH:mm:ss}</green> <level>{level: <7}</level> {message}',
                   colorize=True)
    elif sys.stderr is not None:
        logger.add(sys.stderr, level=cfg.general.log_level,
                   format='{time:HH:mm:ss} {level: <7} {message}', colorize=False)
    # sys.stderr is None in a frozen Windows service: there is no console to
    # attach to, and loguru rejects a None sink outright. Adding it
    # unconditionally raised TypeError inside setup_logging, which killed the
    # service before it did anything else — and only under the SCM, since
    # `debug` runs in a console where stderr exists. The file sink below is
    # what a service logs through; if general.log_file is also empty the
    # daemon runs with no logging at all, which the packaged config avoids by
    # always setting it.

    if cfg.general.log_file:
        os.makedirs(os.path.dirname(cfg.general.log_file), exist_ok=True)
        logger.add(cfg.general.log_file, level=cfg.general.log_level,
                   rotation='10 MB', retention='14 days', enqueue=True)


def main() -> None:
    p = argparse.ArgumentParser(
        prog='behavioral-authd',
        description='Learns how you type and move, then watches for someone else. '
                    'Never locks the session — it only warns.',
    )
    p.add_argument('--version', action='version',
                   version=f'behavioral-authd {__version__}')
    p.add_argument('--config', metavar='PATH', help='config file (default: search)')
    p.add_argument('--mode', choices=['dev', 'prod'],
                   help='override general.mode. dev merges config.dev.yaml, which '
                        'shrinks every promotion gate — a pattern promoted that way '
                        'is a smoke test, not something to rely on')
    p.add_argument('--console', dest='console', action='store_const', const='always',
                   help='force the live status block on')
    p.add_argument('--no-console', dest='console', action='store_const', const='never',
                   help='log only, no status block')
    p.add_argument('--synthetic-input', choices=['user', 'impostor'], metavar='PROFILE',
                   help='inject synthetic behaviour instead of reading real devices '
                        '(testing only; refused in prod mode)')
    p.add_argument('--synthetic-speed', type=float, default=20.0, metavar='X',
                   help='simulated-time speed-up for --synthetic-input (default: 20)')
    args = p.parse_args()

    if args.config:
        os.environ['BEHAVIORAL_AUTH_CONFIG'] = args.config
    cfg = load_settings(mode=args.mode)
    if args.console:
        cfg.daemon.console = args.console

    daemon = Daemon(cfg, synthetic=args.synthetic_input,
                    synthetic_speed=args.synthetic_speed)
    setup_logging(cfg, daemon.console)
    # First line in the log, before anything can fail. Half of this project's
    # diagnosis has been reading a log and asking which build produced it —
    # previously answerable only by matching line numbers in tracebacks.
    logger.info(f'behavioral-auth {__version__} starting ({sys.platform}, '
                f'mode={cfg.general.mode})')

    try:
        asyncio.run(daemon.run())
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
