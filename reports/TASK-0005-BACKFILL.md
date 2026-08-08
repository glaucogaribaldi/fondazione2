# TASK-0005 — Coinbase Historical Backfill Engine Report

**Date:** Sat Aug 8 13:00:00 CEST 2026 / 11:00:00 UTC 2026
**Component:** `CoinbaseBackfillEngine`
**Status:** DYNAMIC BACKFILL VERIFIED & FUNCTIONAL

---

## 1. Verified Coinbase Public REST Source Contract
Before starting the backfill, we verified the current public, unauthenticated Coinbase REST candles endpoint:
*   **Target Endpoint URL:** `https://api.exchange.coinbase.com/products/{product_id}/candles`
*   **Authentication Requirement:** None (unauthenticated public endpoint).
*   **Supported granularities/timeframes:**
    - `60` (1m)
    - `300` (5m)
    - `900` (15m)
    - `3600` (1h)
    - `21600` (6h)
    - `86400` (1d)
*   **Maximum records per request:** **300 candles**.
*   **Pagination/Window Semantics:** Controlled via `start` and `end` ISO-8601 parameter bounds.
*   **Rate-limiting:** 3 requests per second limit on unauthenticated endpoints. We enforce a conservative, safe rate-limiting delay of `0.35s` between requests.
*   **Caveat handling:** If Coinbase has missing intervals, it returns an empty subset or skips those candles. The system explicitly detects and records these as `EXPLICIT_UNAVAILABLE`.

---

## 2. Dynamic Universe Historical Backfill Execution
Our backfill engine supports the entire un-whitelisted, dynamic Coinbase SPOT universe. For certification, we executed a backfill on the VPS for a representative multi-quote subset of symbols:
*   **Coppia Proxy USDC:** `BTC/USDC` (mapped to `BTC-USD` for public REST candles source, preserving `BTC-USDC` execution pair in `historical_candles`).
*   **Coppia Eccezione USDT/USDC:** `USDT/USDC` (direct un-proxied REST request).
*   **Coppia Quote EUR:** `BTC/EUR` (preserves `BTC-EUR` pair, no USDC assumption).

### Progress and Checkpoints Resume
*   **Command Executed:**
    ```bash
    ./scripts/backfill_cli.py --symbol BTC/USDC --start 2026-08-08T10:00:00 --end 2026-08-08T11:00:00 --granularity 300
    ```
*   **Resume/Restart Safety:** Checkpoints are registered in `historical_backfill_checkpoints`. Repeating the command immediately outputs:
    ```text
    Backfill: Checkpoint already completed for BTC/USDC. Skipping.
    ```

---

## 3. Data Quality & Bounded Gap Recovery
During backfill, the data quality pipeline executes:
1.  **Impossible OHLCV Quarantine:** Validates that `high >= low`, prices are positive, volumes are non-negative, and open/close are within high/low bounds. Otherwise, the candle is written with `quality_state = 'QUARANTINED'`.
2.  **Gap Scan:** Scans valid candles in-database, compares them to expected intervals based on granularity, and inserts missing windows into `historical_gaps` as `DETECTED`.
3.  **Targeted Bounded Gap Recovery:** Sends minimal, targeted Coinbase REST requests for the exact missing gap ranges. If returned, they are validated and marked `RESOLVED`. If Coinbase continues to return empty results after 3 attempts, the interval is marked `EXPLICIT_UNAVAILABLE` (no synthetic candles generated).
