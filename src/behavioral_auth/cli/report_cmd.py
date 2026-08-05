"""behavioral-report — print what the pattern learned and what it decided.

--config goes through BEHAVIORAL_AUTH_CONFIG rather than a load_settings(path)
argument, which is the route behavioral-auth already takes, so both CLIs resolve
their configuration by exactly one mechanism.

The argument parser earns its place twice over: before it, this command took no
arguments at all and never read sys.argv, so `behavioral-report --config <path>`
was silently ignored and reported the *default* database instead — a report of
the wrong machine's pattern, with nothing on screen to say so.
"""

from __future__ import annotations

import argparse
import os

from behavioral_auth import __version__
from behavioral_auth.reporting.metrics import report


def main() -> None:
    p = argparse.ArgumentParser(
        prog='behavioral-report',
        description='Raport: czego nauczył się wzorzec i co ocenił.')
    p.add_argument('--version', action='version',
                   version=f'behavioral-report {__version__}')
    p.add_argument('--config', metavar='PATH',
                   help='plik konfiguracyjny (domyślnie: BEHAVIORAL_AUTH_CONFIG, '
                        '/etc/behavioral-auth/config.yaml, config/config.yaml)')
    args = p.parse_args()

    if args.config:
        if not os.path.exists(args.config):
            raise SystemExit(f'--config: nie ma takiego pliku: {args.config}')
        os.environ['BEHAVIORAL_AUTH_CONFIG'] = args.config

    report()


if __name__ == '__main__':
    main()
