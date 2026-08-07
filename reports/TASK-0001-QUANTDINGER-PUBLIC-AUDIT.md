# TASK-0001 - QuantDinger Public Audit

Status: `PUBLIC_SOURCE_REVIEW_COMPLETE / HOST_VERIFICATION_PENDING`
Date: 2026-08-07

## Upstream

Repository reviewed: `https://github.com/OpenByteInc/QuantDinger`

Observed canonical source version on upstream `main`: `5.0.1`.

Upstream states that release tags use the same semantic version prefixed by `v`; therefore `v5.0.1` is the initial pin candidate. The System Agent must still verify the actual tag and resolve its immutable commit SHA before installation.

License observed: Apache License 2.0.

## Why QuantDinger fits Fondazione2

The current v5 architecture already provides several boundaries Fondazione2 needs:

- Strategy API V2;
- backtest and paper/live strategy lifecycle;
- PostgreSQL-backed durable state;
- dedicated trading worker;
- dedicated scheduler worker;
- Celery worker for finite jobs;
- separate cache Redis and durable job Redis;
- Agent Gateway and MCP;
- audit logs and request IDs;
- optional Prometheus/Grafana/Alertmanager;
- production hardening with non-root/read-only containers;
- exchange/broker and market-data extension points.

This makes QuantDinger a better substrate than rebuilding another custom Arena from scratch.

## Strategy API V2

Upstream documents Strategy API V2 as the current executable Python strategy contract. The same strategy source is compiled into a manifest used by backtest and live runtimes.

This is compatible with the Fondazione2 goal:

```text
strategy source
 -> backtest
 -> paper candidate
 -> paper runtime
 -> future live runtime
```

Fondazione2 must still enforce its own Decision Contract and deterministic final risk gate.

## Runtime ownership

Upstream v5 separates:

- `backend`: HTTP/auth/validation/durable command submission;
- `trading-worker`: strategy runtimes, pending orders, broker sessions and reconciliation;
- `scheduler-worker`: schedules and monitoring;
- `celery-worker`: finite AI/backtest/experiment/report jobs;
- `celery-beat`: periodic task dispatch;
- `migration`: schema migration before service start.

This separation is useful for eliminating old Fondazione concurrency ambiguity.

## Agent surfaces

QuantDinger exposes `/api/agent/v1` and an MCP server.

Upstream safety model includes scoped agent tokens and paper-only defaults. Live agent trading requires multiple explicit conditions, including server-side enablement.

Fondazione2 should use this surface for the Strategy Agent only after:

- a dedicated scoped token is created;
- `paper_only=true` is proven;
- no broker credential is visible to OpenClaw;
- all write actions are audit logged;
- strategy source remains in Git.

## Coinbase gap

The upstream README currently lists crypto adapters for Binance, OKX, Bitget, Bybit, Gate and HTX, plus adapter extensions. Coinbase is not listed as a built-in crypto adapter.

Therefore Fondazione2 should NOT assume native Coinbase support.

The official extension guide identifies these boundaries:

- market-data adapter: `app/data_sources` + `DataSourceFactory`;
- exchange/broker low-level calls: `app/services/live_trading` or broker package;
- strategy lifecycle must stay outside adapter code;
- adapter must normalize account, position, order, fill and error shapes;
- live adapters must document idempotency and retry behavior.

Recommendation: build a Coinbase Advanced extension rather than modifying strategy code around Coinbase-specific behavior.

## Security/hardening observations

Positive upstream properties:

- production containers can run non-root;
- read-only root filesystem and dropped capabilities are supported;
- secrets are expected outside public config;
- agent tokens are scoped and audit logged;
- long-running strategy ownership uses leases/heartbeats/fencing tokens;
- public host ports default to loopback;
- high-risk APIs have OpenAPI/test coverage.

Fondazione2 should retain these controls instead of weakening the QuantDinger production overlay.

## Storage

QuantDinger itself uses PostgreSQL for durable state. Fondazione2 should avoid creating a second unrelated canonical trade ledger when possible.

Target design:

- QuantDinger operational tables remain upstream-owned;
- Fondazione2 adds dedicated event/audit tables through explicit migrations/schema ownership;
- no SQLite canonical ledger;
- market/model/risk/execution IDs correlate across both layers.

## Upstream modifications policy

Prefer extension modules and adapters over invasive forks.

If a fork becomes necessary:

- pin upstream commit;
- maintain a small patch set;
- document every modified upstream file;
- run upstream tests plus Fondazione2 contract tests;
- preserve Apache-2.0 notices/requirements.

## Pin recommendation

Initial candidate:

```text
QuantDinger source version: 5.0.1
candidate tag: v5.0.1
```

Final pin status: `PENDING_SYSTEM_AGENT_VERIFICATION`.

The System Agent must resolve:

```text
v5.0.1 -> exact commit SHA
```

and verify that the checked-out `VERSION` remains `5.0.1` before installer implementation.

## Open questions requiring machine/code verification

1. Exact tag commit SHA.
2. Docker image tags/digests to pin.
3. Actual memory/CPU footprint on the target VPS.
4. Interaction with the existing NVIDIA stack.
5. Exact Strategy API V2 APIs to bridge into the Fondazione2 Decision Contract.
6. Coinbase adapter extension tests against the selected pinned source.
7. Whether all required frontend assets are available at the pin without depending on floating image tags.

## Recommendation

`USE_AS_SUBSTRATE`

Do not use QuantDinger as the final autonomous authority for Coinbase orders. Use its strategy/backtest/runtime infrastructure and connect its intents to Fondazione2's deterministic risk and execution contracts.
