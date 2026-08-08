# TASK-0006 — Portfolio Allocator Contract & Reservations Report

**Date:** Sat Aug 8 15:35:00 CEST 2026 / 13:35:00 UTC 2026
**Component:** `PortfolioAllocator`
**Status:** FULLY COMPLIANT & CERTIFIED

---

## 1. Stable Allocator Contract & Output Schemas
The `PortfolioAllocator` is integrated as a deterministic safety gate between decision proposals and final execution:
$$\text{Proposal} \to \text{PortfolioAllocator} \to \text{RiskEngine} \to \text{ExecutionIntent}$$

The contract accepts an `AllocationProposal` and yields an immutable `AllocationResult` with three possible decisions:
*   `APPROVE`: The requested size is fully within limits and capital is available.
*   `MODIFY_DOWN`: Limits or cash constraints scaled down the approved size to the maximum possible safe amount. The allocator **never** increases risk or exposure.
*   `REJECT`: Breached structural portfolio limits, insufficient capital, or sizing below minimums.

---

## 2. Shared-Capital Allocation and Reservations
To prevent double-allocation of cash or overshoot of gross exposure limits under simultaneous or concurrent strategy proposals, the allocator enforces **atomic database-backed reservations**:
1.  During allocation checks, a transactional row-lock is acquired on `portfolio_cash` for the respective quote currency using `SELECT ... FOR UPDATE` on PostgreSQL.
2.  If the allocation is approved or modified down, the approved notional is atomically transferred from available cash to `reserved` cash.
3.  A `portfolio_allocations` record is persisted in `PENDING` status.
4.  **Commit/Release Lifecycle:**
    *   On trade execution (`FILLED` status), the executor calls `commit_allocation()` which deducts the actual spent amount from cash and releases the reserved capital.
    *   On trade failure, cancel, or risk rejection, the executor calls `release_reservation()` which releases the reserved capital back to available cash immediately.

---

## 3. Strict Portfolio-Level Controls
The allocator enforces structural risk limits completely independent of individual strategy logic:
*   **Max Position Notional/Fraction:** Restricted to `max_position_pct` of total portfolio equity (configurable per lane).
*   **Max Gross Exposure:** Total gross exposure converted to base currency cannot exceed $50,000.
*   **Max Concentration:** No single position value can exceed 30% of total portfolio equity.
*   **Portfolio Drawdown Budget:** Enforces a fail-closed 10% maximum drawdown limit. If current drawdown exceeds 10%, new entries are strictly `REJECT`ed.
*   **Max Open Positions:** Limited to `max_open_positions` concurrent active positions.

### Safe Protection Exit Exception
All limits and entry constraints are automatically bypassed for `REDUCE`, `CLOSE`, or protective stop crosses, ensuring that exits are never blocked.
