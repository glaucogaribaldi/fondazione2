# TASK-0002 — Fondazione2 Clean Rebuild

Status: `AUTHORIZED`

Tracking issue: #4

## Target

Fondazione trading VPS only.

Expected identity:

- hostname: `fondazione-1` / `fondazione-noble`
- public IPv4: `35.239.91.187`
- Tailscale IPv4 from TASK-0001: `100.96.230.80`
- Ubuntu 24.04
- NVIDIA L4 24 GB

If identity does not match, stop.

## Governing documents

Read and obey:

- `AGENTS.md`
- `docs/ARCHITECTURE_V0.md`
- `docs/DECISION_CONTRACT.md`
- `docs/SAFETY_CONTRACT.md`
- `docs/HISTORICAL_FAILURES_AND_TESTS.md`
- `docs/QUANTDINGER_INTEGRATION.md`
- `docs/WIPE_AND_REINSTALL_PLAN.md`
- all TASK-0001 reports merged at `6e3f97cd529d1387b050a190ae6b071c01ca622f`

## Objective

Build a reproducible installer first, then execute the operator-approved clean rebuild of the Fondazione VPS according to the versioned rebuild plan, and deploy a clean Fondazione2 paper-first baseline.

## Required baseline

- QuantDinger pinned to a verified immutable v5.0.15 reference or documented blocker;
- Kronos service with `NeoQuasar/Kronos-base`, CPU-first;
- Nemotron Nano 9B v2 through pinned SGLang on the L4;
- PostgreSQL canonical event ledger;
- Redis roles required by the runtime;
- Decision Aggregator;
- deterministic Risk Engine;
- Coinbase public market-data integration;
- realistic Paper Executor path;
- Coinbase live adapter interface present but disabled;
- System Agent and Strategy Agent control hooks.

## Sequence

1. Create and commit installer, compose/config, `.env.example`, preflight, verification and smoke-test tooling.
2. Validate component pins and target preflight.
3. Execute the approved clean rebuild only on the verified Fondazione target.
4. Install from an immutable Fondazione2 commit under `/opt/fondazione2`.
5. Bring services up incrementally and verify health.
6. Run the historical safety regression gates before declaring the paper baseline valid.
7. Publish a PR with full evidence and exact deployed commit.

## Mandatory safety state

```text
TRADING_MODE=paper
LIVE_ARMED=false
COINBASE_PRIVATE_CREDENTIALS_PRESENT=false
REAL_ORDERS_SENT=0
```

No Coinbase private credential is required or allowed in TASK-0002. The live executor must remain disabled.

## OpenClaw ownership

System Agent owns infrastructure/install/deploy/verification.

Strategy Agent may validate strategy interfaces and backtest hooks, but final strategy design is outside TASK-0002.

## Work branch

`openclaw/task-0002-clean-rebuild`

## Required reports

- `reports/TASK-0002-INSTALLER.md`
- `reports/TASK-0002-REBUILD.md`
- `reports/TASK-0002-DEPLOY.md`
- `reports/TASK-0002-VERIFY.md`

Include target identity, disk before/after, GPU status, component pins, service health, test results, deployed commit and paper-only proof.

Final verdict must be one of:

```text
PAPER_BASELINE_STATUS=READY_FOR_ENGINE_VALIDATION
```

or

```text
PAPER_BASELINE_STATUS=BLOCKED
```
