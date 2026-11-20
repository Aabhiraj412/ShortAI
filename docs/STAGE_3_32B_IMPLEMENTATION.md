# Stage 3.32B: Autonomy Control Plane (implementation)

**Status:** implemented and tested in the development worktree. **Not committed, not deployed.**
**Base:** `4ae426b` (Stage 3.32A). Worktree: `.claude/worktrees/crazzy-docs-verify-capabilities-ac634e`, branch `claude/scheduler-redesign-render-fix`.

**Default state after deployment:** every level is `OFF`, and the startup-configuration kill switch is active because `backend/data/autonomy_startup.json` does not exist. Deploying this code enables nothing.

## 1. Summary

The Autonomy Control Plane is the mandatory authorization layer for every Stage 3.32 autonomous operation. It decides:

| Question | Mechanism |
|---|---|
| What can autonomy observe? | Category level ≥ OBSERVE (`record_observation`) |
| What can it recommend? | Category level ≥ RECOMMEND (`propose` → approval queue) |
| What needs approval? | Every executable action. `EXPLICIT_EACH` types need a human approval per request; `STANDING_OR_EXPLICIT` types may use a time-limited, capped standing approval. |
| What can it execute? | Only registered, permitted types; level `APPROVED_EXECUTION`; valid signed approval; no kill switch active; within daily limits |
| What is always prohibited? | 21 registry entries marked `prohibited`, refused in code at propose, approve, standing-grant and execute; no handler can exist for them |
| How is autonomy disabled immediately? | Three independent, fail-closed kill switches (dashboard, sentinel file, startup configuration) |

**Production is outside its reach:**
- the package imports nothing from the scheduler, workers, publishing, OAuth or configuration modules (a test checks this with the AST);
- the kill switches only gate autonomous actions.

## 2. Architecture

```
dashboard "Autonomy Control" tab ── GET  /api/autonomy/{status,permissions,registry,kill-switches,approvals,executions,audit}
   (CSRF token from                  POST /api/autonomy/approvals/{id}/{approve,reject,cancel,execute}
    GET /api/autonomy/session)       PUT  /api/autonomy/permissions/levels
                                     POST /api/autonomy/permissions/standing-approvals[/{type}/revoke]
                                     POST /api/autonomy/kill-switch/{activate,deactivate}
                                     POST /api/autonomy/assessment/run
                                            │ localhost + same-origin + JSON + CSRF token (writes)
                                            ▼
backend/app/autonomy/
  registry.py       closed catalogue: 5 permitted + 21 prohibited action types, payload schemas, Level enum
  control_plane.py  ControlPlane: levels · kill switches · approval queue · signed approvals · atomic claim ·
                    bounded execution + verification · hash-chained append-only audit · maintenance
  handlers.py       the ONLY code an approved action can run (local files under data/autonomy, read-only intelligence)
  engine.py         run_assessment(): observe / recommend from the Stage 3.32A daily summary (on demand, no loop)
                                            │
                                            ▼
studio.db: autonomy_config · autonomy_approval_requests · autonomy_audit_events · autonomy_executions
backend/data/AUTONOMY_DISABLED (sentinel) · backend/data/autonomy_startup.json (startup config) · backend/data/autonomy/ (artifacts)
```

### Request lifecycle

```
propose ──► PENDING ──approve(hash)──► APPROVED ──execute: gate + atomic claim──► EXECUTING ──► COMPLETED
              │                           │  payload change ─► PENDING                │        └─► FAILED
              ├─► REJECTED / CANCELLED     ├─► REJECTED / CANCELLED / EXPIRED          └─ kill switch before start ─► APPROVED
              └─► EXPIRED                  └─ standing approval (auto, signed)
```

**Execution steps.** `execute(request_id)` loads the stored request (the caller supplies only its id) and runs these steps in order:
1. **Maintenance:** expire overdue requests; reconcile interrupted executions.
2. **Gate:** the action type is known and permitted, and a handler exists; no kill switch is active; the effective level is APPROVED_EXECUTION; the status is APPROVED and not expired; the payload re-hash matches both the stored and the approved hash; the HMAC signature is valid; the standing grant is still valid (if used); the daily limit is not reached.
3. **Atomic claim:** `UPDATE … SET status='EXECUTING', claim_token=? WHERE id=? AND status='APPROVED' AND expires_at_utc>?` must change exactly one row.
4. **Attempts:** up to `retry_limit + 1`. Each attempt re-checks the kill switches and runs the handler in a worker thread with `timeout_seconds`.
   - Generic or transient errors are retried.
   - `PermanentActionError` and timeouts are not.
   - Each attempt is a row in `autonomy_executions`.
5. **Verification:** the handler's `verify`. On failure, `compensate` runs and the request becomes FAILED.
6. **Final transition:** conditional on the claim token, then audited.

## 3. Files

| File | Change |
|---|---|
| `backend/app/autonomy/__init__.py` | **New**: package doc |
| `backend/app/autonomy/registry.py` | **New**: `Level`, risk and approval constants, `CATEGORIES`, `ActionSpec`, 5 permitted and 21 prohibited specs, `lookup`, `require_permitted`, `validate_payload` |
| `backend/app/autonomy/control_plane.py` | **New**: `ControlPlane`, schema, audit chain, kill switches, permissions, queue, execution, reads, `get_control_plane()` |
| `backend/app/autonomy/handlers.py` | **New**: 5 handlers (report, recommendation, diagnostic, operator notify, Community draft) plus an import-time guard |
| `backend/app/autonomy/engine.py` | **New**: `run_assessment` |
| `backend/app/main.py` | **Modified**: CSRF token, `require_autonomy_write`, error mapping, strict request models, 19 `/api/autonomy/*` endpoints |
| `backend/app/templates/dashboard.html` | **Modified**: "Autonomy" header pill, "Autonomy Control" tab (Technical group), CSS, renderer and action functions |
| `backend/tests/test_autonomy_control_plane_332b.py` | **New**: 40 test functions (47 test cases including the 8-case kill-switch matrix) |
| `docs/STAGE_3_32B_*.md` | **New**: this document, plus the permission matrix, security model and test results |

## 4. Database changes

**Mechanism.** Tables are created by `CREATE … IF NOT EXISTS` on the control plane's first connection. This is the same pattern as `candidate_ledger` and `youtube_quota`. Schema version 1 is recorded in `autonomy_config` by `ensure_schema()`.
- No existing table is altered or read.
- A missing database is never created: the connection opens with `mode=rw`.

| Table | Purpose | Key constraints and indexes |
|---|---|---|
| `autonomy_config` | key/value: `global_level`, `category_levels`, `standing_approvals`, `dashboard_kill_switch`, `schema_version` | PK `key`; `version` incremented per write; `updated_by` |
| `autonomy_approval_requests` | the approval queue | PK `id`. Indexes: `(status, expires_at_utc)`, `(action_type, created_at_utc)`. **Partial unique** `dedupe_key WHERE status IN (PENDING, APPROVED, EXECUTING)` |
| `autonomy_audit_events` | append-only audit | `seq` AUTOINCREMENT; indexes `request_id`, `ts_utc`. Triggers **refuse UPDATE and DELETE**; `prev_hash`/`event_hash` SHA-256 chain |
| `autonomy_executions` | one row per execution attempt | UNIQUE `(request_id, attempt)`; indexes `(action_type, started_at_utc)`, `started_at_utc` |

**Transactions.** Every state change is one `BEGIN IMMEDIATE` transaction containing the update and its audit event. The busy timeout is 5 s. Writes are a few rows each.

**Live database.** The tables will be created in `studio.db` only when the deployed dashboard first serves an autonomy request. Nothing was written to the live database during development.

## 5. Dashboard

**Header pill:**
- Autonomy: OFF;
- Autonomy: \<level\>;
- Autonomy: BLOCKED (n kill switches);
- plus the pending count.

It costs one `/api/autonomy/status` GET per 60 s.

**"Autonomy Control" tab.** It loads only when opened, and refreshes every 60 s while open. It contains:
- **KPI row:** enabled/disabled, global level, active kill switches, pending approvals, audit count and chain state.
- **Kill switches:** A/B/C with detail; "Activate dashboard kill switch"; "Deactivate…" requires typing `RESUME AUTONOMY`.
- **Autonomy levels:** global plus five categories, configured and effective; Apply asks for a reason and confirmation.
- **Approval queue:**
  - open by default, or filtered by state;
  - each row shows payload, hash, evidence, risk and expiry;
  - Approve / Execute / Reject / Cancel buttons, with confirmation dialogs that show the exact payload and hash.
- **Recent autonomous actions** and **recent failures**.
- **Standing approvals** (grant/revoke) and the **operator inbox**.
- **Permission overview:** every action type, including the prohibited ones and what each guards.
- **Audit trail:** the last 50 events, with the hash-chain verification result.

**Rendering safety:**
- every server value goes through `escHtml`;
- request ids reach handlers through `data-` attributes, never inline JavaScript;
- the operator name is kept in `localStorage` (convenience only).

**Verified:**
- in a static preview with real control-plane JSON from a temporary database;
- the whole page script parses in Node;
- no duplicate function names.

## 6. Operating it (after a future, approved deployment)

1. **Deploy:** nothing changes, because everything is OFF and switch C is active.
2. **Enable execution capability:**
   - create `backend/data/autonomy_startup.json` containing `{"autonomy_execution": "enabled"}`;
   - restart the dashboard (the operator does this);
   - or set `CRAZZYDOCS_AUTONOMY=off` to force the switch on regardless of the file.
3. **Raise levels per category** in the tab. Suggested order: OBSERVE everywhere, then RECOMMEND, then APPROVED_EXECUTION for low-risk categories only.
4. **Emergency stop**, using any one of these:
   - the dashboard button;
   - create `backend/data/AUTONOMY_DISABLED`;
   - remove the startup file and restart.

## 7. Known limitations

1. **Execution runs in the dashboard process,** when an operator clicks Execute or a standing approval fires during an assessment.
   - There is no background executor.
   - 3.32D will add a single-flight daemon job; deploying that needs a daemon restart, with approval.
2. **A timed-out handler thread cannot be killed** in Python. The request is marked FAILED and never retried; the thread finishes in the background. Every current handler is local and short.
3. **The startup switch (C) is latched per process.** Turning it off needs a restart of the process that serves the API (the dashboard). This is by design.
4. **Operator identity is self-declared.** It is a validated name typed in the dashboard, not an authenticated login. Authorization comes from local access, a same-origin browser context and the CSRF token. A local, malicious process running as the same Windows user could call the API. That is outside this threat model (see Security model §6).
5. **Retention.** The audit table grows without pruning, by design; it is small (one row per event). A retention or export policy is an open decision.
6. **`operator.notify`** writes only to the dashboard inbox. E-mail delivery through `NotificationManager` is deliberately not wired in (that would be an external message).
7. **Expiry and reconciliation are lazy.** They run on reads and writes of the control plane, not on a timer.

## 8. Proposed sequence for 3.32C and 3.32D (not started; needs approval)

Both stages build on this control plane and add **no** new authorization path. Every proposal goes through `ControlPlane.propose`, and every execution through `ControlPlane.execute`.

### 3.32C: Decision engine (recommend only)

1. **Rules module** (`autonomy/rules/*.py`). Pure functions turn a Stage 3.32A bundle into typed findings: recoverable job inside its window; window too short; recurring deterministic failure; topic exhaustion; stale code; quota pressure; provider errors.
   - Each finding carries evidence, a deterministic key and an expiry.
2. **Finding → proposal mapping.** Findings may only produce existing action types: `recommendation.record`, `operator.notify`, `report.operational.generate`, `diagnostic.readonly.run`.
   - A remedial action such as "retry job X" appears only as a *recommendation text*, never as an executable type.
3. **Determinism and dedupe.** The same snapshot always produces the same proposals; the existing open-request dedupe collapses repeats. Conflicting rules on one target resolve to the safer outcome (notify rather than act).
4. **Read API and dashboard.** `GET /api/autonomy/findings` (read-only). The dashboard shows findings with "would have proposed".
5. **Tests.** Scenario fixtures per rule; determinism; no proposal can ever name a prohibited type (the registry refuses it anyway); no network.
6. **Activation:** OBSERVE everywhere, then RECOMMEND after the user reviews precision. No execution.

### 3.32D: Executors and verifier

1. **New executor types,** one per release, each added to the registry with spec, handler, verify and compensate. Each must reuse an existing guarded service; for example, a T1 thumbnail retry or `verify_scheduled_publications`.
   - Every new type starts `EXPLICIT_EACH`.
   - Anything touching production state is MEDIUM or HIGH risk.
2. **Budgets and circuit breakers** (Safety model §5) in the gate: per target, per failure signature and global. Repeated `VERIFY_FAILED` drops the category to RECOMMEND and notifies the operator.
3. **Daemon-side single-flight executor job** (optional; needs a daemon restart with approval). It executes only APPROVED requests, through the same `execute()`.
4. **Reconciliation on startup.** Already implemented in `_maintenance`: interrupted actions are never re-run.
5. **Tests.** Executor refusal matrix (kill switch, level, approval, stale precondition, active worker, deadline, budget); breaker trip; two executors → one runs.
6. **Activation:** per action class, APPROVED_EXECUTION with explicit approval first; standing approval only by explicit user decision after a clean period.

**Recommended order:** 3.32C first (zero execution risk; it proves rule quality), then 3.32E Community drafts (it uses `community.draft.prepare`, already explicit-each), and 3.32D last.
