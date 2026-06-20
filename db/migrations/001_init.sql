PRAGMA enable_progress_bar = false;

CREATE TABLE IF NOT EXISTS sessions (
    session_id UUID PRIMARY KEY,
    user_name VARCHAR NOT NULL,
    host_name VARCHAR,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ,
    mode VARCHAR NOT NULL,
    metadata JSON
);

CREATE TABLE IF NOT EXISTS raw_events (
    ts_ns UBIGINT NOT NULL,
    ts_utc TIMESTAMPTZ NOT NULL,
    session_id UUID NOT NULL,
    dev_path VARCHAR NOT NULL,
    dev_name VARCHAR,
    dev_type VARCHAR NOT NULL,
    ev_type USMALLINT NOT NULL,
    ev_code USMALLINT NOT NULL,
    ev_value INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_raw_session_ts ON raw_events(session_id, ts_ns);

CREATE TABLE IF NOT EXISTS feature_windows (
    session_id UUID NOT NULL,
    window_start_ns UBIGINT NOT NULL,
    window_end_ns UBIGINT NOT NULL,
    source VARCHAR NOT NULL,
    f_ks_count FLOAT,
    f_ks_mean_dwell FLOAT,
    f_ks_std_dwell FLOAT,
    f_ks_mean_flight FLOAT,
    f_ks_std_flight FLOAT,
    f_ks_backspace_ratio FLOAT,
    f_ks_repeat_ratio FLOAT,
    f_ks_entropy FLOAT,
    f_ms_count FLOAT,
    f_ms_speed_mean FLOAT,
    f_ms_speed_std FLOAT,
    f_ms_acc_mean FLOAT,
    f_ms_clicks FLOAT,
    f_ms_click_dwell FLOAT,
    f_ms_scrolls FLOAT,
    f_ms_idle_ratio FLOAT,
    f_ms_curvature FLOAT,
    f_ctx_hour_sin FLOAT,
    f_ctx_hour_cos FLOAT,
    f_ctx_is_weekend FLOAT,
    f_activity_density FLOAT,
    computed_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_fw_session_ts ON feature_windows(session_id, window_start_ns);

CREATE TABLE IF NOT EXISTS fused_sequences (
    session_id UUID NOT NULL,
    seq_end_ns UBIGINT NOT NULL,
    seq_len USMALLINT NOT NULL,
    data_json JSON NOT NULL,
    dedup_key UBIGINT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_seq_session_ts ON fused_sequences(session_id, seq_end_ns);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_seq_dedup ON fused_sequences(session_id, dedup_key);

CREATE TABLE IF NOT EXISTS model_registry (
    version INTEGER PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    model_path VARCHAR NOT NULL,
    scaler_path VARCHAR NOT NULL,
    threshold_challenge FLOAT NOT NULL,
    threshold_lock FLOAT NOT NULL,
    metrics_json JSON NOT NULL,
    notes VARCHAR
);

CREATE TABLE IF NOT EXISTS decisions (
    decision_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ts_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    session_id UUID NOT NULL,
    seq_end_ns UBIGINT NOT NULL,
    behavioral_score FLOAT NOT NULL,
    howdy_score FLOAT NOT NULL,
    fused_score FLOAT NOT NULL,
    decision VARCHAR NOT NULL,
    action_taken VARCHAR NOT NULL,
    mode VARCHAR NOT NULL,
    details JSON
);
CREATE INDEX IF NOT EXISTS idx_decision_session_ts ON decisions(session_id, ts_utc);
