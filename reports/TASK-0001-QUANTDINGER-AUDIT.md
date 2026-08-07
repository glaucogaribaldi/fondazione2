# TASK-0001 - QuantDinger Upstream Audit Report

**Date:** Fri Aug 7 15:58:00 CEST 2026 / 13:58:00 UTC 2026
**Commit:** `openclaw/task-0001-bootstrap-audit`
**Audited Repository:** `https://github.com/OpenByteInc/QuantDinger`
**Target Version/Commit Pin:** `v5.0.15` (Release Date: August 3, 2026)
**Docker Image Pin:** `ghcr.io/OpenByteInc/quantdinger-backend:v5.0.15`

---

## 1. Upstream & Version Audit

*   **Release Tag:** `v5.0.15`
*   **Release Date:** August 3, 2026
*   **Docker Registry:** GitHub Container Registry (`ghcr.io`)
*   **Image Path:** `ghcr.io/OpenByteInc/quantdinger-backend:v5.0.15` (Multi-arch support: `amd64` and `arm64`).
*   **License:** **Apache License 2.0** (Highly permissive, allows private closed-source strategy implementations, custom extensions, and integration with Fondazione2 proprietary risk engines).

---

## 2. Process Roles & Concurrency Topology

The backend utilizes a single Docker image that can be instantiated into separate, highly specialized, and decoupled containerized services depending on the process startup arguments:

1.  **API Node:**
    *   *Command:* `gunicorn -c gunicorn_config.py run:app`
    *   *Responsibility:* Thin HTTP interface. Handles client authentication, request validation, and submits commands to the PostgreSQL command queue.
    *   *Constraint:* Never executes long loops, strategy steps, or broker polling inline.
2.  **Migration Runner:**
    *   *Command:* `python -m app.commands.migrate`
    *   *Responsibility:* Runs Alembic database schema migrations. Executed as a blocking pre-startup hook that must succeed before other services launch.
3.  **Trading Worker:**
    *   *Command:* `python -m app.commands.trading_worker`
    *   *Responsibility:* Owns strategy loop runtimes, active orders, and live broker communication.
    *   *Mechanism:* Uses PG `SKIP LOCKED` row locks on the `qd_strategy_commands` table.
4.  **Scheduler Daemon:**
    *   *Command:* `python -m app.commands.scheduler`
    *   *Responsibility:* Manages overall loop heartbeats, portfolio accounting snapshots, and payment/billing sweeps.
5.  **Celery Worker:**
    *   *Command:* `celery -A app.celery_app:celery_app worker`
    *   *Responsibility:* Offloads stateless, asynchronous, or long-running computations (e.g. historical backtests, complex report generation, AI LLM reasoning/reflection tasks).
6.  **Celery Beat:**
    *   *Command:* `celery -A app.celery_app:celery_app beat`
    *   *Responsibility:* Dispatches periodic maintenance events into Celery.

---

## 3. High Availability, Leases, and Fencing

QuantDinger implements a robust PostgreSQL-backed locking model to avoid dual-write brain split failures (such as two workers trading the same account simultaneously):

*   **Leases Table (`qd_strategy_runtime_leases`):** Every active strategy runtime requires an active row lock. Runtimes must continuously renew their lease.
*   **Fencing Tokens:** A monotonic sequence number increments whenever a stale lease is forcefully stolen or recovered by another healthy worker, ensuring stale instructions from late-responding workers are rejected by downstream APIs.
*   **Leader Election (`qd_process_leases`):** A leader lock ensures that only one worker acts as the global scheduler loop or active market data aggregator.
*   **Heartbeats (`qd_worker_heartbeats`):** All running processes write progress heartbeats into this table for real-time observability.

---

## 4. Dual-Redis Network Isolation

To prevent out-of-memory crashes and data loss, QuantDinger segregates Redis into two distinct logical services with different persistence policies:

1.  **Redis Cache (`redis-cache`):** Used for public market data, candle storage, and API response caching.
    *   *Eviction Policy:* `allkeys-lru` or similar volatile eviction is allowed.
2.  **Redis Job Queue (`redis-jobs`):** Serving as the Celery broker.
    *   *Eviction Policy:* Strict `noeviction`.
    *   *Persistence:* Append-Only File (AOF) enabled, ensuring that task lists and execution tokens survive process crashes.

---

## 5. Security & Secret Management

*   **Config Separation:** Environment variables are loaded into Pydantic models.
*   **Redaction:** Secret fields (API keys, secrets) are excluded from log outputs via custom serialization rules in `BaseRestClient`.
*   **Tokens Scopes:** QuantDinger's **Agent Gateway** exposes `/api/agent/v1` routes using API token scopes (MCP, research, trading). Live trading remains explicitly blocked for the Agent Gateway.

---

## 6. Extension Points for Coinbase Advanced

Because QuantDinger v5.0.15 does not natively include a Coinbase Advanced adapter, we must implement our adapter via two cleanly separated extension points:

### A. Execution Adapter (Private Actions)
*   **Target Directory:** `backend_api_python/app/services/live_trading/`
*   **Interface Class:** Implement `class CoinbaseAdvancedClient(BaseRestClient)` inside `app/services/live_trading/coinbase_advanced.py`.
*   **Protocol compliance:** The adapter must conform to `ExchangeOrderAdapter` by exposing:
    1.  `place_market_order(self, intent: OrderIntent) -> LiveOrderResult`
    2.  `place_limit_order(self, intent: OrderIntent) -> LiveOrderResult`
    3.  `cancel_order(self, intent: OrderIntent, *, order_id: str) -> Dict[str, Any]`
    4.  `wait_for_fill(self, intent: OrderIntent, *, order_id: str, max_wait_sec: float) -> FillSnapshot`
    5.  `query_position(self, intent: OrderIntent) -> PositionSnapshot`
*   **Factory registration:** Register the new class in `app/services/live_trading/factory.py` under the ID `"coinbase_advanced"`.

### B. Public Market Data Adapter (Public Actions)
*   **Target Directory:** `backend_api_python/app/data_sources/`
*   **Implementation:** Create `app/data_sources/coinbase_public.py` to fetch:
    1.  Ticker, Bid/Ask spreads.
    2.  OHLCV Candles.
    3.  Product list / master data (precision, minimum notional sizes, base/quote tick increments).
*   **Factory registration:** Register inside `DataSourceFactory` so strategies can query Coinbase candles and prices using standard `get_history` queries.

---

## 7. Verification Verdict

`VPS_UNCHANGED=true` (Confirmed, no modifications, wipes, or configurations were applied to any VPS during this read-only audit turn).
