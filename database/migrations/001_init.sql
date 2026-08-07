CREATE TABLE IF NOT EXISTS decision_audit (
    id BIGSERIAL PRIMARY KEY,
    request_id UUID NOT NULL,
    lane_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    proposed_action TEXT NOT NULL,
    final_action TEXT NOT NULL,
    approved BOOLEAN NOT NULL,
    reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
    model_versions JSONB NOT NULL DEFAULT '{}'::jsonb,
    payload_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS decision_audit_created_at_idx ON decision_audit (created_at DESC);
CREATE INDEX IF NOT EXISTS decision_audit_lane_idx ON decision_audit (lane_id, created_at DESC);

CREATE TABLE IF NOT EXISTS arena_snapshots (
    id BIGSERIAL PRIMARY KEY,
    lane_id TEXT NOT NULL,
    equity NUMERIC(20, 8) NOT NULL,
    cash NUMERIC(20, 8) NOT NULL,
    realized_pnl NUMERIC(20, 8) NOT NULL DEFAULT 0,
    unrealized_pnl NUMERIC(20, 8) NOT NULL DEFAULT 0,
    fees NUMERIC(20, 8) NOT NULL DEFAULT 0,
    max_drawdown_pct NUMERIC(10, 4) NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
