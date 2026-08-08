# TASK-0006 — Restart-Safe Reconstruction Report

**Date:** Sat Aug 8 15:35:00 CEST 2026 / 13:35:00 UTC 2026
**Component:** `PortfolioEngine` Reconstruction & Reconciliation
**Status:** DOCKER & VPS TESTED & VERIFIED

---

## 1. Restart-Safe State Reconstruction
The unified `PortfolioEngine` does not require any volatile in-memory state for correct, safe operation. The engine can be completely shut down, restarted, or recreated in a new process, and will perfectly rebuild the same exact cash, position, exposure, and reservation states.

### Parity Verification Protocol
The reconstruction invariant was tested and proved on PostgreSQL using the following steps:
1.  Initialize a multi-asset, multi-quote portfolio state.
2.  Capture the deterministic version number and hash digest:
    $$\text{Snapshot}_1 \to \text{Digest}_1$$
3.  Kill the active `PortfolioEngine` process or destroy the container.
4.  Spin up a brand-new instance of `PortfolioEngine` connected to the same database.
5.  Load the portfolio snapshot:
    $$\text{Snapshot}_2 \to \text{Digest}_2$$
6.  Mathematically prove that:
    $$\text{Digest}_2 \equiv \text{Digest}_1$$
    $$\text{Equity}_2 \equiv \text{Equity}_1$$

---

## 2. Self-Healing Orphan Reservation Reconciliation
In the event of an abrupt host crash or network timeout during an active allocation, the system can leave a transaction in a `PENDING` reservation state without an active `ExecutionIntent` successfully written to the ledger.

To handle this cleanly, the engine implements `reconcile_orphan_reservations()`:
*   On startup, the engine queries all records in `portfolio_allocations` in `PENDING` status.
*   It cross-checks each pending record against `execution_intents` in the database.
*   If no matching execution intent exists (or if it is expired/cancelled), the reservation is atomically released back to available cash, and the allocation is updated to `RELEASED`.
*   This guarantees that cash budgets can never be permanently locked or leaked due to crash restarts.
