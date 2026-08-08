# TASK-0006 — Portfolio Allocator + Multi-Asset Engine

## Objective
Complete the shared-capital, multi-asset portfolio engine required before Strategy Runtime work. Strategy research remains suspended until TASK-0011 is complete.

## Safety baseline
- `TRADING_MODE=paper`
- `LIVE_ENABLED=false`
- `LIVE_ARMED=false`
- `REAL_ORDERS_SENT=0`
- no Coinbase private trading credentials
- no wipe
- preserve TASK-0003/TASK-0004/TASK-0005 behavior and regression contracts

## Canonical architecture

`StrategyProposal/Decision -> PortfolioAllocator -> RiskEngine -> ExecutionIntent -> Paper/Replay Executor -> PostgreSQL canonical state`

The allocator is deterministic infrastructure. It must never invent alpha, select assets, or increase risk beyond requested bounds.

## Requirements

### 1. Canonical portfolio model
Create a versioned PostgreSQL-backed portfolio state covering at minimum:
- base valuation currency (configurable; default USDC if existing config expects it);
- cash balances per quote currency;
- reserved cash/capital;
- positions per canonical symbol/execution product;
- quantity, average cost, realized/unrealized PnL;
- fresh market marks and conversion timestamps;
- gross exposure, net exposure, concentration, portfolio equity and drawdown;
- portfolio state version / reconstruction digest.

Do not maintain a second incompatible truth beside the existing canonical execution/ledger state. Normalize or migrate existing PAPER state deliberately and document compatibility.

### 2. Multi-quote valuation
Use TASK-0004 canonical product identity and a real fresh conversion graph.

Rules:
- never assume USD == USDC;
- no quote-currency whitelist;
- preserve actual execution pair and quote currency;
- conversion path must be fresh and auditable;
- stale/missing/ambiguous conversion makes a new allocation ineligible/fail-closed;
- stale conversion must not make an existing position disappear from state;
- exits/reductions remain possible under safe deterministic valuation rules where required for protection.

### 3. PortfolioAllocator contract
Define a stable deterministic contract with immutable input/output schemas.

Input must contain enough information to reproduce the allocation decision, including:
- proposal/decision identity;
- canonical symbol and execution product;
- requested action;
- requested risk fraction;
- requested position fraction/notional where applicable;
- current portfolio snapshot/version;
- fresh marks/conversions used;
- applicable portfolio/risk configuration.

Output must be exactly one of:
- `APPROVE`
- `MODIFY_DOWN`
- `REJECT`

and include:
- approved notional/quantity budget;
- reserved capital;
- reason codes;
- portfolio state/version used;
- deterministic allocation/audit hash.

Allocator may only preserve or reduce requested risk. It must never increase requested exposure.

### 4. Shared-capital allocation and reservations
Implement atomic capital reservation so simultaneous proposals cannot oversubscribe cash or risk budgets.

Required semantics:
- reservation occurs transactionally before entry ExecutionIntent finalization;
- reservation is committed/released deterministically on fill/reject/cancel/expiry;
- restart must reconcile orphan reservations;
- idempotent proposal/allocation identity;
- PostgreSQL transaction/concurrency proof, not only unit mocks.

### 5. Exposure and risk budgets
Enforce configurable portfolio-level controls independent of strategy logic:
- max position notional/fraction;
- max asset exposure;
- max quote-currency exposure;
- max gross exposure;
- max net exposure if applicable to supported semantics;
- max concentration;
- portfolio drawdown/risk budget;
- max number of concurrent positions where configured;
- hooks/interfaces for future correlation/group exposure without hardcoded asset sectors.

Entry-only restrictions must not block `REDUCE`, `CLOSE`, or protective exits.

### 6. Accounting invariants
Numerically prove:

`portfolio_equity = base_currency_cash + converted_other_cash + sum(marked_position_values)`

and maintain coherent:
- realized PnL;
- unrealized PnL;
- fees;
- reserved capital;
- available capital;
- drawdown / peak equity.

No double-counting of cost basis, fees, market value, or conversion.

### 7. Restart-safe reconstruction
Implement deterministic reconstruction from canonical PostgreSQL records/state.

Required proof:
1. establish multi-asset/multi-quote state;
2. capture canonical digest;
3. recreate engine/process equivalent;
4. reconstruct portfolio;
5. same balances, positions, reservations, exposure, equity and digest.

No hidden in-memory state may be required for correctness.

### 8. PAPER ↔ replay/backtest parity
TASK-0005 replay must consume the same portfolio/allocation contract and accounting semantics as PAPER wherever applicable.

Do not create a separate simplified allocator for backtest.

Minimum parity proof:
- same starting portfolio snapshot;
- same proposals/marks/conversions/config;
- same allocation decisions/reason codes/notionals;
- same accounting transitions, allowing only executor-specific fill details explicitly documented.

### 9. Audit/provenance
Persist enough data to reproduce every allocation:
- allocation id;
- proposal/decision id;
- portfolio state version/digest;
- marks/conversion provenance;
- config hash;
- code SHA;
- requested vs approved exposure;
- reason codes;
- reservation lifecycle;
- timestamp.

### 10. Observability
Expose bounded-cardinality metrics/health for:
- portfolio equity/cash/reserved cash;
- gross/net exposure;
- drawdown;
- active positions count;
- allocation approve/modify/reject totals;
- stale/missing conversion failures;
- reservation/reconstruction errors.

Avoid per-symbol metric labels across the full Coinbase universe unless explicitly bounded.

## Mandatory tests
At minimum:
- multi-asset shared cash accounting;
- generic USDC→USD market-data proxy valuation identity;
- direct USDT-USDC / EURC-USDC handling;
- EUR/GBP or other discovered quote conversion;
- no USD==USDC assumption;
- stale/missing conversion fail-closed for new entries;
- simultaneous entry proposals cannot oversubscribe cash;
- simultaneous proposals cannot exceed portfolio risk/gross exposure limits;
- allocator can only MODIFY_DOWN, never increase request;
- REDUCE/CLOSE/protective exit bypasses entry-only concentration/cooldown constraints;
- reservation release on reject/fill/cancel/expiry/restart reconciliation;
- exact portfolio equity/PnL/fee/reservation invariants;
- restart reconstruction digest parity;
- PAPER/replay allocator semantic parity;
- real PostgreSQL integration tests with `TEST_POSTGRES_URL`;
- TASK-0003/TASK-0004/TASK-0005 regressions;
- safety invariants.

Mandatory PostgreSQL gates must not be silently skipped in VPS certification.

## VPS certification
Target only the canonical `fondazione` VPS:
- instance `fondazione`
- zone `us-central1-a`
- internal `10.128.0.16`
- public `35.239.91.187`

Fail-closed preflight. Deploy in-place. No wipe.

Certify a representative multi-asset/multi-quote portfolio and at least one true concurrent-allocation test against PostgreSQL.

## Reports
Produce:
- `reports/TASK-0006-PORTFOLIO-MODEL.md`
- `reports/TASK-0006-ALLOCATOR.md`
- `reports/TASK-0006-RECONSTRUCTION.md`
- `reports/TASK-0006-VERIFY.md`

## Completion verdict
Exactly one:
- `PORTFOLIO_ENGINE_STATUS=READY_FOR_STRATEGY_RUNTIME`
- `PORTFOLIO_ENGINE_STATUS=BLOCKED`

## Out of scope
- strategy discovery/optimization/ranking
- final five strategies
- Strategy Runtime implementation (TASK-0007)
- scheduler/model orchestration (TASK-0008)
- final execution simulator semantics (TASK-0009)
- live Coinbase trading/private account APIs
