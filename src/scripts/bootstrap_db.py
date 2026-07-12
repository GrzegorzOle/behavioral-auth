#!/usr/bin/env python3
"""Create the database and bring the schema up to date.

Normally unnecessary: the daemon does this itself on first start. Kept for
provisioning a machine ahead of time.
"""

import sys

from behavioral_auth.config import load_settings
from behavioral_auth.db import open_db


def main() -> int:
    cfg = load_settings()
    conn = open_db(cfg)
    conn.close()
    print(f'Schema up to date: {cfg.storage.db_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
