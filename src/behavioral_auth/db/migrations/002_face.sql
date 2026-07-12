CREATE TABLE IF NOT EXISTS face_models (
    model_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    model_path   VARCHAR     NOT NULL,
    n_samples    INTEGER     NOT NULL,
    backend      VARCHAR     NOT NULL DEFAULT 'opencv',
    notes        VARCHAR
);

CREATE TABLE IF NOT EXISTS face_checks (
    check_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ts_utc       TIMESTAMPTZ NOT NULL DEFAULT now(),
    session_id   UUID,
    backend      VARCHAR     NOT NULL,
    label        INTEGER,
    confidence   FLOAT,
    score        FLOAT       NOT NULL,
    recognised   BOOLEAN     NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_face_checks_ts ON face_checks(ts_utc);
