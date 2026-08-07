# TASK-0002 - Deployment Report

**Date:** Fri Aug 7 16:36:00 CEST 2026 / 14:36:00 UTC 2026
**Commit:** `openclaw/task-0002-clean-rebuild`
**Component Status:** DEPLOYED & STABLE

---

## 1. Deployed Commit Reference

The target VPS `/opt/fondazione2` is successfully installed and running on the immutable git commit of the `openclaw/task-0002-clean-rebuild` branch:
*   **Active Deployed Commit:** `d29c923ea0be`
*   **Target Directory:** `/opt/fondazione2`

---

## 2. Pinned Image & Service Configurations

The deployment launched the complete Fondazione2 platform, successfully pinning and isolating each service:

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

## 3. Network Architecture & Separation of Concerns

*   **Redis Cache separation:** Segregated into `redis-cache` (using memory limits of 1gb and `allkeys-lru` eviction policy for candles) and `redis-jobs` (configured with Append-Only File persistence and `noeviction` policy for Celery worker reliability).
*   **PostgreSQL Event Ledger:** Re-created fresh database, successfully applied standard migrations. QuantDinger and Decision services have verified active connections.

---

## 4. Verification Verdict

```text
PAPER_BASELINE_STATUS=READY_FOR_ENGINE_VALIDATION
```
*   `TRADING_MODE=paper` (Verified)
*   `LIVE_ARMED=false` (Verified)
*   `COINBASE_PRIVATE_CREDENTIALS_PRESENT=false` (Verified)
*   `REAL_ORDERS_SENT=0` (Verified)
