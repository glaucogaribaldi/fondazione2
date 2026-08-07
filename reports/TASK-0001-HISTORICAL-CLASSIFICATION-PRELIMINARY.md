# TASK-0001 - Historical Fondazione Classification (Preliminary)

Status: `PRELIMINARY / OPENCLAW CODE-AUDIT_PENDING`
Date: 2026-08-07

Source repository: `glaucogaribaldi/fondazionesemplice`
Historical latest observed commit: `a0633cb737e25fb29897b05e6b7cfc1965c5d373`
Audit reference: `755e0ba81a4dce4eb86101d4b19821ca45934ad2`

## Policy

Fondazione2 is not a refactor branch of Fondazione Semplice. Default classification is `DROP` or `REUSE_CONCEPT`, not code copy.

The System Agent must verify this classification against the full source tree before installer implementation.

## Classification

| Historical surface | Classification | Fondazione2 treatment |
|---|---|---|
| Kronos model/runtime knowledge | `REUSE_CONCEPT` | Preserve known working model/bootstrap lessons, re-pin and rebuild service cleanly. |
| Nemotron Nano 9B v2 + SGLang runtime knowledge | `REUSE_CONCEPT` | Preserve deployment/GPU lessons, re-pin model/runtime and build a bounded policy service. |
| Old Decision Service orchestration | `DROP` | Replace with explicit StrategyIntent/KronosForecast/NemotronPolicy/DecisionCandidate contracts. |
| Old Arena | `DROP` | Do not migrate. QuantDinger runtime + new paper/execution contracts replace it. |
| SQLite `arena.db` ledger | `DROP` | No migration; PostgreSQL/event audit becomes canonical. |
| Old five lane configs | `REFERENCE_ONLY` | Keep only as historical experiment design. New strategies are rebuilt later. |
| Generic Nemotron prompt shared by lanes | `DROP` | Strategy-specific versioned policies required. |
| `quant_proposal` fixed 8% logic | `DROP` | No role in new decision system. |
| Bootstrap probe / forced paper BUY | `DROP` | Replace with non-financial connectivity/simulation tests. |
| Smoke-test isolation lesson | `REUSE_CONCEPT` | Mandatory test namespaces/databases; synthetic events never touch forward-paper ledger. |
| Risk Engine safety principle | `REUSE_CONCEPT` | Reimplement deterministic final gate with stricter typed semantics and atomic reservations. |
| Existing Risk Engine code | `PORT_WITH_REWRITE` at most | Use only as reference for tests/reason codes; do not assume code is safe to copy. |
| Old BUY/SELL allocation semantics | `DROP` | Replace with OPEN/ADD/REDUCE/CLOSE contracts. |
| Stop/take metadata behavior | `DROP` | Protection must be persisted/executable and independent of model availability. |
| Old cooldown semantics | `DROP` | Entry cooldown must never block risk-reducing exits. |
| Old mark-to-market implementation | `DROP` | Use fresh multi-asset price registry/event state. |
| Old score/ranking formula | `DROP` | Metrics redesigned after paper engine is economically correct. |
| Old Coinbase feed mapping assumptions | `DROP` | Adapter must retain execution product vs market-data source identity and test USD/USDC behavior. |
| Docker/NVIDIA bootstrap scripts | `PORT_WITH_REWRITE` | Extract working host/GPU techniques only after line-by-line review. No destructive script copied blindly. |
| Public dashboard/Grafana concepts | `REUSE_CONCEPT` | New observability may leverage QuantDinger v5 + Fondazione2 domain metrics. |
| Old OpenClaw install skill | `REFERENCE_ONLY` | Preserve explicit destructive gate idea; replace with Fondazione2 System/Strategy Agent skills. |
| Old paper-to-live checklist | `REFERENCE_ONLY` | Useful concepts, but new live gate must match Coinbase adapter and QuantDinger v5. |
| Existing tests for known bugs | `PORT_WITH_REWRITE` | Convert scenarios to new HST acceptance tests, not old implementation assertions. |
| Historical paper results/ranking | `DROP_AS_EVIDENCE` | Do not use to estimate edge or compare new strategy performance. |
| Historical project documents/audit | `REFERENCE_ONLY` | Preserve for provenance and regression requirements. |

## Components intentionally not migrated

The new host should not import:

- SQLite databases;
- lane balances;
- positions;
- fills;
- drawdowns/rankings;
- bootstrap state;
- old `.env` application config;
- old Fondazione OpenClaw memories;
- old strategy runtime state.

## Model-specific note

Keeping Kronos and Nemotron means keeping their **roles and validated deployment knowledge**, not keeping the old decision architecture around them.

Target:

```text
Kronos -> structured forecast
Nemotron -> bounded critic/policy
QuantDinger -> strategy intent/research/runtime
Fondazione2 Risk -> final deterministic authorization
```

## Required OpenClaw follow-up

The System Agent should inspect the full historical tree and either confirm this table or record exceptions with:

- exact file/path;
- reason to reuse;
- dependency/security implications;
- required rewrite scope;
- tests that prove safe behavior.

No exception may justify migrating old runtime state.
