# TASK-0001 - Historical Classification Report

**Date:** Fri Aug 7 15:52:00 CEST 2026 / 13:52:00 UTC 2026
**Commit:** `openclaw/task-0001-bootstrap-audit`
**Historical Source Reference:** `glaucogaribaldi/fondazionesemplice`
*   Current historic commit: `a0633cb737e25fb29897b05e6b7cfc1965c5d373`
*   Audit reference: `755e0ba81a4dce4eb86101d4b19821ca45934ad2`

---

## 1. Classification Methodology & Bias

Our guiding principle for Fondazione2 is **"Do not copy code blindly"**. The legacy codebase from `fondazionesemplice` suffered from major design flaws (such as lack of protection orders execution, portfolio TOCTOU race conditions, tight coupling to a custom paper simulation, and double-counted fees).
We classify components into four categories to guide the creation of the new robust, reproducible Fondazione2 environment:

*   `DROP`: Obsolete or broken legacy components to be fully discarded.
*   `REFERENCE_ONLY`: Components to look at for parameters, configurations, or historical parameters, but not to build upon.
*   `REUSE_CONCEPT`: The underlying concept or design is sound, but must be fully reimplemented or configured from scratch.
*   `PORT_WITH_REWRITE`: Code to migrate to the new structure, with substantial structural modifications to satisfy security and integration contracts.

---

## 2. Component-by-Component Classification

| Component | Target Classification | Historical Description / Rationale |
|---|---|---|
| **`services/arena` (Paper Simulator)** | **`DROP`** | Custom SQLite-backed simulation server. Deeply flawed paper ledger with bad fee math, race conditions, and lack of stop execution. Discarded in favor of QuantDinger's professional built-in paper executor. |
| **`services/market-feed`** | **`DROP`** | Custom polling feed fetching public candles from Coinbase and updating Arena. Discarded; QuantDinger provides its own direct market adapters and scheduler loops. |
| **`octobot`** | **`DROP`** | Drakkar software execution bot. Suboptimal layer that added operational friction. All execution logic will now flow through QuantDinger's Coinbase adapter. |
| **`services/decision-service/app/bootstrap.py` (Bootstrap Probe)** | **`DROP`** | A dirty test check coupled to the old SQLite/Arena setup. Not required or allowed under Fondazione2's strict validation plane. |
| **`services/nemotron-mock`** | **`REFERENCE_ONLY`** | A basic python server that mocked an OpenAI chat completion endpoint. Useful only for quick developer dry-runs without loading the actual GPU server. |
| **`services/decision-service/app/clients.py`** | **`REUSE_CONCEPT`** | The API client architecture for Kronos and SGLang. The concept of utilizing a dedicated model forecast coupled with an LLM-based policy/critic model is preserved, but will be integrated directly inside QuantDinger's Strategy API V2 execution boundaries. |
| **`services/decision-service/app/main.py`** | **`PORT_WITH_REWRITE`** | The decision endpoint that coordinates SGLang, Kronos, and risk rules. It must be refactored into the Fondazione2 **Decision Aggregator**, serving as an intermediate validator that normalizes inputs and intercepts QuantDinger's `StrategyIntent` before running the Risk Engine. |
| **`services/decision-service/app/risk.py`** | **`PORT_WITH_REWRITE`** | The core deterministic rules. Highly valuable safety logic, but must be fully rewritten to address the TOCTOU checks, properly enforce stop/take exits without cooldown blocks, and support correct sizing semantics. |
| **`services/kronos` (Model Service)** | **`PORT_WITH_REWRITE`** | Prediction endpoint exposing `NeoQuasar/Kronos-base` and its custom tokenizer. It must be preserved and integrated cleanly as a standard CPU/GPU service in the Fondazione2 compose layout. |
| **`tests/test_risk.py` & `tests/test_arena_ledger.py`** | **`REUSE_CONCEPT`** | Excellent test specs covering spread limits, stale data, and limit approvals. These will be ported and expanded into the official **Fondazione2 Acceptance Tests (HST-01..12)**. |
| **`openclaw/skills/*`** | **`DROP`** | Legacy OpenClaw installer/updater scripts. They are monolithic and hardcoded for the old repository. We will replace them with the modular `fondazione2-system-agent` and `fondazione2-strategy-agent` skills. |
| **`monitoring` (Prometheus, Grafana, Caddy)** | **`REUSE_CONCEPT`** | Monitoring topology is correct but dashboards and rules must be updated to inspect QuantDinger metrics, PostgreSQL ledger events, and SGLang server load. |

---

## 3. Special Technical Focus Areas

### A. Kronos Deployment & Model Pin
*   **Upstream Repository:** `https://github.com/shiyu-coder/Kronos.git`
*   **Commit Pin (KRONOS_REF):** `67b630e67f6a18c9e9be918d9b4337c960db1e9a`
*   **Model/Tokenizer Pin:** `NeoQuasar/Kronos-base` / `NeoQuasar/Kronos-Tokenizer-base`
*   **Verdict:** **PORT_WITH_REWRITE**. Keep the Dockerfile structure and python dependencies but rewrite the service wrapper to use standardized fastapi routing, cleaner error logging, and explicit GPU/CPU offload configuration.

### B. Nemotron & SGLang Deployment
*   **Image:** `lmsysorg/sglang:v0.5.15.post1`
*   **Model:** `nvidia/NVIDIA-Nemotron-Nano-9B-v2`
*   **Launch parameters:**
    ```bash
    python3 -m sglang.launch_server --model-path nvidia/NVIDIA-Nemotron-Nano-9B-v2 --host 0.0.0.0 --port 30000 --mem-fraction-static 0.88 --trust-remote-code
    ```
*   **Verdict:** **REUSE_CONCEPT / REFERENCE_ONLY**. SGLang launcher configuration is correct for the NVIDIA L4 GPU. We will preserve these parameters in Fondazione2's `docker-compose.yml` but expose static configuration variables for custom memory allocation tuning.

### C. Docker/GPU Bootstrap Techniques
*   The legacy setup used a complex custom bash script `scripts/install_vm.sh` to install drivers, nvidia container runtime, and docker.
*   **Verdict:** **REUSE_CONCEPT**. Rather than custom scripts, we will provide a standardized Ansible playbook or a highly declarative, idempotent Docker Compose configuration matching modern OpenClaw system administration standards.

### D. Observability & OpenClaw Integration
*   The legacy layout attempted to run updates via manual skills. In Fondazione2, **OpenClaw is the first-class Control Plane**. The System Agent and Strategy Agent possess dedicated workspaces and direct git integration, keeping human and machine operations fully audited.

---

## 4. Verification Verdict

`VPS_UNCHANGED=true` (Confirmed, no modifications, wipes, or configurations were applied to any VPS during this read-only audit turn).
