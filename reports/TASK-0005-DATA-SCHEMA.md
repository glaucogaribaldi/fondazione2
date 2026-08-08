# TASK-0005 — Canonical Historical Data Schema Report

**Date:** Sat Aug 8 13:00:00 CEST 2026 / 11:00:00 UTC 2026
**Component:** PostgreSQL & SQLite Schema
**Status:** FULLY INITIALIZED & CERTIFIED

---

## 1. Relational Database Design Overview
To support Fondazione2 Engine 1.0, we have modeled and implemented a versioned, idempotent, and isolated historical data layer. This schema exists side-by-side with our existing real-time paper trading tables while guaranteeing absolute database-level isolation.

---

## 2. PostgreSQL Production Tables and DDL
The following PostgreSQL tables have been dynamically created and verified on our live GCP production target database:

### A. `historical_candles`
Holds canonical, validated, non-synthetic historical OHLCV data.
*   **DDL Schema:**
    ```sql
    CREATE TABLE IF NOT EXISTS historical_candles (
        product_id TEXT NOT NULL,
        canonical_symbol TEXT NOT NULL,
        granularity INTEGER NOT NULL,
        candle_open TIMESTAMPTZ NOT NULL,
        open NUMERIC(28,10) NOT NULL,
        high NUMERIC(28,10) NOT NULL,
        low NUMERIC(28,10) NOT NULL,
        close NUMERIC(28,10) NOT NULL,
        volume NUMERIC(28,10) NOT NULL,
        ingestion_timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
        quality_state TEXT NOT NULL DEFAULT 'VALID',
        PRIMARY KEY (product_id, granularity, candle_open)
    );
    ```
*   **Properties:** Uniqueness is enforced by the composite primary key `(product_id, granularity, candle_open)`, preventing duplicate candle entries.

### B. `historical_backfill_checkpoints`
Stores progress metadata for instant resume/restart safety.
*   **DDL Schema:**
    ```sql
    CREATE TABLE IF NOT EXISTS historical_backfill_checkpoints (
        product_id TEXT NOT NULL,
        granularity INTEGER NOT NULL,
        start_time TIMESTAMPTZ NOT NULL,
        end_time TIMESTAMPTZ NOT NULL,
        last_processed_time TIMESTAMPTZ NOT NULL,
        status TEXT NOT NULL DEFAULT 'PENDING',
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (product_id, granularity, start_time, end_time)
    );
    ```

### C. `historical_gaps`
Tracks detected missing expected candle intervals for recovery purposes.
*   **DDL Schema:**
    ```sql
    CREATE TABLE IF NOT EXISTS historical_gaps (
        product_id TEXT NOT NULL,
        granularity INTEGER NOT NULL,
        gap_start TIMESTAMPTZ NOT NULL,
        gap_end TIMESTAMPTZ NOT NULL,
        status TEXT NOT NULL DEFAULT 'DETECTED',
        attempts INTEGER NOT NULL DEFAULT 0,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (product_id, granularity, gap_start, gap_end)
    );
    ```

### D. `dataset_versions`
Registers immutable historical datasets.
*   **DDL Schema:**
    ```sql
    CREATE TABLE IF NOT EXISTS dataset_versions (
        dataset_id TEXT PRIMARY KEY,
        dataset_hash TEXT NOT NULL,
        universe_hash TEXT NOT NULL,
        canonical_symbols JSONB NOT NULL DEFAULT '[]'::jsonb,
        timeframe INTEGER NOT NULL,
        start_time TIMESTAMPTZ NOT NULL,
        end_time TIMESTAMPTZ NOT NULL,
        as_of TIMESTAMPTZ NOT NULL,
        preprocessing_version TEXT NOT NULL,
        code_sha TEXT NOT NULL,
        config_hash TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    ```

### E. `replay_runs`
Tracks backtest/replay experiment metadata.
*   **DDL Schema:**
    ```sql
    CREATE TABLE IF NOT EXISTS replay_runs (
        run_id TEXT PRIMARY KEY,
        dataset_id TEXT NOT NULL,
        config_hash TEXT NOT NULL,
        code_sha TEXT NOT NULL,
        seed INTEGER NOT NULL,
        fee_rate NUMERIC(10,6) NOT NULL,
        slippage_rate NUMERIC(10,6) NOT NULL,
        start_time TIMESTAMPTZ NOT NULL,
        end_time TIMESTAMPTZ NOT NULL,
        result_digest TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    ```

### F. `replay_ledger`
Fully isolated execution ledger for backtests (preventing paper-state contamination).
*   **DDL Schema:**
    ```sql
    CREATE TABLE IF NOT EXISTS replay_ledger (
        id BIGSERIAL PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES replay_runs(run_id) ON DELETE CASCADE,
        symbol TEXT NOT NULL,
        action TEXT NOT NULL,
        side TEXT NOT NULL,
        quantity NUMERIC(28,10) NOT NULL,
        price NUMERIC(28,10) NOT NULL,
        fee NUMERIC(28,10) NOT NULL,
        slippage NUMERIC(28,10) NOT NULL,
        unrealized_pnl NUMERIC(28,10) NOT NULL,
        realized_pnl NUMERIC(28,10) NOT NULL,
        equity NUMERIC(28,10) NOT NULL,
        cash NUMERIC(28,10) NOT NULL,
        timestamp TIMESTAMPTZ NOT NULL
    );
    ```
