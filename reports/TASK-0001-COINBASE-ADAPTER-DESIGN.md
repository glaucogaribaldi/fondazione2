# TASK-0001 - Coinbase Advanced Adapter Design

**Date:** Fri Aug 7 16:04:00 CEST 2026 / 14:04:00 UTC 2026
**Commit:** `openclaw/task-0001-bootstrap-audit`
**Adapter Scope:** Coinbase Advanced API (v3) Integration
**Mode:** DESIGN-ONLY (No private credentials or active connections used in this task)

---

## 1. Public Market Data Adapter Design

The public market data adapter connects to Coinbase Advanced REST and WebSocket endpoints to populate QuantDinger's caching layers and supply feature-engineering engines.

### A. Product Discovery & Master Data Cache
*   **Endpoint:** `/api/v3/brokerage/products` (GET)
*   **Data Fields Cached:**
    *   `product_id` (e.g. `BTC-USDT` or `ETH-USD`)
    *   `base_increment` (precision definition for size, e.g. `0.00000001`)
    *   `quote_increment` (precision definition for price, e.g. `0.01`)
    *   `base_min_size` (minimum trade size in base asset)
    *   `quote_min_size` (minimum trade size in quote currency)
    *   `status` (must be `online` to trade)
    *   `cancel_only`, `limit_only`, `post_only` status flags.
*   **Symbol Normalization:** Coinbase uses hyphenated symbols (e.g., `BTC-USDT`). Fondazione2 and QuantDinger use slash-separated symbols (e.g., `BTC/USDT`). The adapter will automatically convert `BTC/USDT` -> `BTC-USDT` on outbound API requests and reverse on inbound responses.

### B. Candles (OHLCV)
*   **Endpoint:** `/api/v3/brokerage/products/{product_id}/candles` (GET)
*   **Request Inputs:** `start` (UNIX), `end` (UNIX), `granularity` (e.g. `ONE_MINUTE`, `FIVE_MINUTE`, `FIFTEEN_MINUTE`, `ONE_HOUR`, `ONE_DAY`).
*   **Response mapping:** The array of rows `[start, low, high, open, close, volume]` will be converted to QuantDinger's normalized candle dictionary structure with floats and UTC datetime objects.
*   **Freshness Rules:**
    *   High-frequency candle fetches must check the timestamp of the latest completed bar.
    *   If the current time exceeds the expected timeframe window (e.g., 300 seconds for 5-minute candles) by more than 30 seconds, the cache is marked `STALE` and the system alerts the Decision Aggregator to enter a fail-closed/risk-off state (preventing new entries).

### C. Ticker and Best Bid/Ask
*   **Endpoint:** `/api/v3/brokerage/products/{product_id}/ticker` (GET) or WebSocket Channel `ticker`
*   **Mapping:** Maps `best_bid` and `best_ask` directly.
*   **WebSocket Channel:** `ticker` (real-time stream).
*   **Spread Verification:** Calculates bid/ask spread in basis points:
    $$\text{Spread (bps)} = \frac{\text{Ask} - \text{Bid}}{\text{Bid}} \times 10,000$$
    If the spread exceeds the threshold configured in `LaneSettings` (e.g., 15 bps), the Risk Engine fails closed to prevent high-slippage executions.

### D. Optional Level 2 Order Book (L2)
*   **WebSocket Channel:** `level2`
*   **Mapping:** Real-time bids/asks depth used for microstructural slippage calculations and liquidity checks. It maintains a local order book replica, applying incremental deltas from the WS stream.

---

## 2. Private Execution Adapter Design (Staged / Disarmed)

The execution adapter maps the abstract `ExchangeOrderAdapter` protocol into the Coinbase Advanced API v3 REST endpoints. In this phase, the adapter is written but lacks credentials, remaining technically disarmed.

### A. Authentication & Secret Management
*   Coinbase Advanced API v3 uses HMAC SHA256 signatures with JWT tokens for authentication.
*   **Secrets required:** `COINBASE_API_KEY` (Access Key) and `COINBASE_API_SECRET` (RSA Private Key).
*   **Vault Integration:** These keys must reside in the local TRE Vault (`vault://coinbase/api_key` / `vault://coinbase/api_secret`). They must never be saved in `.env`, repositories, reports, or printed in logs.

### B. Account & Balances
*   **Endpoint:** `/api/v3/brokerage/accounts` (GET)
*   **Mapping:** Fetches the list of user accounts, isolating cash assets (`USD`, `USDC`, `USDT`) and trading tokens (`BTC`, `ETH`). Populates the local `PortfolioSnapshot`.

### C. Order Creation & Cancelation
*   **Endpoints:**
    *   Create: `/api/v3/brokerage/orders` (POST)
    *   Cancel: `/api/v3/brokerage/orders/batch_cancel` (POST)
*   **Execution Types:**
    *   Market Buy/Sell
    *   Limit Buy/Sell (including `post_only` settings)
*   **Sizing Constraints:** Prior to order dispatch, the adapter must load the cached product master data and:
    1.  Force price decimal rounding to match the `quote_increment`.
    2.  Force quantity decimal rounding to match the `base_increment`.
    3.  Assert the order size exceeds both `base_min_size` and `quote_min_size`.

### D. Client Order ID & Idempotency
*   Coinbase Advanced requires a UUIDv4 `client_order_id` for every order placement.
*   **Safety Rule:**
    *   If a request fails due to a network timeout, the adapter must **never** retry with a new `client_order_id`.
    *   It must query the order status endpoint first using the original `client_order_id` to determine if the order was registered by the exchange, ensuring we prevent dual-fills (TOCTOU race safety).

### E. Order Status & Fills Query
*   **Endpoints:**
    *   Query Status: `/api/v3/brokerage/orders/historical/{order_id}` (GET)
    *   Query Fills: `/api/v3/brokerage/orders/historical/fills` (GET)
*   **Mapping to `FillSnapshot`:**
    *   `filled_qty` = total executed size.
    *   `avg_price` = weighted average price of all fills.
    *   `status` = mapped to `FILLED`, `PARTIAL`, `OPEN`, `REJECTED`, or `CANCELED`.
    *   `fees_by_ccy` = parsed fees from executed fill rows.

### F. Retry & Reconciliation Engine
*   **Fills Polling:** A background Celery task polls `/api/v3/brokerage/orders/historical/fills` to reconcile the database event ledger if a WebSocket drop occurs.
*   **Transient Failures:** `429 Too Many Requests` or `5xx Server Error` triggers a truncated exponential backoff retry.
*   **Definitive Failures:** `INSUFFICIENT_FUNDS` or `INVALID_ORDER` marks the order as `REJECTED`, triggers a system alert, and cancels any pending additions in the lane.

---

## 3. Strict Safety Exclusions

1.  **Transfer Exclusions:** Coinbase Advanced API exposes endpoints for funding, wallet transfers, and cryptocurrency withdrawals. The API credentials generated on the Coinbase developer portal must be strictly configured to **exclude** transfer and withdrawal capabilities.
2.  **Code assertion:** Inside the python codebase, any API routes or classes related to transfer, withdrawal, or funding operations are completely **FORBIDDEN** and will not be imported or written.

---

## 4. Verification Verdict

`VPS_UNCHANGED=true` (Confirmed, no modifications, wipes, or configurations were applied to any VPS during this read-only audit turn).
