# TASK-0004 — Dynamic Coinbase SPOT universe & WebSocket normalization

**Issue:** #10  
**Branch:** `openclaw/task-0004-coinbase-universe-websocket`  
**Target:** GCP `fondazione`, `us-central1-a`, internal `10.128.0.16`, public `35.239.91.187`

## Objective
Replace the small/fixed-symbol market-data assumption with a dynamic Coinbase SPOT universe and resilient public WebSocket layer. Fondazione2 must discover, register, normalize and observe **all currently available Coinbase SPOT products/assets** without a hardcoded BTC/ETH/SOL whitelist.

This task is intentionally narrow. Do **not** start strategy optimization/research here. Strategy research becomes TASK-0005 after this task is certified.

## Safety invariants
- No wipe/rebuild.
- Reuse fail-closed target preflight.
- `TRADING_MODE=paper`.
- `LIVE_ENABLED=false`.
- `LIVE_ARMED=false`.
- `REAL_ORDERS_SENT=0`.
- Do not request/add/use private Coinbase trading credentials.
- Public WebSocket certification must work without a Coinbase private API key.
- Deploy in-place only from immutable GitHub-reachable commits.

## Coinbase protocol facts
Official Advanced Trade WebSocket docs state public channels include `heartbeats`, `status`, `ticker`, `ticker_batch`, `candles`, `market_trades`, and `level2` without mandatory authentication. Authentication may improve reliability but is not required for this task.

USDC rule: public non-user channels do not directly subscribe to normal `*-USDC` aliases; they return corresponding `*-USD` data. Coinbase documents `USDT-USDC` and `EURC-USDC` as direct-channel exceptions.

References:
- https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/websocket/websocket-channels
- https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/public/list-public-products

## Required implementation

### A. Dynamic Coinbase universe registry
Implement a canonical registry, name flexible, containing every discovered SPOT product and at least:
- `product_id`
- `product_type`
- `base_currency`
- `quote_currency`
- `canonical_asset`
- `canonical_symbol`
- `execution_product_id`
- `market_data_product_id`
- `market_data_is_proxy`
- alias / alias targets when available
- product status
- `is_disabled`
- `trading_disabled`
- `cancel_only`
- `limit_only`
- `post_only`
- increments/minimums useful for later execution validation
- `market_data_eligible`
- `paper_execution_eligible`
- explicit ineligibility reason
- discovery/update timestamps

No source-code coin or quote whitelist may define the universe.

### B. Discovery and refresh
Use public Coinbase data available without private trading credentials.

The Advanced Trade WS `status` channel must be a runtime source of product/currency state. A public Coinbase REST/Exchange catalog may be used for bootstrap/recovery.

Requirements:
- complete active SPOT catalog;
- pagination/cursor support where applicable;
- registry history for offline/disabled/delisted products;
- periodic refresh + status-driven updates;
- new listing enters automatically without code edit;
- disabled/delisted product becomes non-executable automatically;
- registry transitions auditable/observable.

### C. Public WebSocket service
Implement resilient Coinbase Advanced Trade WebSocket market data.

Required:
- `heartbeats` on every connection;
- `status` for product universe/state;
- real-time price/liveness for **all active SPOT products** via `ticker` / `ticker_batch` as appropriate;
- candles/richer channels integrated according to capacity;
- `level2` / `market_trades` may be dynamically tiered/sharded, but every active asset must remain in the universe and have a live price/liveness path;
- subscription batching/sharding;
- bounded exponential reconnect;
- automatic resubscription;
- heartbeat watchdog;
- `sequence_num` tracking where provided;
- sequence-gap observability;
- deterministic duplicate/out-of-order handling;
- stale-data detection;
- raw inbound `product_id` retained for audit.

### D. Generic product normalization
Preserve actual Coinbase execution pairs. Never invent `ASSET-USDC` if Coinbase does not offer it.

For each product explicitly model:
```text
canonical_asset
canonical_symbol
execution_product_id
market_data_product_id
market_data_is_proxy
quote_currency
```

For USDC proxy cases, inbound `ASSET-USD` market data must route to the correct canonical `ASSET/USDC` execution identity where Coinbase alias/protocol semantics require it.

Example only, never whitelist logic:
```text
canonical_symbol       = BTC/USDC
execution_product_id   = BTC-USDC
market_data_product_id = BTC-USD
market_data_is_proxy   = true
```

Use Coinbase metadata/alias graph wherever possible. Preserve direct `USDT-USDC` and `EURC-USDC` behavior. Unknown/ambiguous/conflicting mapping => fail closed + observable error. Never globally replace `USD` with `USDC`.

### E. Multi-quote valuation / eligibility
The universe includes products regardless of quote currency. Do not drop assets trading against USD/EUR/GBP/etc.

Portfolio valuation currency must be explicit/configurable, currently expected USDC for paper accounting.

Build/reuse a fresh quote-conversion graph from Coinbase products/marks:
- never silently assume USD == USDC;
- use current conversion products/marks;
- enforce freshness;
- if no safe fresh conversion path exists, product stays visible/streamed but `paper_execution_eligible=false` with reason;
- missing conversion must never zero another position.

### F. Existing pipeline integration
Feed the current TASK-0003 canonical path, not a second engine:

`Coinbase -> MarketSnapshot -> Kronos -> Nemotron -> Decision/Risk -> ExecutionIntent -> PaperExecutor -> PostgreSQL`

REST polling may remain bootstrap/recovery only. Normal runtime price/liveness after TASK-0004 must be WebSocket-driven.

### G. Observability
Expose safe aggregate metrics/events for:
- universe products total/active/disabled/execution-eligible;
- unique base assets;
- quote-currency distribution;
- WS connections/state;
- reconnects;
- heartbeat age;
- received messages/events;
- sequence gaps;
- stale products;
- normalization/proxy failures;
- subscription shards/count;
- product additions/removals/status transitions;
- quote-conversion failures.

Avoid unsafe high-cardinality Prometheus labels; use structured logs/PostgreSQL events for per-product detail.

## Required tests
Data-driven, not three-coin fixtures only:
1. arbitrary multi-product SPOT catalog discovered without hardcoded symbols;
2. new listing enters after refresh without code change;
3. disabled/trading-disabled product becomes non-executable;
4. generic `ASSET-USDC -> ASSET-USD market-data proxy -> ASSET/USDC` mapping when metadata/protocol supports it;
5. `USDT-USDC` direct mapping;
6. `EURC-USDC` direct mapping;
7. arbitrary non-USDC product such as `ASSET-EUR` preserves real pair;
8. ambiguous/unknown mapping fails closed;
9. reconnect -> automatic resubscribe;
10. heartbeat timeout -> unhealthy/reconnect;
11. stale event rejected from decision input;
12. duplicate/out-of-order/sequence-gap handling deterministic/observable;
13. fresh multi-quote conversion path works;
14. missing/stale conversion => visible but execution-ineligible/fail-closed;
15. TASK-0003 regression suite remains green;
16. certification requires no private Coinbase credentials.

## Runtime proof
On target VPS prove:
- discovered active SPOT universe is substantially larger than old BTC/ETH/SOL examples;
- report exact product count and unique base-asset count;
- show quote-currency distribution;
- show several mappings across different quote currencies;
- show generic USDC/USD proxy mapping from real metadata/events;
- show live WS events and heartbeat/liveness;
- show reconnect/resubscribe test;
- zero real orders.

## Acceptance gates
- target preflight PASS;
- all SPOT products returned by chosen public Coinbase discovery source represented with explicit active/inactive state;
- no hardcoded coin whitelist drives discovery;
- public WS receives live price/liveness for complete active SPOT price universe;
- listing/status changes update registry without source edit;
- generic USDC/USD normalization tested;
- non-USDC quotes modeled correctly;
- no silent USD=USDC assumption;
- reconnect/liveness/stale/sequence tests PASS;
- TASK-0003 regressions PASS;
- `TRADING_MODE=paper`;
- `LIVE_ENABLED=false`;
- `LIVE_ARMED=false`;
- `REAL_ORDERS_SENT=0`;
- no Coinbase private trading credential required/exposed;
- exact GitHub-reachable deployed code SHA and PR head SHA reported.

## Required reports
- `reports/TASK-0004-UNIVERSE.md`
- `reports/TASK-0004-WEBSOCKET.md`
- `reports/TASK-0004-VERIFY.md`

Final verdict:
```text
COINBASE_UNIVERSE_STATUS=READY_FOR_STRATEGY_RESEARCH
```
or
```text
COINBASE_UNIVERSE_STATUS=BLOCKED
```
