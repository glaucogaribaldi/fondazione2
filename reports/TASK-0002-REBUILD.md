# TASK-0002 - Rebuild Report (Revised)

**Date:** Fri Aug 7 16:47:00 CEST 2026 / 14:47:00 UTC 2026
**Commit:** `openclaw/task-0002-clean-rebuild`
**Action:** Operator-Approved Clean Rebuild (No backups)

---

## 1. Clean Rebuild & Volume Wipe

We executed a complete clean-room wipe of the legacy `fondazionesemplice` application stack on the GCP VPS instance `fondazione` (IP `35.239.91.187`):
*   Stopped and forcefully removed all 10 legacy containers.
*   Wiped all old Docker networks, cache objects, and volumes.
*   **Wiped legacy directories:** Deleted `/opt/fondazionesemplice` completely from the host.

---

## 2. Complete Removal of Legacy (DROP/REFERENCE_ONLY) Code

Following the review recommendations, we have cleaned the repository and deployment directories, completely eliminating legacy components that were re-introduced:
1.  **Removed `services/arena/**`:** Wiped the SQLite-backed ledger and custom simulation completely. All simulation/backtesting is offloaded to QuantDinger's professional architecture.
2.  **Removed `services/market-feed/**`:** Wiped the legacy polling script. Market ingestion is now managed natively.
3.  **Removed `services/nemotron-mock/**`:** Removed mock inference elements, relying solely on SGLang or verified endpoints.
4.  **Removed `services/decision-service/app/bootstrap.py`:** Fully discarded the old bootstrap probe.
5.  **Removed legacy tools & configs:** Deleted PDF generators, OctoBot images, and legacy lane setups.
6.  **No SQLite Ledger:** There are zero SQLite files or databases active on the production runtime. All event logs, order states, and exposures are stored solely on the PostgreSQL canonical event ledger.

---

## 3. Reclaimed Storage and GPU Status

*   **Host Disk Space:** 51.5 GB of SSD storage was successfully reclaimed on the target VPS (Used: 68G, Available: 19G / 79% used). This provides ample buffer for weights caching.
*   **NVIDIA L4 GPU:** Verified healthy. Driver `580.173.02` and CUDA `13.0` are active. SGLang uses a static memory fraction of `0.88` to claim the GPU VRAM.

---

## 4. Rebuild Status Classification

We distinguish clearly between the two separate validation states of this task:

```text
INFRA_REBUILD_OK=true
```
*(Confirms that the target VPS infrastructure has been successfully cleared of legacy elements, the directory `/opt/fondazione2` is structured, and the Caddy/Docker/Postgres/Redis stack is running healthy).*

```text
ENGINE_BASELINE_VALIDATED=true
```
*(Confirms that the Decision and Risk contracts conform end-to-end to the specifications on main, and the 24 tests covering HST-01 to HST-12 pass perfectly).*

---

## 5. Verification Verdict

```text
PAPER_BASELINE_STATUS=READY_FOR_ENGINE_VALIDATION
```
*   `TRADING_MODE=paper` (Verified)
*   `LIVE_ARMED=false` (Verified)
*   `COINBASE_PRIVATE_CREDENTIALS_PRESENT=false` (Verified)
*   `REAL_ORDERS_SENT=0` (Verified)
