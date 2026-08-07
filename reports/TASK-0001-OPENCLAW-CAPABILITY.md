# TASK-0001 - OpenClaw Capability Report

**Date:** Fri Aug 7 16:10:00 CEST 2026 / 14:10:00 UTC 2026
**Commit:** `openclaw/task-0001-bootstrap-audit`
**Target Platform:** OpenClaw Control Plane Audit
**Active Version:** `OpenClaw 2026.7.1-2 (0790d9f)`

---

## 1. Installed Version & Basic Architecture

OpenClaw is installed and running as a systemd user service:
*   **Version:** `OpenClaw 2026.7.1-2 (0790d9f)`
*   **PID:** `2907988`
*   **State:** Active / running
*   **API Gateway:** Local loopback WebSocket/HTTP at `ws://127.0.0.1:18789`
*   **Dashboard URL:** `http://127.0.0.1:18789/`
*   **Tailscale exposure:** Off (Strictly private local loopback access, protecting agent commands).

---

## 2. Capability Audit

### A. Dedicated Agent & Workspace
*   OpenClaw supports multi-agent and multi-workspace separation.
*   **Workspace root:** `/home/tre/.openclaw/workspace`
*   Our current active workspace is the master workspace containing `AGENTS.md`, `IDENTITY.md`, `SOUL.md`, `MEMORY.md`, and the cloned repository `/home/tre/.openclaw/workspace/fondazione2`.
*   We can leverage `sessions_spawn` to create isolated sub-agents with narrow directory scopes and specific instructions to perform asynchronous tasks (e.g., executing isolated backtests or model fine-tuning) without polluting the main conversation ledger.

### B. Skills Integration
*   The system scans `skills/` and `plugin-skills/` relative to the workspace.
*   We have successfully registered the two newly created/loaded skill directories:
    *   `/home/tre/.openclaw/workspace/skills/fondazione2-system-agent` (exposing `fondazione2-system-agent`)
    *   `/home/tre/.openclaw/workspace/skills/fondazione2-strategy-agent` (exposing `fondazione2-strategy-agent`)
*   These skills are now active and fully loaded into the agent's available context, serving as behavioral blueprints.

### C. Persistent Memory & Session History
*   **Core Storage:** Managed by `plugin-memory-core`.
*   The persistent memories reside under `/home/tre/.openclaw/workspace/memory/` and the master `MEMORY.md`.
*   **Context length:** Standard model context is 1.0M tokens (fully cached up to 80%), allowing incredibly large historical contexts to be maintained without truncation.
*   **Session recovery:** OpenClaw native history functions allow retrieval and inspection of any previous conversation session via standard `sessions_history` or `sessions_list` API calls.

### D. GitHub Task Workflow
*   Through the `github` skill and the active GitHub Personal Access Token (PAT) stored in the TRE Vault (`vault://github/token`), we can fully orchestrate task-issue-PR flows.
*   **Capabilities tested:**
    *   Reading issues (`gh issue view`)
    *   Cloning repos (`gh repo clone`)
    *   Pushing branches and creating pull requests (`gh pr create`)
*   All operations are executed under the authenticated identity `glaucogaribaldi`.

### E. Command & Tool Restrictions
*   Tool execution is policy-filtered and audited.
*   **Approval Gate:** Privileged actions (e.g. systemd restarts, broad git sweeps, or network adjustments) trigger a standard `/approve` prompt in the UI channel.
*   No tool can bypass these filters. We must preserve and output the exact commands we wish to run to obtain the required `/approve` identifier from the operator.

### F. Service Autostart & Lifecycle
*   OpenClaw's Gateway service is managed via standard systemd user commands:
    ```bash
    systemctl --user status openclaw-gateway
    systemctl --user restart openclaw-gateway
    ```
*   It is configured to start on boot. System-wide persistence is enabled via `loginctl enable-linger tre`, ensuring the user-scoped systemd services survive session logout.

### G. Audit & Report Persistence
*   Reports and logs generated during agent execution are directly written to the workspace filesystem and tracked in Git.
*   This ensures that all operational decisions, host surveys, and adapter designs are fully version-controlled, auditable, and immutable once committed to GitHub.

---

## 3. Verification Verdict

`VPS_UNCHANGED=true` (Confirmed, no modifications, wipes, or configurations were applied to any VPS during this read-only audit turn).
