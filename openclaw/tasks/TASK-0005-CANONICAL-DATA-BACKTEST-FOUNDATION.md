# TASK-0005 — Canonical Data + Historical Market Data + Backtest Foundation

GitHub Issue: #12

## Mission

Build the canonical historical-data and deterministic replay foundation required for Fondazione2 ENGINE 1.0.

This is an infrastructure task. Strategy research is suspended. Do not invent, optimize, rank, benchmark, or promote trading strategies.

## Authoritative baseline

Start from current `main` after merged TASK-0004.

Preserve all TASK-0003 and TASK-0004 contracts:

- dynamic full Coinbase SPOT universe;
- canonical symbol identity;
- separate `execution_product_id` and `market_data_product_id`;
- explicit `market_data_is_proxy`;
- generic public-feed USDC→USD proxy behavior where Coinbase requires it;
- direct USDT-USDC / EURC-USDC exceptions;
- arbitrary quote currencies;
- dynamic quote conversion to portfolio USDC without assuming USD=USDC;
- PostgreSQL canonical runtime truth;
- deterministic Risk Engine;
- PaperExecutor semantics;
- fail-closed behavior.

## Safety invariants

These are mandatory for the entire task:

```text
TRADING_MODE=paper
LIVE_ENABLED=false
LIVE_ARMED=false
REAL_ORDERS_SENT=0
```

Also:

- no private Coinbase trading credentials;
- no live order code activation;
- no VPS wipe;
- no migration of old Fondazione state;
- no contamination of PAPER balances/positions/orders by backtest/replay jobs.

## Target

Canonical VPS identity remains:

- instance: `fondazione`
- zone: `us-central1-a`
- internal IP: `10.128.0.16`
- public IP: `35.239.91.187`

Run target preflight before any deploy/migration. Fail closed on mismatch.

---

# A. Official Coinbase source contract

Before implementation, independently verify the current official Coinbase public documentation for the historical market-data endpoint(s) you will use.

Record in `reports/TASK-0005-BACKFILL.md`:

- exact public endpoint(s);
- authentication requirement (must remain public/no private trading credential for this task);
- supported granularities/timeframes;
- maximum candles/records per request;
- pagination/window semantics;
- rate-limit behavior relevant to the implementation;
- ordering/timestamp semantics;
- any documented caveats for unavailable intervals.

Do not encode undocumented request limits as truth. If Coinbase behavior differs from documentation, record both the documented contract and observed behavior and implement conservatively.

---

# B. Canonical historical market-data schema

Implement versioned PostgreSQL storage for historical market data.

At minimum the model must preserve:

- source/provider;
- canonical symbol;
- execution product id;
- market-data product id;
- proxy flag;
- base currency;
- quote currency;
- product/universe version;
- granularity;
- candle open timestamp;
- open/high/low/close/volume;
- source timestamp if distinct;
- ingestion timestamp;
- source/version metadata;
- quality/completeness state.

Required properties:

1. uniqueness prevents duplicate canonical candles;
2. inserts/upserts are idempotent;
3. history can represent products that later become disabled/delisted;
4. no hardcoded asset or quote whitelist;
5. schema migrations are explicit and restart-safe;
6. historical tables cannot overwrite runtime PAPER ledger truth.

Recommended logical entities (names may differ if justified):

- `historical_candles`;
- `historical_backfill_jobs`;
- `historical_gaps`;
- `dataset_versions`;
- `replay_runs` / isolated experiment ledger metadata.

---

# C. Historical backfill engine

Implement a restart-safe and idempotent CLI/service.

It must support:

```text
--all-active-products
--symbol <canonical symbol>
--start <timestamp>
--end <timestamp>
--granularity <supported timeframe>
--resume
--dry-run
```

Equivalent config/API shapes are acceptable, but all capabilities are required.

Behavior:

1. source product identity from the canonical TASK-0004 universe registry;
2. use `market_data_product_id` for historical source requests where appropriate;
3. preserve canonical/execution identity in storage;
4. chunk requests according to verified Coinbase constraints;
5. bounded retries/backoff;
6. explicit rate limiting;
7. durable checkpoints;
8. interruption/resume without duplicates;
9. per-product/per-window error state;
10. never fabricate missing candles;
11. products without available history are recorded as unavailable, not silently omitted.

Full-universe operation must be architecturally supported, but certification may use a bounded representative historical window to avoid unnecessary mass-download cost/time. The task must prove that no code change is required to run against the entire dynamic universe.

---

# D. Data integrity and gap recovery

Implement deterministic validation for each canonical time series:

- duplicate timestamp;
- out-of-order candle;
- missing expected interval;
- overlapping/conflicting candle;
- non-positive or invalid price;
- `high < low`;
- OHLC values outside high/low bounds;
- invalid/negative volume;
- timestamp misalignment for requested granularity;
- interval outside known product listing/availability where that information is available.

Persist gap/quality state.

Implement targeted recovery:

```text
integrity scan
      ↓
detected missing interval(s)
      ↓
minimal bounded Coinbase refetch
      ↓
revalidate
      ↓
RESOLVED or EXPLICIT_UNAVAILABLE
```

Synthetic forward-fill/interpolation is disabled by default and must never occur silently.

---

# E. Historical ↔ live continuity contract

TASK-0005 must use the same identity semantics as TASK-0004.

Examples:

```text
canonical_symbol       = BTC/USDC
execution_product_id   = BTC-USDC
market_data_product_id = BTC-USD
market_data_is_proxy   = true
```

Historical and live paths must agree on this mapping.

Implement continuity checks between:

- latest closed historical candle;
- current live market identity/marks;
- universe/product status.

Do not assume USD == USDC for valuation.

Proxy identity changes, listings and delistings must be explicit/versioned rather than retroactively rewriting historical identity.

---

# F. Canonical Dataset / Feature Input Contract

Create the generic immutable data contract future models and strategies will consume.

At minimum a dataset/snapshot must carry:

- `dataset_id`;
- dataset version/hash;
- universe version/hash;
- canonical symbol(s);
- timeframe;
- ordered observations;
- start/end timestamps;
- `as_of` timestamp;
- source/provenance metadata;
- preprocessing/feature-contract version;
- code SHA/config hash where generated;
- no-future-data guarantee.

## Hard no-lookahead rule

For replay time `T`, every data accessor MUST reject or exclude observations with timestamp greater than `T`.

This rule must be enforced in code, not merely documented.

Do not add strategy-specific alpha indicators in TASK-0005. Generic transforms required for canonicalization/validation are allowed.

---

# G. Deterministic Backtest / Replay Foundation

Build a replay engine, not a separate toy trading bot.

Target flow:

```text
Historical canonical data
        ↓
Replay Clock / as_of T
        ↓
Canonical MarketSnapshot / Dataset view
        ↓
Strategy Runtime boundary STUB / deterministic fixture
        ↓
existing Portfolio/Risk contracts where available
        ↓
ExecutionIntent
        ↓
simulated execution
        ↓
isolated replay/backtest ledger
```

The strategy boundary in this task exists only to certify infrastructure. Use deterministic fixture policies, not alpha research.

Requirements:

- strictly chronological event processing;
- explicit simulated clock;
- deterministic seed/config;
- no future observations visible;
- stable symbol normalization;
- reuse existing deterministic Risk Engine and ExecutionIntent semantics wherever possible;
- configurable fee/spread/slippage assumptions;
- no write access to production PAPER balances/positions/execution-result tables unless explicitly namespaced/isolated for replay;
- replay can be interrupted/restarted safely where practical;
- same data + config + code + seed => same result digest.

A replay must produce enough evidence to reconstruct every decision/input/output in order.

---

# H. Provenance and reproducibility

Every dataset/backtest run must record at least:

- run/experiment id;
- dataset id/hash;
- universe version/hash;
- code SHA;
- config hash;
- timeframe;
- requested date range;
- effective available date range;
- symbols/product identities;
- fees/spread/slippage settings;
- seed;
- start/end run timestamps;
- result/audit digest;
- warnings/gaps/unavailable intervals.

Two identical certification replays must produce identical canonical result digests.

---

# I. Required tests

Add non-vacuous tests covering at minimum:

1. multi-quote dynamic product backfill without whitelist;
2. generic USDC execution ↔ USD market-data proxy identity in historical storage;
3. direct USDT-USDC / EURC-USDC handling;
4. idempotent repeated ingest;
5. restart/resume checkpointing;
6. duplicate detection;
7. out-of-order detection;
8. missing interval detection;
9. impossible OHLCV rejection/quarantine;
10. targeted gap recovery;
11. unavailable interval recorded explicitly;
12. no-lookahead accessor rejection;
13. replay chronological ordering;
14. identical replay reproducibility/digest;
15. different config/seed changes provenance/digest as expected;
16. backtest/PAPER storage isolation;
17. TASK-0003/TASK-0004 regression suite remains green;
18. safety flags remain PAPER-only.

No acceptance test may pass by manually incrementing a metric or manually setting the expected result instead of exercising the production path under test.

---

# J. Runtime certification on `fondazione`

After local/unit/integration work:

1. preflight exact target;
2. deploy in-place only;
3. apply migrations explicitly;
4. run full test suite with zero unexpected skips/failures;
5. backfill a representative multi-quote set including:
   - at least one generic USDC→USD proxy;
   - direct USDT-USDC or EURC-USDC;
   - at least one EUR or GBP quote;
   - at least one additional discovered quote if historical data is available;
6. demonstrate resume after interrupted backfill;
7. demonstrate integrity scan/gap recovery;
8. execute two identical deterministic replay runs and prove identical digests;
9. prove replay state did not alter current PAPER balances/positions/orders;
10. report current safety flags and `REAL_ORDERS_SENT=0`;
11. record immutable GitHub-reachable deployed code SHA and final PR head externally in the PR comment when necessary.

---

# K. Required reports

Create:

- `reports/TASK-0005-DATA-SCHEMA.md`
- `reports/TASK-0005-BACKFILL.md`
- `reports/TASK-0005-BACKTEST-FOUNDATION.md`
- `reports/TASK-0005-VERIFY.md`

The reports must distinguish documented claims, test evidence, and actual target-runtime evidence.

---

# L. Stop conditions

Stop and report `BLOCKED` if any of the following cannot be proven safely:

- target identity;
- public historical source contract;
- database isolation;
- no-lookahead enforcement;
- deterministic replay reproducibility;
- safe migrations;
- TASK-0003/TASK-0004 regressions;
- PAPER safety invariants.

Do not bypass a blocker by weakening a test.

---

# M. Completion verdict

Exactly one final verdict is allowed:

```text
ENGINE_DATA_STATUS=READY_FOR_PORTFOLIO_ENGINE
```

or

```text
ENGINE_DATA_STATUS=BLOCKED
```

When READY:

- open a PR to `main`;
- post exact deployed SHA and PR head SHA;
- stop for independent ChatGPT review;
- do NOT start TASK-0006 automatically unless explicitly instructed or the existing project automation authorizes progression after merge/review.
