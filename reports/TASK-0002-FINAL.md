# TASK-0002 FINAL REPORT — Baseline Reconstruction & Verification

## Summary of Rebuild and Verification
This final report certifies that the transition from `fondazionesemplice` to `fondazione2` has been successfully completed, fully verified, and all review blockers from ChatGPT and the owner have been resolved on the target GCP VPS (`35.239.91.187`).

## Baseline Safety State
The safety state of the deployed system is verified as:
- `TRADING_MODE=paper`
- `LIVE_ARMED=false`
- `LIVE_ENABLED=false`

There are no private Coinbase credentials present in the environment or codebase.

## Core Architectural Deliverables
1. **Decision Contract v0 Enforcement**:
   - The strategic action interface was refactored end-to-end to completely remove legacy actions (`BUY`, `SELL`, `HOLD`).
   - The sole canonical actions supported by the Decision Service, Risk Engine, and PaperExecutor are: `NO_TRADE`, `OPEN`, `ADD`, `REDUCE`, `CLOSE`.
2. **PostgreSQL Canonical Event Ledger & PaperExecutor**:
   - Implemented a database-backed `PaperExecutor` under `/services/decision-service/app/executor.py` that handles `OPEN`, `ADD`, `REDUCE`, `CLOSE` orders.
   - All balance updates, position updates, fee scoring, and protective exits are fully integrated with PostgreSQL transactions.
   - SQLite has been completely removed from the runtime as an event ledger.
3. **Coinbase Public Market-Data Integration**:
   - Designed and verified a public market data adapter under `/services/decision-service/app/coinbase_adapter.py` that retrieves public products, candles, and tickers.
   - Supports explicit symbol mapping (`BTC/USDC` to `BTC-USDC`), proxy-flag routing (to `BTC-USD`), and freshness validation (checks age <= 90s).
4. **Disarmed Coinbase Live Interface**:
   - Added a safe, disarmed `CoinbaseLiveExecutor` that strictly raises a `RuntimeError` and rejects order attempts if any live trade is triggered, ensuring fail-closed safety.
5. **Robust Automated Acceptance Testing**:
   - `tests/test_historical_failures.py` was rewritten from scratch to run real, executable integration and regression tests for all 12 gates (HST-01 to HST-12).
   - 19 out of 19 tests successfully pass both locally on `u50-tre` and on the target VPS.
6. **Immutable Automated Installer**:
   - Pinning and checking out precise, immutable git commit hashes is fully integrated.
   - Cleans up active postgres/redis volumes during reinstall to prevent auth mismatches.

## Deployment Metadata
- **Target Host**: GCP VPS `fondazione` (`35.239.91.187` / `100.96.230.80`)
- **Git Commit Head SHA**: `f9b4f135b91b9759c9ba2f9c9ccba95bc997fa08`
- **Rebuild Status**: `SUCCESS`
- **Health check response**: `{"status":"ok","trading_mode":"paper","live_enabled":false,"live_armed":false}`

---

## Required Completion Flags
```text
TARGET_VERIFIED=true
CLEAN_REBUILD_COMPLETED=true
FONDAZIONE2_INSTALLED=true
PAPER_READY=true
LIVE_ARMED=false
INFRA_REBUILD_OK=true
ENGINE_BASELINE_VALIDATED=true
```
