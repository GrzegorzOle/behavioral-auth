"""behavioral-verify – real-time behavioral authentication verification.

Starts the collector as a subprocess (auto-wraps with 'sg input' if the
current session lacks the input group), then periodically scores the
accumulated data and shows a live dashboard.

Usage:
    behavioral-verify                  # 60-second session, score every 10s
    behavioral-verify --duration 120
    behavioral-verify --interval 5
    behavioral-verify --no-face
"""

from __future__ import annotations

import argparse
import grp
import json
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path


# ── ANSI colours ─────────────────────────────────────────────────────────────
_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_BOLD   = "\033[1m"
_RESET  = "\033[0m"

def _colour(text: str, score: float, c: float, l: float) -> str:
    if score < c:   return f"{_GREEN}{_BOLD}{text}{_RESET}"
    if score < l:   return f"{_YELLOW}{_BOLD}{text}{_RESET}"
    return f"{_RED}{_BOLD}{text}{_RESET}"

def _bar(value: float, width: int = 30) -> str:
    v = max(0.0, min(value, 1.0))
    return "█" * int(v * width) + "░" * (width - int(v * width))


# ── Input-group helper ────────────────────────────────────────────────────────

def _has_input_group() -> bool:
    """Return True if the current process has the 'input' group active."""
    try:
        gid = grp.getgrnam("input").gr_gid
        return gid in os.getgroups()
    except KeyError:
        return True   # no 'input' group on this OS – assume ok

def _collector_cmd(session_env: dict[str, str]) -> list[str]:
    """Build the collector subprocess command, wrapping with sg if needed."""
    venv_bin = Path(sys.executable).parent
    collector_bin = str(venv_bin / "behavioral-collector")
    env_prefix = " ".join(f"{k}={v}" for k, v in session_env.items())
    inner = f"{env_prefix} {collector_bin}"

    if _has_input_group():
        return ["bash", "-c", inner]
    else:
        return ["sg", "input", "-c", inner]


# ── Scoring ───────────────────────────────────────────────────────────────────

def _score_now(skip_face: bool, verify_session_id: str | None = None) -> dict | None:
    from behavioral_auth.config import load_settings
    from behavioral_auth.features.pipeline import build_feature_windows, build_sequences
    from behavioral_auth.inference.runtime import predict_behavioral, latest_sequence
    from behavioral_auth.inference.decision import normalize_behavioral, get_face_score, _load_model_meta
    from behavioral_auth.inference.fusion import fuse_scores
    import duckdb

    cfg = load_settings()
    meta = _load_model_meta(cfg)
    conn = duckdb.connect(cfg.storage.db_path)

    # Build features only for the current verify session (not all sessions,
    # which would be slow and use stale data)
    if verify_session_id:
        build_feature_windows(conn, verify_session_id, cfg)
        build_sequences(conn, verify_session_id, cfg)

        # Check if verify session has enough sequences yet
        n_seqs = conn.execute(
            "SELECT COUNT(*) FROM fused_sequences WHERE session_id = ?",
            [verify_session_id]
        ).fetchone()[0]

        if n_seqs > 0:
            # Use verify session's latest sequence
            row = latest_sequence(conn, verify_session_id)
            using = "live"
        else:
            # Fall back: use the OLDEST sequence (from original training session)
            # Training sessions have the smallest seq_end_ns timestamps
            row = conn.execute(
                "SELECT seq_end_ns, data_json FROM fused_sequences "
                "ORDER BY seq_end_ns ASC LIMIT 1"
            ).fetchone()
            using = "training-baseline"
    else:
        row = conn.execute(
            "SELECT seq_end_ns, data_json FROM fused_sequences ORDER BY seq_end_ns DESC LIMIT 1"
        ).fetchone()
        using = "any"

    conn.close()
    if not row:
        return None

    # Score the sequence
    import json, numpy as np, onnxruntime as ort
    from pathlib import Path
    scaler = json.loads(Path(cfg.features.scaler_path).read_text())
    mean = np.array(scaler['mean'], dtype=np.float32)
    std  = np.array(scaler['std'],  dtype=np.float32)
    X = np.array(json.loads(row[1]), dtype=np.float32)
    X = (X - mean) / std
    Xn = np.transpose(X[np.newaxis, ...], (0, 2, 1))
    sess = ort.InferenceSession(str(cfg.model.model_path), providers=['CPUExecutionProvider'])
    recon = sess.run(['recon'], {'input': Xn})[0][0]
    target = Xn[0, :, -1]
    beh_raw = float(np.mean((recon - target) ** 2))

    beh_norm = normalize_behavioral(beh_raw, meta)
    face = 0.5 if skip_face else get_face_score(cfg)
    fused = fuse_scores(beh_norm, face)
    c = cfg.fusion.challenge_threshold
    l = cfg.fusion.lock_threshold

    if fused >= l:   decision = "LOCK 🔒"
    elif fused >= c: decision = "CHALLENGE ⚠️"
    else:            decision = "ALLOW ✅"

    return dict(beh_raw=beh_raw, beh_norm=beh_norm, face=face,
                fused=fused, decision=decision, c=c, l=l, using=using,
                model_lock_ref=meta.get("lock_threshold", "?"),
                val_mean=meta.get("val_mean_error", "?"))


# ── Dashboard output ──────────────────────────────────────────────────────────

def _header(duration: int, interval: int, skip_face: bool) -> None:
    print(f"\n{_BOLD}╔══════════════════════════════════════════════════╗")
    print(f"║       behavioral-auth  •  weryfikacja na żywo        ║")
    print(f"╚══════════════════════════════════════════════════╝{_RESET}")
    print(f"  Czas sesji : {duration}s   Interwał : {interval}s   "
          f"Twarz : {'pominięta' if skip_face else 'aktywna'}")
    print(f"  {_GREEN}■ ALLOW{_RESET} < {_YELLOW}■ CHALLENGE{_RESET} < {_RED}■ LOCK{_RESET}\n")

def _print_score(r: dict, elapsed: float, event_count: int | str) -> None:
    now = datetime.now().strftime("%H:%M:%S")
    dec_str = _colour(r["decision"], r["fused"], r["c"], r["l"])
    using_tag = f"[{r['using']}]" if r.get("using") != "live" else ""
    print(f"  [{now}]  t={elapsed:5.0f}s  events={event_count}  {using_tag}")
    print(f"    beh_raw={r['beh_raw']:.3f}  beh_norm={r['beh_norm']:.3f}  "
          f"face={r['face']:.2f}  fused={r['fused']:.3f}")
    print(f"    [{_bar(r['fused'])}]  {r['fused']:.3f}  →  {dec_str}")
    val_mean = r.get('val_mean', '?')
    val_str = f"{val_mean:.3f}" if isinstance(val_mean, float) else str(val_mean)
    thr_line = (f"    thresholds: challenge={r['c']}  lock={r['l']}  "
                f"(model ref: {r['model_lock_ref']:.3f}  val_mean={val_str})")
    print(thr_line + "\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="behavioral-verify",
        description="Weryfikacja behawioralna na żywo – zbieranie + ocena razem",
    )
    parser.add_argument("--duration", type=int, default=60, metavar="SEC",
                        help="Długość sesji w sekundach (domyślnie 60)")
    parser.add_argument("--interval", type=int, default=10, metavar="SEC",
                        help="Co ile sekund oceniać (domyślnie 10)")
    parser.add_argument("--no-face", action="store_true",
                        help="Pomijaj weryfikację twarzy (szybsze)")
    args = parser.parse_args()

    from behavioral_auth.config import load_settings
    import duckdb

    cfg = load_settings()
    session_id = str(uuid.uuid4())

    # Register session
    conn = duckdb.connect(cfg.storage.db_path)
    conn.execute(
        "INSERT INTO sessions (session_id, user_name, host_name, mode, metadata) VALUES (?, ?, ?, ?, ?)",
        [session_id, os.getenv("USER", "unknown"), socket.gethostname(), cfg.general.mode, "{}"]
    )
    conn.close()

    _header(args.duration, args.interval, args.no_face)

    # ── Start collector subprocess ────────────────────────────────────────────
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    env["CUDA_VISIBLE_DEVICES"] = ""
    env["BEHAVIORAL_SESSION_ID"] = session_id
    project_root = str(Path(__file__).resolve().parents[3])

    def start_collector() -> subprocess.Popen:
        cmd = _collector_cmd({"PYTHONPATH": "src", "CUDA_VISIBLE_DEVICES": "",
                              "BEHAVIORAL_SESSION_ID": session_id})
        return subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=env,
            cwd=project_root,
        )

    def stop_collector(proc: subprocess.Popen) -> None:
        """SIGTERM → Writer.close() → flush → DB unlocked."""
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            proc.kill()

    col_proc = start_collector()
    time.sleep(1.5)   # startup delay

    if col_proc.poll() is not None:
        err = col_proc.stderr.read().decode(errors="replace")
        print(f"❌  Collector nie uruchomił się:\n{err}")
        print("Spróbuj: sg input -c 'behavioral-verify'")
        sys.exit(1)

    print(f"  Sesja {session_id[:8]}…  Zacznij normalnie pisać / poruszać myszą…\n")

    # ── Scoring loop ─────────────────────────────────────────────────────────
    t_start = time.time()
    t_next  = t_start + args.interval
    results : list[dict] = []

    try:
        while True:
            elapsed = time.time() - t_start
            if elapsed >= args.duration:
                break

            if time.time() >= t_next:
                # Stop collector → flush DB → score → restart collector
                stop_collector(col_proc)
                time.sleep(0.2)   # ensure file lock is released

                try:
                    import duckdb as _db
                    tmp = _db.connect(cfg.storage.db_path)
                    n_ev = tmp.execute(
                        "SELECT COUNT(*) FROM raw_events WHERE session_id = ?",
                        [session_id]
                    ).fetchone()[0]
                    tmp.close()
                except Exception:
                    n_ev = "?"

                r = _score_now(skip_face=args.no_face, verify_session_id=session_id)

                if r:
                    _print_score(r, elapsed, n_ev)
                    results.append(r)
                else:
                    print(f"  [{datetime.now().strftime('%H:%M:%S')}]  "
                          f"Za mało danych ({n_ev} zdarzeń tej sesji) – dalej pisz…\n")

                t_next += args.interval

                # Restart collector for next cycle
                col_proc = start_collector()
                time.sleep(0.5)

            time.sleep(0.4)

    except KeyboardInterrupt:
        print("\n  Przerwano (Ctrl+C)")
    finally:
        stop_collector(col_proc)

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'─'*54}")
    if results:
        fv = [r["fused"] for r in results]
        print(f"  Podsumowanie sesji ({len(results)} pomiarów)")
        print(f"  Fused score: min={min(fv):.3f}  max={max(fv):.3f}  "
              f"śr={sum(fv)/len(fv):.3f}")
        for dec, cnt in sorted(Counter(r["decision"].split()[0] for r in results).items()):
            icon = "✅" if dec == "ALLOW" else ("⚠️" if dec == "CHALLENGE" else "🔒")
            print(f"  {icon}  {dec}: {cnt}×")
    else:
        print("  Brak danych – zbierz min. 2–3 minuty aktywności:")
        print("    sg input -c 'behavioral-collector'  (w osobnym terminalu)")
    print()

