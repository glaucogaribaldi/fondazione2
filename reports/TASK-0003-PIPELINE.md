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

---

## 2. Bounded Model Behavior & Fail-Closed Safety
To satisfy the Safety Contract, all exceptions and invalid data resolve deterministically to `NO_TRADE`:
- **Model failures/timeouts**: Any unreachable backend, JSON validation error, or response mismatch defaults safely to `NO_TRADE` with reason codes persisted in the audit ledger.
- **Sizing checks**: SGLang/Nemotron may recommend reductions or exits but cannot under any circumstance bypass or override the Risk Engine's strict allocation bounds.

---

## 3. QuantDinger & Postgres State Integration
QuantDinger acts purely as a consumer, controller, and analytics platform, observing the canonical PostgreSQL ledger tables:
- **Single Source of Truth**: There is no competing ledger, SQLite database, or file-backed accounting truth.
- All historical trades, balances, and event triggers are written by the `PaperExecutor` and read by the QuantDinger analytics dashboard.

---

## 4. Safety State Evidence
- `TRADING_MODE=paper` (Enforced)
- `LIVE_ARMED=false` (Enforced)
- `LIVE_ENABLED=false` (Enforced)
- `REAL_ORDERS_SENT=0` (Enforced)
- No private credentials exist in the system.
