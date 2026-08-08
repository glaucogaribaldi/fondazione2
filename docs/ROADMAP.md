# Fondazione2 Roadmap

## Governing objective

Fondazione2 must reach **ENGINE 1.0 — PRODUCTION_READY_FOR_RESEARCH** before strategy discovery, optimization, ranking, or final lane selection resumes.

Strategy research is suspended until TASK-0011 is complete. Infrastructure tasks must remain PAPER-only unless a later, explicit operator authorization changes that.

Permanent safety baseline until then:

- `TRADING_MODE=paper`
- `LIVE_ENABLED=false`
- `LIVE_ARMED=false`
- `REAL_ORDERS_SENT=0`
- no Coinbase private trading credentials required

---

## Completed foundation

### TASK-0001 — Host / dependency / architecture audit — COMPLETE
Established authoritative host identity, dependency pins, QuantDinger boundary, Coinbase adapter design, OpenClaw capabilities, historical failure catalogue, and rebuild recommendation.

### TASK-0002 — Clean rebuild — COMPLETE
Built and deployed the reproducible Fondazione2 runtime on the canonical `fondazione` VPS with PostgreSQL, Redis, QuantDinger, Kronos, Nemotron/SGLang, deterministic risk, Paper Executor, safety controls, and target preflight.

### TASK-0003 — Decision pipeline + realistic PAPER loop — COMPLETE
Completed real Coinbase public market-data decision flow, canonical market snapshots, Kronos/Nemotron integration, deterministic NO_TRADE failure paths, realistic paper execution, mark-to-market, PnL/drawdown, PostgreSQL causal audit, reconciliation, and paper safety certification.

### TASK-0004 — Dynamic Coinbase SPOT universe + WebSocket normalization — COMPLETE
Added dynamic full Coinbase SPOT discovery, normalized execution-vs-market-data identities, generic USDC→USD public-feed proxying where required, arbitrary quote preservation, resilient WebSocket ingestion, liveness/reconnect/resubscribe, sequence handling, quote-conversion eligibility, dynamic listing/status updates, and canonical mark routing.

---

# ENGINE 1.0 completion sequence

## TASK-0005 — Canonical Data + Historical Market Data + Backtest Foundation

Build the canonical historical/data replay layer before any strategy research:

- versioned PostgreSQL historical OHLCV truth;
- dynamic full-universe Coinbase backfill;
- restart-safe/idempotent checkpoints;
- gap detection and targeted recovery;
- historical/live identity continuity;
- immutable dataset/as-of contract;
- no-lookahead data access;
- deterministic replay/backtest foundation;
- isolated backtest ledger;
- dataset/config/code provenance and reproducible digests.

Completion gate:

`ENGINE_DATA_STATUS=READY_FOR_PORTFOLIO_ENGINE`

## TASK-0006 — Portfolio Allocator + Multi-Asset Engine

Complete portfolio-level accounting and capital allocation independently of strategy logic:

- shared-capital multi-asset portfolio;
- cash and quote-currency accounting;
- exposure by asset / quote / portfolio;
- position and concentration limits;
- correlation/exposure hooks;
- portfolio-level drawdown and risk budget;
- deterministic allocator contract;
- restart-safe portfolio reconstruction;
- PAPER/backtest semantic parity.

Completion gate:

`PORTFOLIO_ENGINE_STATUS=READY_FOR_STRATEGY_RUNTIME`

## TASK-0007 — Strategy Runtime API + Experiment Framework

Create the stable plugin boundary strategies will later use:

`CanonicalSnapshot -> StrategyPlugin -> StrategyProposal -> Allocator -> Risk -> ExecutionIntent`

Include:

- versioned Strategy Runtime API;
- strategy sandboxing/bounds;
- immutable proposal schema;
- experiment IDs;
- parameter/config versioning;
- dataset/model/code provenance;
- deterministic seeds;
- artifact/result registry;
- reproducible experiment manifests.

No real alpha strategy selection in this task.

Completion gate:

`STRATEGY_RUNTIME_STATUS=READY_FOR_ORCHESTRATION`

## TASK-0008 — Runtime Scheduler + Model Orchestration

Make the engine capable of operating safely across the full dynamic universe:

- bounded concurrency;
- asset/job prioritization;
- rate-limit management;
- timeout/cancellation/recovery;
- Kronos/Nemotron queues and batching;
- GPU memory/health controls;
- model version and latency tracking;
- deterministic fallback/fail-closed behavior;
- scheduler restart recovery;
- no uncontrolled fan-out across hundreds of assets.

Completion gate:

`RUNTIME_ORCHESTRATOR_STATUS=READY_FOR_EXECUTION_1_0`

## TASK-0009 — Execution Simulator 1.0 + Coinbase Constraints

Finish execution semantics before strategy evaluation:

- Coinbase product increments/minimums;
- bid/ask and spread model;
- maker/taker fee model;
- slippage model;
- latency;
- partial fills;
- reject/cancel semantics;
- idempotent retry and reconciliation;
- restart-safe order state;
- paper/backtest execution parity;
- stable interface for future live adapter.

Completion gate:

`EXECUTION_ENGINE_STATUS=READY_FOR_OPERATIONS`

## TASK-0010 — Operations + CI/CD + Backup/Restore + Observability

Turn the engine into an operable system:

- database migrations;
- pinned/reproducible deployment;
- CI gates and regression suite;
- service boot/restart policy;
- PostgreSQL backup and tested restore;
- rollback procedure;
- logs/rotation/disk monitoring;
- secrets handling;
- health/readiness for every component;
- technical control-plane/dashboard;
- alerts for Coinbase, DB, models, queues, executions, PnL/drawdown, and stale data.

Completion gate:

`OPERATIONS_STATUS=READY_FOR_SOAK_CERTIFICATION`

## TASK-0011 — Full-system Soak / Chaos / Restart Certification

Final ENGINE 1.0 certification under realistic PAPER operation.

Exercise and prove recovery from:

- Coinbase disconnect/reconnect;
- new listing/delisting;
- container restart;
- full VPS restart;
- PostgreSQL transient failure;
- Kronos failure;
- Nemotron failure;
- stale market data;
- queue pressure;
- simultaneous multi-asset operation;
- backfill/replay interruption;
- no duplicate execution;
- no lost or contradictory canonical ledger state.

Final engine gate:

```text
FONDAZIONE2_ENGINE_VERSION=1.0
ENGINE_STATUS=PRODUCTION_READY_FOR_RESEARCH
TRADING_MODE=paper
LIVE_ENABLED=false
LIVE_ARMED=false
REAL_ORDERS_SENT=0
```

---

# Research begins only after ENGINE 1.0

## TASK-0012 — Strategy Research & Benchmarking

Only after TASK-0011 passes:

- candidate generation;
- QuantDinger baseline;
- deterministic/rule baselines;
- Kronos-only candidates;
- bounded Kronos+Nemotron candidates;
- backtest;
- walk-forward;
- out-of-sample;
- benchmark and ablation;
- realistic Coinbase cost model;
- paper-candidate packaging.

The final five strategies are not predetermined.

## Later — Paper Arena / Forward Trial

Run selected candidates on the same live market, capital rules, execution engine, and observation period with immutable reporting.

## Later — Live Adapter Certification

Only after adequate paper evidence and a separate explicit operator decision:

- private Coinbase adapter;
- restricted credential model;
- shadow execution;
- reconciliation;
- kill switch;
- live-specific limits.

## Later — Explicit Live Enablement

Separate task and explicit human authorization. No agent may infer permission to trade real funds from successful tests or paper results.
