import subprocess
from behavioral_auth.config import load_settings

def howdy_score():
    cfg = load_settings()
    if not cfg.howdy.enabled:
        return 0.5
    try:
        rc = subprocess.run(cfg.howdy.command, shell=True, timeout=cfg.howdy.timeout_sec, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
        return cfg.howdy.success_score if rc == 0 else cfg.howdy.fail_score
    except Exception:
        return cfg.howdy.fail_score
