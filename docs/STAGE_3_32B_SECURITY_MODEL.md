# Stage 3.32B: Security Model

## 1. Assets and threats

**Assets:**
- production integrity: the scheduler, cycles, workers, schedules, configuration, topic history, OAuth and publishing;
- the approval queue;
- the audit history.

| Threat | Example | Control |
|---|---|---|
| Autonomy exceeds its mandate | the engine proposes a restart or a publish | Closed registry; prohibited types refused in code; no handler can exist; module-boundary test |
| Autonomy approves itself or raises its own level | the engine calls `approve` / `set_level` | Reserved actor prefixes refused for every human-only operation |
| Cross-site request forgery | a web page posts to `127.0.0.1:8000/api/autonomy/...` | Same-origin check (Origin / Sec-Fetch-Site), JSON-only, per-process CSRF token in a custom header (needs a CORS preflight, which the app refuses for that header) |
| DNS rebinding | an attacker hostname resolving to 127.0.0.1 | `require_local_control` requires a loopback client **and** a loopback Host |
| Remote access | another machine on the LAN | Loopback client check (403) |
| Forged approval | a database edit sets `status='APPROVED'` | HMAC-SHA256 approval signature over (id, type, payload hash, approver, time, expiry, mode), keyed by `data/autonomy/approval.key` (outside the database) |
| Payload swap after approval | a database edit or the API changes the payload | The payload hash is recomputed at execution; mismatch → approval invalidated, back to PENDING, audited |
| Replay / double execution | two clicks, two processes, retries | Atomic conditional claim; terminal states never re-run; partial unique index on open duplicates |
| Stale approval | approved last week, executed today | Per-type TTL; expiry is enforced at approve, at execute and at claim time (`expires_at_utc > now` inside the UPDATE) |
| Audit tampering | delete or edit audit rows | UPDATE/DELETE triggers abort; a SHA-256 hash chain detects edits even if the triggers are dropped |
| Secret leakage | an error message or evidence contains a key or token | `redact()` on all stored or returned details, reasons, errors and results; secret-looking keys dropped; error text bounded |
| Resource exhaustion | a runaway handler | Per-type timeout, bounded retries, daily limits, standing-grant caps, bounded list sizes (≤ 200 / 500) |
| Control plane failure | database locked or missing, config corrupt | Fail closed (§3) |

## 2. Authorization layers for state-changing requests

Every layer must pass.

1. **`require_local_control`:** loopback client and loopback Host.
2. **`require_same_origin`** (existing 3.22 guard):
   - `Sec-Fetch-Site` of `cross-site` or `same-site` → 403;
   - an `Origin` other than the dashboard → 403.
3. **`require_autonomy_write`:**
   - `Sec-Fetch-Site`, if present, must be `same-origin` or `none`;
   - `Content-Type` must be `application/json` (otherwise 415; a simple-form CSRF is impossible);
   - `X-CrazzyDocs-CSRF` must equal the per-process token (`secrets.token_urlsafe(32)`, compared in constant time).
4. **Strict body model** (`extra="forbid"`, length limits) and explicit confirmation where it matters (`confirm: true`, typed `RESUME AUTONOMY`).
5. **Control-plane checks:**
   - a valid human operator identity;
   - a permitted action type;
   - a valid state transition;
   - the payload-hash match;
   - the gate (for execute).

**The token.** It is served by `GET /api/autonomy/session`, which needs a loopback, same-origin context. A cross-origin page cannot read it (CORS allows only the dashboard origins), and its Origin is refused anyway. The token changes on every dashboard restart; the dashboard re-fetches it after a 403.

## 3. Fail-closed behaviour

| Failure | Result |
|---|---|
| `studio.db` missing | Never created. The dashboard switch reads as active; every write is 503; execution blocked. |
| Database locked or unreadable, or config JSON invalid | The configuration falls back to all-OFF with the dashboard switch active. |
| Startup config missing, unreadable, invalid, or not `{"autonomy_execution": "enabled"}` | Kill switch C active |
| `CRAZZYDOCS_AUTONOMY` ∈ {off, disabled, 0, false, kill, no} | Kill switch C active, even if the file enables execution |
| Sentinel check raises (permissions, I/O) | Kill switch B active |
| Kill-switch activation cannot reach the database | The sentinel file is written instead, and the event goes to `audit_fallback.jsonl` |
| Approval key missing or unreadable at execution | Signature invalid → blocked |
| Any unexpected exception in an endpoint | 503 "autonomous execution stays blocked"; details are logged locally, not returned |
| Process stops while an action is EXECUTING | After its time budget (timeout × attempts + 120 s), maintenance marks it FAILED / INTERRUPTED. **It is never re-executed automatically.** |

## 4. Kill switches

| Switch | Where | Set by | Read | Survives |
|---|---|---|---|---|
| **A. Dashboard** | `autonomy_config['dashboard_kill_switch']` | Dashboard button or API (CSRF-protected); deactivation needs typed confirmation | Before every gate and every attempt | Restarts |
| **B. Sentinel file** | `backend/data/AUTONOMY_DISABLED` exists | Operator creates the file (any content); the dashboard falls back to it if the database is down | Before every gate and every attempt | Everything; works without the database or the dashboard |
| **C. Startup config** | `backend/data/autonomy_startup.json` + `CRAZZYDOCS_AUTONOMY` | Operator, then a process restart | Once, at `ControlPlane` construction (latched) | Process lifetime |

**Effects:**
- Any active switch blocks execution.
- Requests that were APPROVED stay APPROVED but cannot run. They are not silently cancelled.
- A switch activated between claim and start releases the claim back to APPROVED.
- A switch activated between attempts stops the remaining attempts (FAILED, with reason).
- Proposals and approvals can still be recorded, so the queue keeps working for humans.

**No kill switch can stop production.** The kill-switch code paths write only `autonomy_config`, the sentinel file and audit rows; a test proves the production tables are unchanged. The dashboard's existing production stop and pause controls are separate and unchanged.

## 5. Audit trail

**Recorded event types:**

| Area | Event types |
|---|---|
| Observations and assessments | `OBSERVATION_RECORDED`, `ASSESSMENT_COMPLETED` |
| Proposals | `ACTION_PROPOSED`, `PROPOSAL_REFUSED`, `UNKNOWN_ACTION_REJECTED`, `PROHIBITED_ACTION_BLOCKED` |
| Decisions | `APPROVED`, `APPROVAL_REFUSED`, `APPROVAL_INVALIDATED`, `PAYLOAD_AMENDED`, `REJECTED`, `CANCELLED`, `EXPIRED` |
| Execution | `EXECUTION_STARTED`, `EXECUTION_BLOCKED`, `EXECUTION_COMPLETED`, `EXECUTION_FAILED` |
| Controls | `KILL_SWITCH_ACTIVATED`, `KILL_SWITCH_DEACTIVATED`, `PERMISSION_CHANGED` (levels, standing grants and revocations) |

**Fields per event:** `seq`, `ts_utc`, `event_type`, `actor`, `request_id`, `action_type`, `previous_state`, `new_state`, `reason`, `details_json`, `prev_hash` and `event_hash`.

**Integrity:**
- The application only INSERTs, inside the same transaction as the state change it records.
- Triggers abort UPDATE and DELETE.
- `verify_audit_chain()` (also `GET /api/autonomy/audit?verify=true` and the dashboard) recomputes the chain and reports the first bad `seq`.
- If the database is unavailable, events go to `data/autonomy/audit_fallback.jsonl`. They are never dropped.

## 6. Out of scope and residual risks

1. **A malicious local process running as the same Windows user** can read `approval.key`, edit `studio.db` or call the API with a fetched token. The control plane does not defend the machine against its own user; the OS account boundary does.
2. **Operator identity is a declared name.** There is no password or login. It is stored for accountability, not authentication.
3. **The approval key is a local file** created with the most restrictive mode Windows honours. It is not stored in the database, logs or Git. Back it up with `backend/data` if approvals must survive a data-directory restore; otherwise outstanding approvals simply become invalid (fail closed).
4. **Handlers run in the dashboard process.** A handler bug can cost CPU for at most its timeout. All current handlers are local, read-only or write only inside `data/autonomy/`.
