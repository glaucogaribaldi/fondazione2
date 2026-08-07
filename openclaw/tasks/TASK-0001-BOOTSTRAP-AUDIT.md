# TASK-0001 - Fondazione2 Bootstrap Audit

Status: `READY_AFTER_MERGE`
Owner: `fondazione2-system-agent` with `fondazione2-strategy-agent` for strategy surfaces
Destructive actions: `FORBIDDEN`
VPS changes: `READ_ONLY_ONLY`

## Objective

Prepare the evidence required to build the first reproducible Fondazione2 installer. Do not wipe, stop, restart or modify the current Fondazione VPS in this task.

## Inputs

### Fondazione2

Repository: `https://github.com/glaucogaribaldi/fondazione2`
Read the merged commit containing this task.

### Historical source

Repository: `https://github.com/glaucogaribaldi/fondazionesemplice`
Reference current historical commit: `a0633cb737e25fb29897b05e6b7cfc1965c5d373`
Audit reference: `755e0ba81a4dce4eb86101d4b19821ca45934ad2`

Do not modify the historical repository.

### QuantDinger upstream

Repository: `https://github.com/OpenByteInc/QuantDinger`

Do not install `main` blindly. Determine current release/tag/commit candidates and recommend one immutable ref after reviewing architecture, Strategy API V2, agent gateway, exchange/broker extension model, PostgreSQL/Redis topology, Docker deployment and licensing.

## Workstream A - Historical source classification

Create a report classifying relevant `fondazionesemplice` components as:

- `REFERENCE_ONLY`;
- `REUSE_CONCEPT`;
- `PORT_WITH_REWRITE`;
- `DROP`.

Default bias: do not copy code.

Pay special attention to:

- Kronos deployment/model pin;
- Nemotron/SGLang deployment/model pin;
- Docker/GPU bootstrap techniques;
- tests that can become regression tests;
- observability;
- OpenClaw integration;
- anything coupled to old Arena/SQLite/bootstrap probe.

## Workstream B - QuantDinger upstream audit

Verify from code/docs, not marketing text:

- current version/ref;
- architecture/process roles;
- Strategy API V2 lifecycle;
- backtest execution assumptions;
- paper runtime behavior;
- live runtime boundaries;
- adapter interfaces;
- agent gateway/MCP scopes;
- state stored in PostgreSQL;
- Redis roles;
- worker ownership/leases/fencing;
- secrets handling;
- observability;
- test/CI surfaces;
- license obligations.

Identify the exact extension points required for Coinbase Advanced.

## Workstream C - Coinbase adapter design

Without private credentials, produce a design for:

### public data

- product discovery/master data;
- candles;
- ticker;
- best bid/ask;
- optional L2;
- precision/increments;
- min sizes/notional;
- status flags;
- symbol normalization;
- freshness rules.

### future private execution

Design only, do not connect:

- account/balance;
- create/cancel order;
- order status;
- fills;
- client order id/idempotency;
- retry/reconciliation;
- permissions required;
- explicit exclusion of transfer/withdrawal flows.

## Workstream D - Host inventory

Read-only inventory of the machine(s) intended for Fondazione2:

- OS;
- CPU/RAM;
- GPU/VRAM/driver;
- disk layout/free space;
- Docker/Compose;
- Tailscale identity/connectivity;
- current Fondazione containers/services;
- ports;
- existing OpenClaw version/workspaces/agents relevant to Fondazione.

Do not expose secrets. Do not alter services.

## Workstream E - OpenClaw capability check

Verify the actual installed OpenClaw version and official available interfaces for:

- dedicated agent/workspace;
- skills;
- persistent memory/session;
- GitHub task workflow;
- command/tool restrictions;
- service autostart;
- audit/report persistence.

Do not invent commands that the installed version does not support.

## Required outputs

Commit reports to a new branch in Fondazione2:

- `reports/TASK-0001-HOST-INVENTORY.md`
- `reports/TASK-0001-HISTORICAL-CLASSIFICATION.md`
- `reports/TASK-0001-QUANTDINGER-AUDIT.md`
- `reports/TASK-0001-COINBASE-ADAPTER-DESIGN.md`
- `reports/TASK-0001-OPENCLAW-CAPABILITY.md`
- `reports/TASK-0001-RECOMMENDATION.md`

The final recommendation must include:

1. pinned QuantDinger candidate;
2. pinned Kronos candidate;
3. pinned Nemotron/SGLang candidate;
4. expected disk/VRAM/RAM requirements;
5. installer implementation plan;
6. blockers;
7. explicit statement `VPS_UNCHANGED=true`.

## Git workflow

Create a branch such as:

`openclaw/task-0001-bootstrap-audit`

Commit only reports/docs. Open a PR to `main` or the current integration branch specified by the operator.

## Stop conditions

Return `BLOCKED` rather than guessing if:

- host identity is ambiguous;
- upstream ref cannot be verified;
- access is unavailable;
- private secrets would be required;
- accomplishing the task would require modifying the VPS.

No wipe. No deploy. No service restart. No Coinbase private connection.
