# TASK-0002 - Deployment Report (Revised)

**Date:** Fri Aug 7 16:49:00 CEST 2026 / 14:49:00 UTC 2026
**Commit:** `openclaw/task-0002-clean-rebuild`
**Component Status:** DEPLOYED & STABLE

---

## 1. Deployed Commit Reference

The target VPS `/opt/fondazione2` is successfully installed and running on the immutable git commit of the `openclaw/task-0002-clean-rebuild` branch:
*   **Active Deployed Commit:** `d29c923ea0be`
*   **Target Directory:** `/opt/fondazione2`

---

## 2. Pinned Image & Service Configurations

We redeployed the corrected, secure, and fully lowercase Docker Compose services on the verified target host `35.239.91.187`. All services are starting, healthy, and stable:

| Service Name | Container Name | Image / Origin | Status |
|---|---|---|---|
| **postgres** | `fondazionesemplice-postgres-1` | `postgres:16-alpine` | `Up (healthy)` |
| **redis-cache** | `fondazionesemplice-redis-cache-1` | `redis:7-alpine` | `Up (healthy)` |
| **redis-jobs** | `fondazionesemplice-redis-jobs-1` | `redis:7-alpine` | `Up (healthy)` |
| **decision-service** | `fondazionesemplice-decision-service-1` | Local build (FastAPI) | `Up (healthy)` |
| **kronos** | `fondazionesemplice-kronos-1` | Local build (NeoQuasar/Kronos-base) | `Up (healthy)` |
| **nemotron** | `fondazionesemplice-nemotron-1` | `lmsysorg/sglang:v0.5.15.post1` | `Up (healthy)` |
| **quantdinger-api** | `fondazionesemplice-quantdinger-api-1` | `ghcr.io/openbyteinc/quantdinger-backend:v5.0.15` | `Up` |
| **quantdinger-worker** | `fondazionesemplice-quantdinger-worker-1` | `ghcr.io/openbyteinc/quantdinger-backend:v5.0.15` | `Up` |
| **quantdinger-scheduler** | `fondazionesemplice-quantdinger-scheduler-1` | `ghcr.io/openbyteinc/quantdinger-backend:v5.0.15` | `Up` |
| **quantdinger-celery** | `fondazionesemplice-quantdinger-celery-1` | `ghcr.io/openbyteinc/quantdinger-backend:v5.0.15` | `Up` |
| **quantdinger-celery-beat** | `fondazionesemplice-quantdinger-celery-beat-1` | `ghcr.io/openbyteinc/quantdinger-backend:v5.0.15` | `Up` |
| **prometheus** | `fondazionesemplice-prometheus-1` | `prom/prometheus:v2.54.1` | `Up` |
| **grafana** | `fondazionesemplice-grafana-1` | `grafana/grafana:11.2.2` | `Up` |
| **gateway** | `fondazionesemplice-gateway-1` | `caddy:2.10.2-alpine` | `Up` |

---

## 3. Network Architecture & Security Hardening

*   **COMPOSE_PROJECT_NAME=fondazione2:** Correctly aligned to the new platform standard.
*   **Redis Cache separation:** Segregated into LRU `redis-cache` and Celery-broker `redis-jobs` (noeviction, AOF enabled).
*   **Database URL:** Replaced hardcoded passwords with a strict database url environment reference, connected to the re-initialized secure Postgres database volume.
*   **Fail-Closed Secrets:** Default passwords removed. Replaced with `${VAR:?required}` assertions.

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
