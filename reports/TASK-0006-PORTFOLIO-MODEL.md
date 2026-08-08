# TASK-0006 — Unified Multi-Asset Portfolio Model Report

**Date:** Sat Aug 8 15:35:00 CEST 2026 / 13:35:00 UTC 2026
**Component:** `PortfolioEngine`
**Status:** DESIGNED, TESTED & PRODUCTION-READY

---

## 1. Unified Shared-Capital Schema
To prevent discrepancies and maintain a single source of truth across both live paper trading (PAPER) and historical replay (backtest), we have normalized the portfolio state into versioned, PostgreSQL-backed core entities:
*   `portfolio_cash`: Tracks the unified pool of cash (`cash` and `reserved`) per currency.
*   `portfolio_positions`: Tracks positions across standard and proxy symbols (`symbol`, `quantity`, `entry_price`, `realized_pnl`, `unrealized_pnl`, `stop_loss_price`, `take_profit_price`).
*   `portfolio_metadata`: Manages global state variables (`peak_equity`, `version`, `digest`) required to maintain consistent state versions.

### SQL Schema Definition (PostgreSQL)
```sql
CREATE TABLE IF NOT EXISTS portfolio_cash (
    currency TEXT PRIMARY KEY,
    cash NUMERIC(28,10) NOT NULL DEFAULT 0,
    reserved NUMERIC(28,10) NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS portfolio_positions (
    symbol TEXT PRIMARY KEY,
    quantity NUMERIC(28,10) NOT NULL DEFAULT 0,
    entry_price NUMERIC(28,10) NOT NULL DEFAULT 0,
    realized_pnl NUMERIC(28,10) NOT NULL DEFAULT 0,
    unrealized_pnl NUMERIC(28,10) NOT NULL DEFAULT 0,
    stop_loss_price NUMERIC(28,10),
    take_profit_price NUMERIC(28,10),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## 2. Multi-Quote Valuation & Conversion Invariants
The system enforces strict multi-quote valuation invariants. No quote currencies are hardcoded or whitelisted; instead, all non-base assets and cash balances are dynamically mapped and evaluated using a real-time conversion graph BFS.

### Mathematical Accounting Formula
The total portfolio equity ($E_p$) valued in base currency (default `USDC`) is calculated as:
$$E_p = \text{cash}_{\text{base}} + \sum_{c \in \text{currencies}} \left( \text{cash}_c \times R_{c \to \text{base}} \right) + \sum_{s \in \text{positions}} \left( Q_s \times P_s \times R_{\text{quote}_s \to \text{base}} \right)$$

where:
*   $\text{cash}_{\text{base}}$: Cash balance in the base currency (`USDC`).
*   $R_{c \to \text{base}}$: Dynamic conversion rate from currency $c$ to `USDC` computed via the BFS graph.
*   $Q_s$: Active position quantity of symbol $s$.
*   $P_s$: Fresh market mark price of symbol $s$ (falls back safely to entry price if mark is stale or missing).
*   $R_{\text{quote}_s \to \text{base}}$: Conversion rate from the symbol's quote currency to `USDC`.

### Exposure metrics
*   **Gross Exposure ($G_e$):** Absolute sum of all active position values converted to base currency.
    $$G_e = \sum_{s} \left| Q_s \times P_s \times R_{\text{quote}_s \to \text{base}} \right|$$
*   **Net Exposure ($N_e$):** Net sum of active position values (long only in current paper/backtest setup).
*   **Concentration:** Percentage of the largest single position value relative to total portfolio equity.
