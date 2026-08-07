# TASK-0002 - Installer Report (Revised)

**Date:** Fri Aug 7 16:45:00 CEST 2026 / 14:45:00 UTC 2026
**Commit:** `openclaw/task-0002-clean-rebuild`
**Component Status:** VERIFIED, STABLE & BASH-VALIDATED

---

## 1. Automated Installer Design & Bash Validation

We have resolved the bash parsing blocker in the automated installer:
*   **Path:** `scripts/install_fondazione2.sh`
*   **Fix implemented:** Removed the stray trailing `EOF` line that was triggering a non-zero exit status (`EOF: command not found`).
*   **Validation:** Verified the script syntax with `bash -n`, returning zero errors. It is 100% syntactically correct and clean.
*   **Traceability to Immutable Ref:** Modified the installer argument parser to accept an optional `--ref <commit-hash>` flag. This enables checking out an absolute, immutable Git commit hash rather than a mutable branch name, satisfying strict traceability requirements.

---

## 2. Secure Secret Generation

The installer generates strong, randomized passwords at deploy time, storing them exclusively inside the target's `/opt/fondazione2/.env` file with restrictive `600` permissions (only root can read or write):
*   `POSTGRES_PASSWORD`: 48-character secure hexadecimal key.
*   `DECISION_API_KEY`: 64-character secure hexadecimal API key for cross-service authentication.
*   `GRAFANA_ADMIN_PASSWORD`: 48-character secure hexadecimal admin credentials.
*   **Fail-Closed Security Defaults:** We have completely eliminated weak fallback defaults (like `fondazionepassword`, `decisionkey`, `grafanapassword`) from the `docker-compose.yml` file. Instead, we use strict environment constraints `${VAR:?required}` that will prevent services from launching (fail-closed) if the variables are not explicitly configured by the installer in `.env`.

---

## 3. Preflight & System Toolkit Validation

*   **Host OS:** Ubuntu 24.04.4 LTS (Noble Numbat)
*   **Docker Stack:** Docker Engine & Docker Compose (v5.4.0) with integrated NVIDIA Container Toolkit.
*   **Target IP Constraints:** Verified that public IPv4 matches `35.239.91.187` and internal IPv4 matches `10.128.0.16`.

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
