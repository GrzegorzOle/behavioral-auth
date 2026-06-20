-- Migration 002: face recognition support
-- Stores face model metadata and optional per-verification snapshots.

-- Tracks face model versions (when it was trained, how many samples etc.)
CREATE TABLE IF NOT EXISTS face_models (
    model_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    model_path   VARCHAR     NOT NULL,
    n_samples    INTEGER     NOT NULL,
    backend      VARCHAR     NOT NULL DEFAULT 'opencv',
    notes        VARCHAR
);

-- Per-verification face check results (lightweight audit log)
CREATE TABLE IF NOT EXISTS face_checks (
    check_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ts_utc       TIMESTAMPTZ NOT NULL DEFAULT now(),
    session_id   UUID,
    backend      VARCHAR     NOT NULL,  -- 'opencv' | 'howdy'
    label        INTEGER,               -- predicted LBPH label (-1 = no face)
    confidence   FLOAT,                 -- LBPH confidence (lower = better)
    score        FLOAT       NOT NULL,  -- anomaly score returned to decision engine
    recognised   BOOLEAN     NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_face_checks_ts ON face_checks(ts_utc);

