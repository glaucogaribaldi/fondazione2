# TASK-0003 — Decision pipeline integration & realistic Coinbase paper loop

**Agent:** `fondazione2-system-agent`

## Objective
Turn the validated TASK-0002 baseline into a repeatable, observable end-to-end PAPER loop:

Coinbase public market data → canonical MarketSnapshot → Kronos → Nemotron → Decision Aggregator → deterministic Risk Engine → ExecutionIntent → PostgreSQL-backed PaperExecutor → canonical PostgreSQL audit/event state → QuantDinger read/integration layer.

This task validates the engine. It does **not** choose or optimize the five final strategies.

## Hard safety constraints
- Authorized target only:
  - GCP instance: `fondazione`
  - zone: `us-central1-a`
  - internal IP: `10.128.0.16`
  - public IP: `35.239.91.187`
- Run the TASK-0002 fail-closed GCE identity preflight before deploy/test.
- `TRADING_MODE=paper`
- `LIVE_ENABLED=false`
- `LIVE_ARMED=false`
- No Coinbase private credentials.
- `REAL_ORDERS_SENT=0`.
- No wipe/reinstall. Deploy in-place from immutable GitHub-reachable commits only.
- PostgreSQL remains the canonical ledger/event store.
- PaperExecutor remains the only executable trading path.
- Do not create an alternative QuantDinger ledger/accounting source.

## Required implementation
1. Build a repeatable orchestration loop using fresh Coinbase public ticker/candles/product metadata and canonical symbol mapping, keeping `execution_product_id`, `market_data_product_id`, and `market_data_is_proxy` distinct.
2. Construct and persist a canonical market snapshot with freshness checks. Stale/missing data must fail closed to `NO_TRADE`.
3. Feed the same immutable snapshot to the real Kronos backend and the real Nemotron/SGLang backend.
4. Enforce bounded model behavior:
   - Kronos forecasts only.
   - Nemotron may criticize/reduce/abstain but cannot bypass deterministic risk or increase risk beyond strategy/risk limits.
   - Any timeout, invalid schema, model failure, stale dependency, or missing output => deterministic `NO_TRADE`.
5. Normalize all model/aggregator outputs to Decision Contract v0: `NO_TRADE | OPEN | ADD | REDUCE | CLOSE`.
6. Run every non-`NO_TRADE` proposal through the deterministic Risk Engine, which may only `APPROVE | MODIFY_DOWN | REJECT | PROTECTIVE_EXIT` and must never increase requested risk.
7. Convert approved risk decisions into one canonical `ExecutionIntent`; execute only through PaperExecutor.
8. Make paper execution realistic and auditable: bid/ask-aware fill reference, configured fee/slippage assumptions, symbol-specific fresh marks, SL/TP/protective exits, idempotent `client_order_id`, restart-safe reconciliation, atomic portfolio limits and fail-closed freshness.
9. Persist the full causal chain in PostgreSQL: market snapshot reference, Kronos output, Nemotron output, aggregator decision, risk decision, ExecutionIntent, ExecutionResult, reason codes, market marks and portfolio/equity state.
10. Integrate QuantDinger as consumer/orchestrator/analytics layer over canonical state without duplicate execution/accounting truth.
11. Add health/readiness/metrics for Coinbase freshness, Kronos/Nemotron reachability, inference/decision latency, model errors/timeouts, stale data, rejects, fills, equity, realized/unrealized PnL and drawdown.
12. Add a controlled validation harness capable of running a short real-market PAPER session on `fondazione` and producing a reproducible causal trace.

## Required tests / gates
- GCE authorized-target preflight PASS.
- Real Coinbase public product/ticker/candles smoke PASS.
- Real Kronos backend reachable and produces structured forecast from the same market snapshot.
- Real Nemotron/SGLang backend reachable and produces structured bounded output.
- Model timeout/error/invalid output => persisted `NO_TRADE` with reason code.
- Symbol proxy regression covers `BTC-USDC` execution vs `BTC-USD` market-data proxy without discarding ticks or confusing execution product.
- At least one complete end-to-end PAPER cycle is persisted from market data through final decision/execution result. A valid `NO_TRADE` cycle satisfies this gate if all components actually ran and the reason chain is persisted.
- If a paper trade occurs, prove fee/slippage/MTM/protective-exit/idempotency/restart semantics through PostgreSQL.
- Restart the decision/execution worker and prove no duplicate execution for the same `client_order_id`.
- PostgreSQL isolated certification DB remains separate from runtime DB; runtime ledger integrity PASS.
- QuantDinger does not create duplicate orders or a second accounting truth.
- Safety check: `paper / live_enabled=false / live_armed=false`, no private Coinbase credentials, zero real orders.
- All code/report references use GitHub-reachable immutable SHAs.

## Required reports
Publish:
- `reports/TASK-0003-PIPELINE.md`
- `reports/TASK-0003-PAPER-RUN.md`
- `reports/TASK-0003-VERIFY.md`

Reports must contain exact deployed SHA, target identity evidence, component health, real Coinbase/Kronos/Nemotron evidence, an end-to-end causal trace, test results, restart/idempotency evidence, QuantDinger integration evidence and final safety state.

## Completion flags
Final verdict must contain exactly one of:

`DECISION_PIPELINE_STATUS=READY_FOR_STRATEGY_RESEARCH`

or

`DECISION_PIPELINE_STATUS=BLOCKED`

And always include:

`TRADING_MODE=paper`
`LIVE_ENABLED=false`
`LIVE_ARMED=false`
`REAL_ORDERS_SENT=0`

## Delivery
Work on branch `openclaw/task-0003-decision-pipeline-paper-loop`, update Issue #7 with evidence, and open a final PR to `main`.

Proceed autonomously until all gates are green or a genuine technical blocker is reached.