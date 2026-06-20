"""Howdy face-recognition backend (Linux only).

Invokes the howdy CLI tool and maps its return code to an anomaly score:
  rc == 0  → face recognised → success_score (low, ~0.05)
  rc != 0  → not recognised  → fail_score    (high, ~0.85)
"""

import subprocess
from behavioral_auth.config import load_settings


def howdy_score() -> float:
    """Run howdy and return a float anomaly score.

    Returns 0.5 (neutral) when howdy is disabled in config.
    Returns fail_score on timeout or any subprocess error.
    """
    cfg = load_settings()
    if not cfg.howdy.enabled:
        return 0.5
    try:
        rc = subprocess.run(
            cfg.howdy.command, shell=True,
            timeout=cfg.howdy.timeout_sec,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        return cfg.howdy.success_score if rc == 0 else cfg.howdy.fail_score
    except Exception:
        return cfg.howdy.fail_score
