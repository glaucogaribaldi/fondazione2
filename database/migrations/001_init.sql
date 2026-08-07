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

CREATE TABLE IF NOT EXISTS paper_balances (
    id BIGSERIAL PRIMARY KEY,
    lane_id TEXT NOT NULL UNIQUE,
    equity NUMERIC(20, 8) NOT NULL,
    cash NUMERIC(20, 8) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS paper_positions (
    id BIGSERIAL PRIMARY KEY,
    lane_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    quantity NUMERIC(20, 8) NOT NULL,
    entry_price NUMERIC(20, 8) NOT NULL,
    stop_loss_price NUMERIC(20, 8),
    take_profit_price NUMERIC(20, 8),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT paper_positions_lane_symbol UNIQUE (lane_id, symbol)
);

CREATE TABLE IF NOT EXISTS execution_intents (
    id BIGSERIAL PRIMARY KEY,
    execution_intent_id UUID NOT NULL UNIQUE,
    risk_decision_id UUID NOT NULL,
    mode TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity NUMERIC(20, 8) NOT NULL,
    order_type TEXT NOT NULL DEFAULT 'MARKET',
    limit_price NUMERIC(20, 8),
    stop_price NUMERIC(20, 8),
    take_profit_price NUMERIC(20, 8),
    time_exit_at TIMESTAMPTZ,
    client_order_id TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_results (
    id BIGSERIAL PRIMARY KEY,
    execution_intent_id UUID NOT NULL UNIQUE REFERENCES execution_intents(execution_intent_id) ON DELETE CASCADE,
    broker_order_id TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_quantity NUMERIC(20, 8) NOT NULL,
    filled_quantity NUMERIC(20, 8) NOT NULL,
    average_fill_price NUMERIC(20, 8),
    fee NUMERIC(20, 8) NOT NULL DEFAULT 0,
    slippage NUMERIC(20, 8) NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS market_marks (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL UNIQUE,
    price NUMERIC(20, 8) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
