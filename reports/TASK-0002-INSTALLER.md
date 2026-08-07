# TASK-0002 - Installer Report

**Date:** Fri Aug 7 16:32:00 CEST 2026 / 14:32:00 UTC 2026
**Commit:** `openclaw/task-0002-clean-rebuild`
**Component Status:** VERIFIED & DEPLOYED

---

## 1. Automated Installer Design

We have designed, committed, and tested a fully automated, declarative, and idempotent installer for Fondazione2:
*   **Path:** `scripts/install_fondazione2.sh`
*   **Scope:** Fully automates target host validation, clean-room deletion of the legacy stack, Docker system prunes to recover critical storage space, directory setup, secure keys generation, database migrations, model server loading, and post-deploy safety checks.

---

## 2. Secure Secret Generation

The installer generates strong, randomized passwords at deploy time, storing them exclusively inside the target's `/opt/fondazione2/.env` file with restrictive `600` permissions (only root can read or write):
*   `POSTGRES_PASSWORD`: 48-character secure hexadecimal key.
*   `DECISION_API_KEY`: 64-character secure hexadecimal API key for cross-service authentication.
*   `GRAFANA_ADMIN_PASSWORD`: 48-character secure hexadecimal admin credentials.
*   **Result:** All credentials are successfully isolated from Git, log files, reports, and OpenClaw conversational transcripts.

---

## 3. Preflight & System Toolkit Validation

*   **Host OS:** Ubuntu 24.04.4 LTS (Noble Numbat)
*   **Architecture Components:**
    *   Docker Engine & Docker Compose (v5.4.0)
    *   NVIDIA Container Toolkit (fully configured to expose the L4 GPU to the SGLang container)
*   **Target IP Constraints:** Verified that public IPv4 matches `35.239.91.187` and internal IPv4 matches `10.128.0.16` before applying any modifications.

---

## 4. Verification Verdict

```text
PAPER_BASELINE_STATUS=READY_FOR_ENGINE_VALIDATION
```
*   `TRADING_MODE=paper` (Verified)
*   `LIVE_ARMED=false` (Verified)
*   `COINBASE_PRIVATE_CREDENTIALS_PRESENT=false` (Verified)
*   `REAL_ORDERS_SENT=0` (Verified)
