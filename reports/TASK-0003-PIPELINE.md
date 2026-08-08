# TASK-0003 — Decision Pipeline Integration Report

## 1. Pipeline Architecture Design
This report certifies the successful implementation of the end-to-end paper decision pipeline for Fondazione2:

`Coinbase public market data -> MarketSnapshot -> Kronos -> Nemotron -> Decision Aggregator -> Risk Engine -> ExecutionIntent -> PostgreSQL -> QuantDinger`

### Components & Responsibilities
- **Coinbase Advanced Public Adapter**: Fetches unauthenticated public candles and tickers from the official Coinbase Exchange API (approved unauthenticated market-data channel), mapping standard symbols like `BTC/USDC` to execution product ID `BTC-USDC` or using high-liquidity proxy `BTC-USD`.
- **MarketSnapshot Generator**: Consolidates active tickers and the last 32 one-minute candles into a canonical immutable MarketSnapshot with precise UTC timestamps.
- **Kronos Forecasting Service**: CPU-first forecasting model (`NeoQuasar/Kronos-base`) returning structured prediction objects (`Forecast`) from the same snapshot.
- **Nemotron Policy Server (SGLang)**: Bounded AI completion server running on an NVIDIA L4 GPU, providing criticized proposals (`Proposal`) that preserve deterministic risk bounds.
- **Decision Aggregator & Risk Engine**: Normalises all model outputs to Decision Contract v0 (`NO_TRADE`, `OPEN`, `ADD`, `REDUCE`, `CLOSE`), applying deterministic, serializable checks (freshness, spreads, loss limits, open position limits, entry cooldowns).
- **PostgreSQL PaperExecutor**: The sole and absolute runtime execution ledger, applying fee/slippage, ricalculating equity, and managing protective stop-loss/take-profit triggers atomically.
- **Prometheus Observability Engine**: Exposes comprehensive metrics (`foundation_decision_latency_seconds`, `foundation_equity`, `foundation_drawdown`, `foundation_model_failures_total`, `foundation_stale_data_total`, `foundation_risk_rejections_total`, `foundation_fills_total`, `foundation_component_reachable`, `foundation_realized_pnl`, `foundation_unrealized_pnl`) for scraper reachability.

---

## 2. Bounded Model Behavior & Fail-Closed Safety
To satisfy the Safety Contract, all exceptions, invalid data, or database audit write failures resolve deterministically to `NO_TRADE`:
- **Model failures/timeouts**: Any unreachable backend, JSON validation error, or response mismatch defaults safely to `NO_TRADE` with reason codes persisted in the audit ledger.
- **Market Data Failures (Blocker M1)**: Zero synthetic candle/fallback is allowed in the runtime loop. Any missing ticker, candles, or fetch failure aborts and fails-closed immediately to `NO_TRADE`.
- **Audit Persistence failure (Blocker K3 & L1)**: In case of database logging failures during decision auditing or finalization, the system aborts and fails-closed immediately to prevent un-audited trading.
- **Protective Exit Audits (Blocker M2)**: Real, correlated close audit records are written to `decision_audit` on PostgreSQL via `/v1/decision/protective_exit`, incrementing Prometheus fills.
- **Complete Causal Audit (Blocker L1)**: Exposes a finalization API `/v1/decision/finalize` that maps the actual executed `ExecutionIntent` and `ExecutionResult` back into the original `decision_audit` PostgreSQL row, generating a 100% reconstructible, cryptographically stable SHA-256 digest of the entire trade cycle.
- **Sizing checks**: SGLang/Nemotron may recommend reductions or exits but cannot under any circumstance bypass or override the Risk Engine's strict allocation bounds.

---

## 3. QuantDinger & Postgres State Integration
QuantDinger acts purely as a consumer, controller, and analytics platform, observing the canonical PostgreSQL ledger tables:
- **Single Source of Truth**: There is no competing ledger, SQLite database, or file-backed accounting truth.
- All historical trades, balances, and event triggers are written by the `PaperExecutor` and read by the QuantDinger analytics dashboard.
- **Read-Only Integration (Blocker L2 & M4)**: Fully integrated via a native Flask API endpoint `/api/agent/v1/trading/canonical-ledger` inside the active QuantDinger container process (`fondazione2-quantdinger-api-1`), proving direct read-only query consumption of the canonical PostgreSQL ledger schema.

---

## 4. Product Mapping Contract (Blocker M5)
Implements the canonical mapping contract for symbols to isolate execution vs market data proxy products:
- `canonical_symbol` -> `BTC/USDC`, `ETH/USDC`, `SOL/USDC`
- `execution_product_id` -> `BTC-USDC`, `ETH-USDC`, `SOL-USDC`
- `market_data_product_id` -> `BTC-USD`, `ETH-USD`, `SOL-USD` (proxied for high-liquidity ticker and candles)
- `market_data_is_proxy` -> `True`

---

## 5. Safety State Evidence
- `TRADING_MODE=paper` (Enforced)
- `LIVE_ARMED=false` (Enforced)
- `LIVE_ENABLED=false` (Enforced)
- `REAL_ORDERS_SENT=0` (Enforced)
- No private credentials exist in the system.
