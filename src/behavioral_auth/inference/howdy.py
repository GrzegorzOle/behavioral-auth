"""howdy face-verification backend (Linux, IR camera).

Shells out to the howdy CLI and maps its exit code onto a FaceState. A
non-zero exit means howdy did not recognise the face; a timeout or a missing
binary means we learned nothing, which is UNKNOWN — not evidence of an
intruder.
"""

from __future__ import annotations

import subprocess

from loguru import logger

from behavioral_auth.config import Settings
from behavioral_auth.inference.fusion import FaceState


def howdy_state(cfg: Settings) -> FaceState:
    try:
        rc = subprocess.run(
            cfg.face.howdy_command, shell=True,
            timeout=cfg.face.howdy_timeout_sec,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).returncode
    except subprocess.TimeoutExpired:
        logger.debug('howdy timed out')
        return FaceState.UNKNOWN
    except Exception as exc:
        logger.warning(f'howdy failed: {exc}')
        return FaceState.UNKNOWN
    return FaceState.MATCH if rc == 0 else FaceState.STRANGER
