# TASK-0002 - Rebuild Report

**Date:** Fri Aug 7 16:34:00 CEST 2026 / 14:34:00 UTC 2026
**Commit:** `openclaw/task-0002-clean-rebuild`
**Action:** Operator-Approved Clean Rebuild (No backups)

---

## 1. Clean Rebuild & Volume Wipe

As explicitly authorized and requested by the operator, we executed a complete clean-room wipe of the legacy `fondazionesemplice` application stack on the GCP VPS instance `fondazione` (IP `35.239.91.187`):
*   Stopped and forcefully removed all 10 legacy containers.
*   Wiped all old Docker networks, cache objects, and volumes, including the legacy PostgreSQL volume containing the old paper ledger.
*   Wiped legacy directory `/opt/fondazionesemplice` from the host.

---

## 2. Disk Space Recovery

Before the rebuild, the target VPS had only **18G available** (81% disk usage), representing a critical deployment blocker.
*   **Action taken:** Systematic docker system prunes and layer cleanups.
*   **Reclaimed space:** **51.5 GB of SSD storage was successfully reclaimed**.
*   **Resulting state:** Disk space is fully open and wide, allowing secure download and caching of the massive SGLang and Kronos machine learning weights.

---

## 3. Hardware & GPU Status

*   **Host CPU:** Intel(R) Xeon(R) CPU @ 2.20GHz
*   **Host RAM:** 31 GiB (Free: ~19 GiB)
*   **GPU:** 1x NVIDIA L4 (24GB VRAM)
*   **Driver Version:** `580.173.02`
*   **CUDA Version:** `13.0`
*   **Status:** Healthy. The GPU driver was successfully queried, showing 0 active processes prior to SGLang start.

---

## 4. Verification Verdict

```text
PAPER_BASELINE_STATUS=READY_FOR_ENGINE_VALIDATION
```
*   `TRADING_MODE=paper` (Verified)
*   `LIVE_ARMED=false` (Verified)
*   `COINBASE_PRIVATE_CREDENTIALS_PRESENT=false` (Verified)
*   `REAL_ORDERS_SENT=0` (Verified)
