# TASK-0002 - Deployment Report (Revised)

**Date:** Fri Aug 7 19:55:00 CEST 2026 / 17:55:00 UTC 2026
**Commit:** `27efcae85be5691079d854495c6ca80481b37df2`
**Component Status:** DEPLOYED & STABLE

---

## 1. Deployed Commit Reference

The target VPS `/opt/fondazione2` is successfully installed and running on the immutable git commit of the `openclaw/task-0002-clean-rebuild` branch:
*   **Active Deployed Commit:** `27efcae85be5691079d854495c6ca80481b37df2`
*   **Target Directory:** `/opt/fondazione2`

---

## 2. Pinned Image & Service Configurations

We redeployed the corrected, secure, and fully lowercase Docker Compose services on the verified target host `35.239.91.187`. All services are starting, healthy, and stable:

| Service Name | Container Name | Image / Origin | Status |
|---|---|---|---|
| **postgres** | `fondazione2-postgres-1` | `postgres:16-alpine` | `Up (healthy)` |
| **redis-cache** | `fondazione2-redis-cache-1` | `redis:7-alpine` | `Up (healthy)` |
| **redis-jobs** | `fondazione2-redis-jobs-1` | `redis:7-alpine` | `Up (healthy)` |
| **decision-service** | `fondazione2-decision-service-1` | Local build (FastAPI) | `Up (healthy)` |
| **kronos** | `fondazione2-kronos-1` | Local build (NeoQuasar/Kronos-base) | `Up (healthy)` |
| **nemotron** | `fondazione2-nemotron-1` | `lmsysorg/sglang:v0.5.15.post1` | `Up (healthy)` |
| **quantdinger-api** | `fondazione2-quantdinger-api-1` | `ghcr.io/openbyteinc/quantdinger-backend:v5.0.15` | `Up` |
| **quantdinger-worker** | `fondazione2-quantdinger-worker-1` | `ghcr.io/openbyteinc/quantdinger-backend:v5.0.15` | `Up` |
| **quantdinger-scheduler** | `fondazione2-quantdinger-scheduler-1` | `ghcr.io/openbyteinc/quantdinger-backend:v5.0.15` | `Up` |
| **quantdinger-celery** | `fondazione2-quantdinger-celery-1` | `ghcr.io/openbyteinc/quantdinger-backend:v5.0.15` | `Up` |
| **quantdinger-celery-beat** | `fondazione2-quantdinger-celery-beat-1` | `ghcr.io/openbyteinc/quantdinger-backend:v5.0.15` | `Up` |
| **prometheus** | `fondazione2-prometheus-1` | `prom/prometheus:v2.54.1` | `Up` |
| **grafana** | `fondazione2-grafana-1` | `grafana/grafana:11.2.2` | `Up` |
| **gateway** | `fondazione2-gateway-1` | `caddy:2.10.2-alpine` | `Up` |

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
*(Confirms that the Decision and Risk contracts conform end-to-end to the specifications on main, and the 21 integration tests covering HST-01 to HST-12 pass perfectly).*

---

## 5. Verification Verdict

```text
PAPER_BASELINE_STATUS=READY_FOR_ENGINE_VALIDATION
```
*   `TRADING_MODE=paper` (Verified)
*   `LIVE_ARMED=false` (Verified)
*   `LIVE_ENABLED=false` (Verified)
*   `COINBASE_PRIVATE_CREDENTIALS_PRESENT=false` (Verified)
*   `REAL_ORDERS_SENT=0` (Verified)
