# TASK-0001 - Coinbase Advanced Adapter Public Design

Status: `PUBLIC_DESIGN / RUNTIME_VERIFICATION_PENDING`
Date: 2026-08-07

## API family

Fondazione2 targets Coinbase Advanced Trade v3.

REST base documented by Coinbase:

`https://api.coinbase.com/api/v3/brokerage/{resource}`

Public REST endpoints exist for products, product book, candles and market trades. Private endpoints separate `view`, `trade` and `transfer` permissions.

## Adapter split

Do not create one monolithic Coinbase client.

```text
coinbase/
  public_market_data
  product_master
  websocket_market_data
  account_view
  order_execution
  reconciliation
  auth
```

The first clean Fondazione2 install requires only public market data.

## Public market data

### REST

Use public endpoints for bootstrap/discovery where possible:

- list public products;
- get public product;
- public product candles;
- public market trades;
- public product book.

Public REST has caching behavior; real-time decision paths should prefer WebSocket or explicitly handle caching semantics.

### WebSocket

Production market-data endpoint:

`wss://advanced-trade-ws.coinbase.com`

Useful public channels:

- `heartbeats`;
- `status`;
- `ticker`;
- `market_trades`;
- `candles`;
- `level2`.

Use `heartbeats` to keep subscriptions alive and monitor continuity.

For an order-book-dependent strategy, prefer `level2` because Coinbase documents it as the easiest channel for keeping an in-sync book and handling delivery consistency.

## Critical USDC caveat

Coinbase documents special behavior for many `-USDC` products on WebSocket market-data channels: subscribing to `-USDC` outside the user channel can return the corresponding `-USD` market data. `USDT-USDC` and `EURC-USDC` are documented exceptions.

This must become an explicit adapter test.

Fondazione2 must never silently map:

```text
BTC-USDC == BTC-USD
```

without recording the source market and mapping policy.

Proposed normalized identifiers:

```text
execution_product_id = BTC-USDC
market_data_product_id = BTC-USD | BTC-USDC
market_data_is_proxy = true | false
```

For paper certification, determine empirically and via current Coinbase metadata which exact market source is used for each tradeable USDC product.

If exact real-time `-USDC` market data requires authentication, decide explicitly between:

1. use a view-only authenticated market-data connection; or
2. use a documented USD proxy and model the basis/spread separately; or
3. exclude that product from a paper trial requiring exact execution-book simulation.

Do not recreate the historical USDC/USD feed mismatch.

## Product master

Normalize at minimum:

- product ID;
- base/quote currency;
- product status;
- trading disabled/cancel-only flags when exposed;
- base increment;
- quote increment;
- minimum sizes/notional;
- market type;
- source timestamp;
- supported order configurations when relevant.

Dynamic Universe must consume product master data, not hard-coded symbol names.

## Private account/read adapter - future

Private endpoints with `view` permission are sufficient for read-only operations such as accounts, order history, fills, products and preview order functionality according to the current endpoint permissions table.

Before live, use a read-only/view phase for:

- balance reconciliation;
- portfolio observation;
- fee/tier observation;
- preview orders where applicable;
- comparing paper fills with realistic executable conditions.

## Live execution adapter - future

Create Order requires `trade` permission.

Fondazione2 will need normalized methods such as:

```text
preview_order(intent)
create_order(intent)
cancel_order(order_id)
get_order(order_id)
list_fills(...)
reconcile(client_order_id)
```

Do not implement or expose portfolio fund transfer functions in the Fondazione2 execution surface.

## Idempotency

Coinbase Advanced Create Order requires `client_order_id` and documents duplicate behavior: if a non-unique ID is provided, a new order is not created and the corresponding existing order is returned.

Fondazione2 should derive a stable client order ID from the immutable `ExecutionIntent` ID and persist the mapping before or atomically with send state.

Required test:

```text
same ExecutionIntent replayed N times -> at most one Coinbase order
```

## Protective orders

Coinbase Advanced supports order types including attached TP/SL/bracket structures. Current documentation includes attached take-profit/stop-loss configuration on an originating order.

Fondazione2 paper behavior should model protective exits independently and deterministically. For future live, evaluate whether a strategy's protection is best represented by native attached/bracket orders or by a managed exit, but never rely exclusively on an AI model being online to close risk.

Even native exchange protection is not equivalent to guaranteed execution under all volatility conditions; reconciliation remains necessary.

## User WebSocket - future

Authenticated user-order endpoint:

`wss://advanced-trade-ws-user.coinbase.com`

Use for live/shadow order state and reconciliation, not as the only source of truth. REST reconciliation should repair missed/reordered stream events.

## Permissions policy

Desired future key permissions:

- `view`: yes;
- `trade`: only for explicit live phase;
- `transfer`: no.

The key/portfolio model must be verified at live-enablement time because Coinbase permissions and portfolio semantics may evolve.

## Paper adapter requirements

Paper does not need private Coinbase credentials for the initial implementation.

It must use real Coinbase market data and model:

- current bid/ask or order book;
- spread;
- fees based on configured/observed assumptions;
- slippage;
- size/precision/minimums;
- partial fill model when needed;
- stale/gap handling;
- protective exits;
- source-market identity (`USD` vs `USDC`).

## Runtime acceptance tests

Before `COINBASE_PUBLIC_ADAPTER_PASS`:

1. enumerate products;
2. verify BTC/ETH/SOL candidate metadata;
3. prove quote currency and product ID normalization;
4. connect heartbeat;
5. connect ticker;
6. connect level2 for at least one selected product;
7. detect/recover a sequence gap or reconnect scenario;
8. prove freshness timestamps;
9. explicitly test `BTC-USDC`/`BTC-USD` mapping behavior;
10. no private credentials loaded.

Before future `COINBASE_LIVE_ADAPTER_PASS`:

- view-only account/fill tests;
- preview order tests;
- unique client order ID tests;
- duplicate replay test;
- cancel/reconcile tests;
- user WebSocket recovery;
- permission proof `transfer=false` or equivalent;
- live kill switch test under a dedicated non-production/safely bounded procedure.
