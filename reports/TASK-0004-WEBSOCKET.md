# TASK-0004 — Coinbase Public WebSocket Service Report

**Date:** Sat Aug 8 10:50:00 CEST 2026 / 08:50:00 UTC 2026
**Component:** `CoinbaseWebSocketService`
**Status:** FULLY INTEGRATED & RUNNING

---

## 1. Public Advanced Trade WebSocket Integration
We have successfully implemented and integrated a resilient, public unauthenticated WebSocket client connected to:
`wss://advanced-trade-ws.coinbase.com`

The client operates in the background of the FastAPI `decision-service` process and consumes the following public Advanced Trade channels without requiring any private API keys.

---

## 2. Subscription Sharding & Batching
Coinbase limits the size of subscription messages and the number of symbols per subscribe event. To ensure total universe visibility, our service shards the **517 active price streams** into clean batches of **100 products each** (Blocker C):
*   **Total subscription batches sent**: **6**
*   **Channels subscribed**:
    - `ticker` (batches of 100 for all 517 active products)
    - `status` (dynamic catalog state updates)
    - `heartbeats` (liveness monitoring and watchdog)

---

## 3. Resiliency, Watchdog, & Reconnect
*   **Heartbeat Liveness Watchdog:** The service runs an asynchronous watchdog loop checking heartbeat age every 5 seconds. If the age exceeds 25 seconds, the connection is marked unhealthy and restarted automatically.
*   **Exponential Backoff Reconnect:** Upon connection loss, the service reconnects with bounded exponential backoff (starting at `1s`, doubling up to a maximum of `60s`).
*   **Out-of-Order / Sequence-Gap Tracking:** Every received message extracts the `sequence_num` from the Coinbase packet. A comparison is run against the last received sequence; any gap is logged and recorded in the Prometheus counter `foundation_ws_sequence_gaps_total`.

---

## 4. Normalization and Real-time Ticker Events Routing
When the public WebSocket pushes a tick for a proxy pair (such as `BTC-USD`), the service automatically routes the price update to all mapped symbols in the database:
*   Inbound `BTC-USD` tick -> Updates `market_marks` for `BTC/USD` AND `BTC/USDC`.
*   This triggers the `PaperExecutor` to dynamically recalculate the equity/drawdown of any active lane holding `BTC/USDC`, ensuring the decision service is fed with real-time mark-to-market data!
