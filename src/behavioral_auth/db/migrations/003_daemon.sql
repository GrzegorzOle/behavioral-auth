-- Daemon state machine, learning cycles, monitoring scores and alarms.

-- An enrollment is one learned pattern for one person. `reset` retires the
-- current one and opens a new one; everything downstream is scoped to it.
CREATE TABLE IF NOT EXISTS enrollments (
    enrollment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    retired_at    TIMESTAMPTZ,
    status        VARCHAR NOT NULL DEFAULT 'learning',  -- learning | active | retired
    user_name     VARCHAR,
    host_name     VARCHAR,
    notes         VARCHAR
);

CREATE TABLE IF NOT EXISTS daemon_state (
    id            INTEGER PRIMARY KEY,   -- always 1: this table holds one row
    state         VARCHAR NOT NULL,
    since         TIMESTAMPTZ NOT NULL DEFAULT now(),
    enrollment_id UUID,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    details       JSON
);

CREATE TABLE IF NOT EXISTS state_transitions (
    transition_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ts_utc        TIMESTAMPTZ NOT NULL DEFAULT now(),
    enrollment_id UUID,
    from_state    VARCHAR,
    to_state      VARCHAR NOT NULL,
    reason        VARCHAR,
    details       JSON
);

CREATE TABLE IF NOT EXISTS learning_cycles (
    cycle_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ts_utc          TIMESTAMPTZ NOT NULL DEFAULT now(),
    enrollment_id   UUID NOT NULL,
    cycle_no        INTEGER NOT NULL,
    n_train         INTEGER NOT NULL,
    n_holdout       INTEGER NOT NULL,
    pass_rate       FLOAT,
    error_ratio     FLOAT,
    threshold       FLOAT,
    threshold_drift FLOAT,
    separation      FLOAT,   -- synthetic-impostor error / holdout error
    stable          BOOLEAN NOT NULL,
    stable_streak   INTEGER NOT NULL,
    promoted        BOOLEAN NOT NULL DEFAULT false,
    metrics_json    JSON
);
CREATE INDEX IF NOT EXISTS idx_cycles_enr ON learning_cycles(enrollment_id, ts_utc);

-- Replaces `decisions`: one row per scored sequence. There is no lock action,
-- so there is no "action_taken" — only a verdict.
CREATE TABLE IF NOT EXISTS scores (
    score_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ts_utc        TIMESTAMPTZ NOT NULL DEFAULT now(),
    enrollment_id UUID,
    session_id    UUID NOT NULL,
    seq_end_ns    UBIGINT NOT NULL,
    error         FLOAT NOT NULL,
    ratio         FLOAT NOT NULL,   -- error / calibrated threshold
    beh_anomalous BOOLEAN NOT NULL,
    face_state    VARCHAR,          -- match | stranger | unknown
    fused         FLOAT,            -- display only; not the decision rule
    verdict       VARCHAR NOT NULL, -- normal | anomalous | deadband
    state         VARCHAR NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scores_ts ON scores(ts_utc);

CREATE TABLE IF NOT EXISTS alarms (
    alarm_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at      TIMESTAMPTZ,
    enrollment_id UUID,
    session_id    UUID,
    reason        VARCHAR NOT NULL,  -- behavioral | face | both
    peak_ratio    FLOAT,
    n_scores      INTEGER,
    details       JSON
);
CREATE INDEX IF NOT EXISTS idx_alarms_started ON alarms(started_at);

CREATE TABLE IF NOT EXISTS face_samples (
    sample_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ts_utc          TIMESTAMPTZ NOT NULL DEFAULT now(),
    enrollment_id   UUID NOT NULL,
    path            VARCHAR,
    width           INTEGER,
    sharpness       FLOAT,
    brightness      FLOAT,
    self_confidence FLOAT   -- NULL before a provisional model exists
);
CREATE INDEX IF NOT EXISTS idx_face_samples_enr ON face_samples(enrollment_id);

ALTER TABLE sessions        ADD COLUMN IF NOT EXISTS role          VARCHAR DEFAULT 'user';
ALTER TABLE fused_sequences ADD COLUMN IF NOT EXISTS enrollment_id UUID;

-- feature_windows had no watermark and no unique key, so every re-run of the
-- feature pipeline re-inserted the same windows. Collapse the duplicates
-- (keeping the earliest computed_at) before enforcing uniqueness. Nothing is
-- lost: windows are pure functions of raw_events.
CREATE TABLE feature_windows_rebuilt AS
    SELECT DISTINCT ON (session_id, window_start_ns) *
    FROM feature_windows
    ORDER BY session_id, window_start_ns, computed_at;

DROP TABLE feature_windows;
ALTER TABLE feature_windows_rebuilt RENAME TO feature_windows;
ALTER TABLE feature_windows ADD COLUMN IF NOT EXISTS enrollment_id UUID;

CREATE UNIQUE INDEX uniq_fw ON feature_windows(session_id, window_start_ns);
CREATE INDEX idx_fw_session_ts ON feature_windows(session_id, window_start_ns);
