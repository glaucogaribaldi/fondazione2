# TASK-0001 - Host Inventory Report

**Date:** Fri Aug 7 15:47:00 CEST 2026 / 13:47:00 UTC 2026
**Commit:** `openclaw/task-0001-bootstrap-audit`
**Target Host Category:** Fondazione2 Candidate Machines
**Audit Mode:** READ-ONLY (No modifications, no restarts)

---

## 1. Machine Inventory Overview

We have audited three distinct machines available on the Tailscale private network (`u50-tre`, `fondazione-1`, `dolphin`).

### A. Primary Local Machine: `u50-tre` (zava-U50)
*   **IP Address (Tailscale):** `100.115.100.39`
*   **OS:** Ubuntu 26.04 LTS (Resolute Raccoon), Kernel 7.0.0-28-generic (x64)
*   **CPU:** Intel(R) Celeron(R) N5105 @ 2.00GHz (4 Cores / 4 Threads)
*   **RAM:** 14 GiB total (~9.4 GiB available)
*   **Disk Layout:** NVMe SSD, 468G total on `/`, 135G used, 310G free (31% used)
*   **GPU/VRAM:** None (Intel Integrated Graphics)
*   **Docker & Compose:** Docker Engine v29.1.3, Docker Compose v5.3.1
*   **Active Local Docker Containers:**
    *   `mission-control-mission-control-1` (Uvicorn backend)
    *   `fondazione-cockpit-db-1` (Postgres 16-alpine)
    *   `tre-redis-1` (Redis 7-alpine, `127.0.0.1:6380`)
    *   `tre-postgres-1` (Postgres 16 / pgvector, `127.0.0.1:5433`)
    *   `tre-qdrant-1` (Qdrant, `127.0.0.1:6333`)
    *   `tcos-n8n` (n8n automation)
    *   `maxun` frontend/backend/browser/postgres/minio (Maxun scraper stack)
    *   `totally-copyright-os` frontend/fastapi/celery/postgres/redis stack
    *   `cognee_server` (Cognee semantic model engine)
*   **OpenClaw Status:** Installed and running as systemd user service (PID 2907988, Active).

### B. Active Production VPS: `fondazione-1` (fondazione-noble)
*   **IP Address (Tailscale):** `100.96.230.80`
*   **OS:** Ubuntu 24.04.4 LTS (Noble Numbat), Kernel 6.17.0-1021-gcp (x64)
*   **CPU:** Intel(R) Xeon(R) CPU @ 2.20GHz
*   **RAM:** 31 GiB total (~24 GiB available)
*   **Disk Layout:** GCP Boot/Data Disk, 87G total on `/`, 70G used, 18G free (81% used - **Space is tight!**)
*   **GPU/VRAM:** 1x NVIDIA L4 (Ada Lovelace, 24GB VRAM)
    *   **Driver Version:** 580.173.02
    *   **CUDA Version:** 13.0
    *   **GPU Volatile Memory:** 21791MiB / 23034MiB used (94% usage, loaded by SGLang)
*   **Docker & Compose:** Docker Engine v29.7.2, Docker Compose v5.4.0
*   **Running Docker Containers (The `fondazionesemplice` Stack):**
    *   `fondazionesemplice-gateway-1` (caddy:2.10.2-alpine, ports 80, 443)
    *   `fondazionesemplice-grafana-1` (grafana/grafana:11.2.2, port 3000)
    *   `fondazionesemplice-arena-1` (Custom paper ledger server, port 8082)
    *   `fondazionesemplice-market-feed-1` (Custom market polling engine, port 8083)
    *   `fondazionesemplice-decision-service-1` (Custom decision aggregator, port 8080)
    *   `fondazionesemplice-kronos-1` (Kronos predictor, port 8081)
    *   `fondazionesemplice-octobot-1` (OctoBot 2.1.1 execution bot, port 5001)
    *   `fondazionesemplice-postgres-1` (PostgreSQL 16-alpine, port 5432)
    *   `fondazionesemplice-nemotron-1` (SGLang launch server running `nvidia/NVIDIA-Nemotron-Nano-9B-v2`, port 30000)
    *   `fondazionesemplice-prometheus-1` (Prometheus v2.54.1, port 9090)

### C. Secondary/Staged VPS: `dolphin` (dolphin-jammy)
*   **IP Address (Tailscale):** `100.124.202.86`
*   **OS:** Ubuntu 22.04.5 LTS (Jammy Jellyfish), Kernel 6.8.0-1064-gcp (x64)
*   **CPU:** Intel(R) Xeon(R) CPU @ 2.20GHz
*   **RAM:** 31 GiB total (~30 GiB available)
*   **Disk Layout:** GCP Boot/Data Disk, 97G total on `/`, 13G used, 85G free (13% used)
*   **GPU/VRAM:** 1x NVIDIA L4 (Ada Lovelace, 24GB VRAM)
    *   **NVIDIA SMI Status:** FAILED. High-level diagnostic indicates that the driver kernel modules are not loaded/built.
    *   **DKMS details:** `nvidia/610.43.02` is only "added" to DKMS but has not been compiled or installed (likely due to a kernel update mismatch or partial installation).
    *   **Apt details:** `nvidia-dkms` is in state `iF` (half-configured), and `nvidia-driver` is in state `iU` (unpacked but unconfigured).
*   **Docker & Compose:** NOT INSTALLED. (Command `docker` not found).
*   **Running Services:** None (staged machine, no Fondazione services currently active).

---

## 2. Port Allocation Map (Tailscale / localhost)

On `fondazione-1`, the ports are heavily bound as follows:
*   `80` / `443`: Caddy Gateway (External reverse proxy)
*   `3000`: Grafana Dashboard
*   `5001`: OctoBot Web TUI
*   `8080`: decision-service
*   `8081`: kronos forecasting service
*   `8082`: arena paper-trading server
*   `8083`: market-feed service
*   `9090`: Prometheus scraper
*   `30000`: SGLang server (Nemotron inference API)

On `u50-tre` (local):
*   `3100` / `3110`: Mission Control
*   `5180` / `5173` / `3001` / `3002` / `9000` / `9001`: Maxun scraping stack
*   `5432` / `5433` / `5439`: Multi-tenant Postgres databases
*   `5678`: n8n automation
*   `6379` / `6380`: Redis caches
*   `6333` / `6334`: Qdrant vector DB
*   `18789`: OpenClaw Gateway WS/HTTP (Local loopback only)

---

## 3. Host Feasibility Analysis for Fondazione2

1.  **Production Target Recommendation:** `fondazione-1` (`100.96.230.80`) is the only viable host currently equipped with a working CUDA GPU toolkit and running SGLang correctly.
2.  **GPU Resource Constraints:** The SGLang server on `fondazione-1` completely saturates the L4 GPU (consuming 21.7 GiB of 23 GiB VRAM with static mem-fraction 0.88). Running additional large GPU models on this host without strict scheduling is not possible.
3.  **Storage Hazard on Active VPS:** The `/` volume on `fondazione-1` is at **81% capacity** (only 18G available). Before deploying Fondazione2, a systematic wipe of `fondazionesemplice` Docker volumes, images, and logs is absolutely required to free up disk space.
4.  **Dolphin Action Plan:** `dolphin` is a powerful backup candidate (31 GiB RAM, 1x L4 GPU, 85G free disk space). However, it is blocked by:
    *   No Docker installed.
    *   Broken NVIDIA DKMS kernel compilation (driver is unconfigured).
    This machine requires a bootstrap execution before it can host Fondazione2.

---

## 4. Verification Verdict

`VPS_UNCHANGED=true` (Confirmed, no modifications, wipes, or configurations were applied to any VPS during this read-only audit turn).
