# TASK-0003 — Decision Pipeline Integration & Realistic Coinbase Paper Loop

Status: `AUTHORIZED`

Tracking issue: #7

## Target

Fondazione trading VPS only.

Authoritative identity:

- GCE instance name: `fondazione`
- zone: `us-central1-a`
- internal IPv4: `10.128.0.16`
- public IPv4: `35.239.91.187`
- Tailscale IPv4: `100.96.230.80`

Run the fail-closed target identity gate from TASK-0002 before any deployment action. If identity does not match, stop.

## Governing baseline

TASK-0002 merged to `main` in PR #6 and is the required baseline. Preserve its safety contracts, PostgreSQL canonical ledger, PaperExecutor semantics, isolated test database, immutable deployment trace, Coinbase public adapter, Kronos, Nemotron/SGLang and QuantDinger components.

Read and obey:

- `AGENTS.md`
- `docs/ARCHITECTURE_V0.md`
- `docs/DECISION_CONTRACT.md`
- `docs/SAFETY_CONTRACT.md`
- `docs/HISTORICAL_FAILURES_AND_TESTS.md`
- `docs/QUANTDINGER_INTEGRATION.md`
- TASK-0002 reports and tests merged through PR #6

## Objective

Turn the validated baseline into one observable, reproducible, end-to-end PAPER decision pipeline:

`Coinbase public market data -> canonical MarketSnapshot -> Kronos -> Nemotron -> Decision Aggregator -> deterministic Risk Engine -> ExecutionIntent -> PaperExecutor -> PostgreSQL -> QuantDinger/metrics`

This task validates the engine and data flow. It does **not** define, optimize, rank or finalize the five production strategies.

## Required implementation

1. Implement a repeatable orchestration loop that fetches fresh Coinbase public ticker/candles and creates canonical market snapshots with timestamps and freshness checks.
2. Feed the same immutable snapshot into Kronos and Nemotron so their outputs can be traced to identical source data.
3. Require structured model outputs, explicit timeouts and fail-closed behavior. Any Kronos/Nemotron error, invalid payload, unavailable backend or stale source data must resolve safely to `NO_TRADE` with persisted reason codes.
4. Normalize all strategic actions through Decision Contract v0: `NO_TRADE`, `OPEN`, `ADD`, `REDUCE`, `CLOSE`.
5. Run approved proposals through the deterministic Risk Engine before creating an `ExecutionIntent`.
6. Execute only in the PostgreSQL-backed PaperExecutor. No alternate execution or accounting ledger is permitted.
7. Improve paper realism without live credentials: bid/ask-aware reference price, configured fee/slippage assumptions, per-symbol fresh marks, protective exits, idempotent client order IDs and restart-safe reconciliation.
8. Persist the complete causal chain in PostgreSQL: market snapshot reference, model outputs, aggregator result, risk result, execution intent/result, reason codes, fills, fees/slippage, marks, portfolio state and equity.
9. Integrate QuantDinger as a consumer/control surface of canonical state and events; it must not duplicate execution or accounting truth.
10. Add health/readiness/metrics for model reachability, decision latency, stale market data, model failures/timeouts, risk rejections, fills, equity and drawdown.
11. Provide a controlled short paper-run harness on the authorized VPS and generate reproducible evidence.

## Mandatory safety state

```text
TRADING_MODE=paper
LIVE_ENABLED=false
LIVE_ARMED=false
COINBASE_PRIVATE_CREDENTIALS_PRESENT=false
REAL_ORDERS_SENT=0
```

No Coinbase private credentials may be added. The live executor remains disarmed and must not send orders.

## Non-goals

- no live trading;
- no private Coinbase API credentials;
- no wipe/rebuild of the VPS;
- no final design or optimization of the five strategies;
- no production capital allocation;
- no alternative SQLite/runtime ledger.

## Deployment rules

- work on branch `openclaw/task-0003-decision-pipeline`;
- deploy in-place only after target preflight PASS;
- deployment must use a GitHub-reachable immutable commit;
- do not destroy the TASK-0002 canonical PostgreSQL ledger;
- destructive integration tests must use an isolated test database and must fail closed if isolation cannot be proven.

## Acceptance gates

1. Target identity preflight PASS for exactly the authorized GCP instance.
2. Kronos real backend processes real Coinbase public data and returns a valid structured forecast.
3. Nemotron real SGLang backend processes the same snapshot and returns a valid structured result; timeout/error path proves deterministic `NO_TRADE`.
4. At least one complete end-to-end PAPER cycle is persisted from market snapshot through final decision/execution audit. A valid `NO_TRADE` cycle is acceptable; forced trading is prohibited.
5. Any actual PAPER fill uses PaperExecutor only and records realistic fee/slippage and symbol-specific mark-to-market state.
6. PostgreSQL remains canonical event/accounting truth; QuantDinger does not create a competing ledger.
7. Stale-data, model-failure, restart/idempotency and protective-exit regression tests PASS.
8. No committed secrets or private Coinbase credentials.
9. Runtime proves `TRADING_MODE=paper`, `LIVE_ENABLED=false`, `LIVE_ARMED=false`, `REAL_ORDERS_SENT=0`.
10. Reports and deployment reference exact immutable GitHub-reachable SHAs.

## Required reports

- `reports/TASK-0003-PIPELINE.md`
- `reports/TASK-0003-PAPER-RUN.md`
- `reports/TASK-0003-VERIFY.md`

Reports must include target identity evidence, deployed SHA, service/model health, Coinbase public-data evidence, one complete causal decision trace, persistence evidence, test results, QuantDinger integration evidence and mandatory safety state.

Final verdict must be one of:

```text
DECISION_PIPELINE_STATUS=READY_FOR_STRATEGY_RESEARCH
```

or

```text
DECISION_PIPELINE_STATUS=BLOCKED
```
