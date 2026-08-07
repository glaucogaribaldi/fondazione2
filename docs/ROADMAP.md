# Fondazione2 Roadmap

## Phase 0 - Architecture bootstrap

Status: `IN_PROGRESS`

Deliverables:

- agent governance;
- safety contract;
- architecture v0;
- QuantDinger boundary;
- decision/execution contracts;
- historical regression requirements;
- wipe gate;
- first OpenClaw audit task.

No VPS mutation.

## Phase 1 - Evidence and dependency pinning

OpenClaw TASK-0001:

- host inventory;
- QuantDinger upstream audit and pin;
- Kronos pin;
- Nemotron/SGLang pin;
- Coinbase adapter design;
- OpenClaw capability verification.

No VPS mutation.

## Phase 2 - Reproducible installer

Create:

- Docker/Compose topology;
- local secret bootstrap;
- PostgreSQL migrations;
- Redis topology required by QuantDinger;
- QuantDinger pinned install;
- Kronos service;
- Nemotron/SGLang service;
- Coinbase public market adapter;
- health/readiness;
- non-financial smoke tests;
- installer preflight/dry-run.

Still no wipe until acceptance passes.

## Phase 3 - Clean VPS rebuild

After explicit operator gate:

- remove old Fondazione application state without backup;
- clean install Fondazione2;
- no old ledger/run/memory migration;
- verify GPU/services/database;
- prove `LIVE_ARMED=false`.

## Phase 4 - Decision and realistic paper core

Implement:

- StrategyIntent;
- KronosForecast;
- NemotronPolicy;
- DecisionCandidate;
- Deterministic Risk Engine;
- Portfolio Allocator;
- ExecutionIntent;
- Paper Executor;
- PostgreSQL event ledger;
- price registry/freshness;
- reconciliation/idempotency.

All historical failure acceptance tests must pass.

## Phase 5 - Strategy Agent laboratory

Implement Strategy Agent workflow and tooling:

- strategy scaffolding;
- Strategy API V2 integration;
- test generation/execution;
- backtest;
- benchmark;
- ablation;
- walk-forward/OOS;
- paper candidate packaging;
- deploy handoff to System Agent;
- forward-test reporting.

## Phase 6 - Five strategy redesign

The previous five lanes are not migrated.

Create new candidates only after the engine is valid. Initial research families may include the strategies suggested by the historical audit, but none is canonical until independently reviewed and tested.

## Phase 7 - Paper forward trial

Run real-time Coinbase market data with realistic paper execution.

Requirements before scientific conclusions:

- stable runtime;
- strategy/version freeze;
- cost model documented;
- benchmark/ablation;
- sufficient observation period/trade count chosen before evaluating results;
- immutable audit data.

## Phase 8 - Live adapter certification

Only after paper evidence:

- private Coinbase adapter integration;
- restricted credential model;
- shadow execution;
- order reconciliation;
- kill switch;
- live-specific limits;
- operator review.

## Phase 9 - Explicit live enablement

Separate task and change set.

No strategy or agent may infer authorization from successful paper tests.
