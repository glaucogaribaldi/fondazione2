# TASK-0001 - Fondazione2 Final Recommendation Report

**Date:** Fri Aug 7 16:15:00 CEST 2026 / 14:15:00 UTC 2026
**Commit:** `openclaw/task-0001-bootstrap-audit`
**Audited Target:** Final release definitions and architecture decisions for the Fondazione2 deploy.

---

## 1. Pinned Component Candidates

We recommend pinning the entire model and execution stack to immutable references to guarantee absolute reproducibility and prevent silent breaking updates:

| Component | Pinned Candidate / Ref | Origin Repository / Source |
|---|---|---|
| **QuantDinger Backend** | `v5.0.15` (Docker: `ghcr.io/OpenByteInc/quantdinger-backend:v5.0.15`) | `https://github.com/OpenByteInc/QuantDinger` |
| **Kronos Service** | Git Commit `67b630e67f6a18c9e9be918d9b4337c960db1e9a` | `https://github.com/shiyu-coder/Kronos.git` |
| **Kronos Model** | `NeoQuasar/Kronos-base` | Hugging Face Hub |
| **Kronos Tokenizer** | `NeoQuasar/Kronos-Tokenizer-base` | Hugging Face Hub |
| **Nemotron Inference** | SGLang version `v0.5.15.post1` | `lmsysorg/sglang:v0.5.15.post1` |
| **Nemotron Model** | `nvidia/NVIDIA-Nemotron-Nano-9B-v2` | Hugging Face Hub |
| **PostgreSQL** | `postgres:16-alpine` | Official Docker Hub |
| **Redis Cache** | `redis:7-alpine` (Eviction allowed: LRU) | Official Docker Hub |
| **Redis Jobs (Celery)** | `redis:7-alpine` (Persistence: AOF, `noeviction`) | Official Docker Hub |

---

## 2. Resource Requirements (Disk, RAM, VRAM)

To host the entire integrated Fondazione2 environment, the target VPS machine must satisfy the following strict limits:

### A. RAM Allocations (Host Memory)
*   **SGLang (Nemotron):** Requires a minimum of **16 GiB** host RAM / shm for tensor operations and client pipelines.
*   **Kronos Service:** Requires **2 to 4 GiB** host RAM.
*   **QuantDinger API + Workers + Celery:** Requires **2 GiB** host RAM.
*   **Postgres + Redis:** Requires **1 to 2 GiB** host RAM.
*   **System Overhead:** Requires **2 GiB** host RAM.
*   **Minimum Host RAM Requirement:** **31 GiB** (Perfectly matches `fondazione-1` and `dolphin`).

### B. VRAM Allocations (GPU Memory)
*   **SGLang (Nemotron-Nano-9B-v2):** Uses a static memory-fraction of `0.88`, which allocates exactly **21.8 GiB** out of 24 GiB of GPU VRAM. This is a static lock.
*   **Kronos Service:** Configured to run on **CPU** (saving valuable GPU VRAM and preventing out-of-memory driver failures on L4).
*   **Minimum Host VRAM Requirement:** **24 GiB** (Fully satisfies the capacity of an NVIDIA L4 GPU).

### C. Disk Storage Allocations (SSD Space)
*   **Host OS + Base Packages:** ~10 GB.
*   **NVIDIA Drivers & Docker Images:** ~15 GB.
*   **Nemotron-Nano-9B-v2 weights:** ~18 GB (Hugging Face Cache).
*   **Kronos-base weights:** ~5 GB (Hugging Face Cache).
*   **Postgres Ledger & Cache Storage:** ~10 GB.
*   **Safety Buffer:** ~20 GB.
*   **Minimum SSD Requirement:** **80 GB**.
    *   *Warning on `fondazione-1`:* The current disk has 87G total capacity, with only **18G available** (81% used). Setting up Fondazione2 alongside `fondazionesemplice` is impossible without expanding the disk volume or executing the systematic clean-wipe of the legacy stack.

---

## 3. Installer Implementation Plan

We propose an automated, multi-stage installer executed in sequence:

1.  **Stage 1: Preflight check & Target Verification**
    *   Verify hostname, verify IP, assert OS is Ubuntu supported, and check CPU/RAM.
    *   Ensure GPU is detected (`nvidia-smi` check).
2.  **Stage 2: Stop and Erase Legacy Stack**
    *   Stop and remove all legacy `fondazionesemplice` containers, volumes, and networks to free up critical disk space (18G -> ~60G free space).
3.  **Stage 3: Base Tooling & Docker setup**
    *   Assert Docker, Compose, and NVIDIA Container Toolkit are installed and fully configured.
4.  **Stage 4: Directory Creation & Git Checkout**
    *   Create base directory `/opt/fondazione2`.
    *   Clone `fondazione2` repository at the pinned deployment commit.
5.  **Stage 5: Environment & Secret Generation**
    *   Copy `.env.example` to `.env`.
    *   Generate secure, random keys (JWT secrets, PostgreSQL passwords, API keys) and write them strictly inside `/opt/fondazione2/.env`.
6.  **Stage 6: PostgreSQL & Redis Bootstrap**
    *   Start Postgres and Redis containers. Run Alembic database migrations.
7.  **Stage 7: Core Models Download & Spin Up**
    *   Spin up SGLang (Nemotron) and Kronos containers. Monitor logs until the health endpoints (`http://localhost:30000/health` and `http://localhost:8081/healthz`) return HTTP 200 (completed weights download).
8.  **Stage 8: Core Services Spin Up**
    *   Start QuantDinger, Decision Aggregator, and Risk Engine.
9.  **Stage 9: Validation Smoke Test**
    *   Verify the system boots with `TRADING_MODE=paper` and `LIVE_ARMED=false`. Run automated synthetic order test loops inside a sandbox/test account.

---

## 4. Key Project Blockers

1.  **Storage Deficit on `fondazione-1`:** Having only 18G of free space is a critical blocker. We cannot pull the new Docker images or download model weights (requires at least 40G of free space). The system wipe must be authorized to proceed.
2.  **Broken Driver on Backup Host (`dolphin`):** `dolphin` has excellent free storage (85G free) and identical CPU/RAM/GPU specifications, but lacks Docker and is blocked by an unconfigured NVIDIA driver module compilation error.

---

## 5. Verification Verdict

`VPS_UNCHANGED=true` (Confirmed, no modifications, wipes, or configurations were applied to any VPS during this read-only audit turn).
