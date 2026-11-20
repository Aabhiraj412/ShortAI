# Stage 3.32G: Controlled Autopilot

**Purpose:** one coherent, controlled autonomy lifecycle built on the existing components. There is no parallel control system: every proposal still goes through `ControlPlane.propose`, and every execution through `ControlPlane.execute`.

**Operating mode is unchanged by this stage:**
- the global level and all category levels stay **OFF**;
- the startup kill switch stays active (no `autonomy_startup.json`).

## 1. Lifecycle and architecture

```
3.32A Operational Intelligence (read-only data)
      │
      ├─► 3.32C Recommendations (deterministic rules; optional AI wording, display only)
      │        │  Stage 3.32G bridge: rule id → fixed, registered, low-impact follow-up actions only
      ├─► 3.32D Self-Healing (classification, diagnosis, allow-listed recovery plans)
      ▼
3.32B Control Plane: propose → (signed, hash-bound) approve → claim + lease → handler → verify → audit
      ▲                     (levels, kill switches, limits, circuit breaker, expiry, dedupe)
      │
Autopilot (3.32G): read-only overview of all of the above, plus the action policy;
                   3.32E Community drafts and 3.32F publishing readiness, shown with their own gates
```

**Modes:** each action's effective level is `min(global level, category level)`.

| Mode | Allowed |
|---|---|
| **OFF** | nothing: no observations, proposals or execution |
| **OBSERVE** | `record_observation` only; proposals are refused |
| **RECOMMEND** | proposals into the approval queue (including from recommendations through the bridge); never executes |
| **APPROVED_EXECUTION** | executes only requests that hold a valid explicit or standing approval, after every gate |

Any active kill switch blocks all execution:
- the dashboard switch;
- the `backend/data/AUTONOMY_DISABLED` sentinel;
- the startup switch, which stays active unless `autonomy_startup.json` enables execution (fail closed).

## 2. Centralized action-permission policy

The policy lives in `registry.py` (`policy_tier`, `policy_entry`) and is served by `GET /api/autopilot/policy`. Every entry has:
- identifier, category and risk;
- required permission and approval mode;
- preconditions and postconditions (the handler's independent verification);
- timeout, retries and daily limit;
- idempotency, audit and rollback.

### Autonomous-eligible

Low risk. These may run under a **time-limited standing approval**, only when their category is at APPROVED_EXECUTION and no kill switch is active.

| Action | Category | Risk | Timeout / retries | Daily limit |
|---|---|---|---|---|
| `diagnostic.readonly.run` | diagnostics | LOW | 60 s / 1 | 96 |
| `operator.notify` (local dashboard inbox only, never external) | notifications | LOW | 10 s / 1 | 48 |
| `recommendation.record` | recommendations | READ_ONLY | 10 s / 0 | 100 |
| `report.operational.generate` | reporting | LOW | 60 s / 1 | 24 |
| `selfheal.diagnostic.run` | self_healing | LOW | 60 s / 1 | 96 |

### Approval required

Every request needs its own explicit human approval.

| Action | Category | Risk | Timeout / retries | Daily limit |
|---|---|---|---|---|
| `community.draft.prepare` | community_drafts | MEDIUM | 30 s / 1 | 6 |
| `selfheal.provider.retry_readonly` | self_healing | MEDIUM | 45 s / 0 | 12 |
| `selfheal.asset.reacquire_staged` | self_healing | MEDIUM | 90 s / 0 | 12 |

### Permanently gated

Never executable by autonomy at any level, and refused at proposal with a `PROHIBITED_ACTION_BLOCKED` audit event (28 entries):

| Area | Actions |
|---|---|
| Publishing | `youtube.community.publish`, `youtube.video.publish` |
| External | **`external.communication.send`** and **`external.financial.action`** (both new and explicit in 3.32G) |
| Data | `production.data.delete`, `production.asset.replace`, `topic_history.modify` |
| Credentials | `oauth.credentials.change`, `oauth.scopes.change` |
| Git | `git.destructive`, `git.branch.change` |
| Processes | `process.daemon.restart`, `process.dashboard.restart`, `render.worker.launch`, `render.worker.restart`, `production.scheduler.start`, `production.scheduler.stop` |
| Production | `production.schedule.change`, `production.cycle.create`, `production.config.modify`, `recovery.deadline.change`, `recovery.job.retry`, `render.video.rerender`, `worker.lock.bypass` |
| Checks | `duplicate_detection.bypass`, `quality_checks.bypass` |
| Autonomy itself | `autonomy.self_policy.change`, `autonomy.code.execute` |

### Gated separately, outside the autonomy registry

- **Community draft generation (3.32E):** operator-configured schedule, Community kill switch, global sentinel. It produces drafts only, and approval stays manual.
- **Community API publishing (3.32F):** `PublishingService` gates (OFF by default, a fresh `API_AVAILABLE` check, content matching the approval, per-post confirmation). It is unreachable from autonomy: `youtube.community.publish` is PROHIBITED.

## 3. Recommendations to approval requests (the bridge)

`POST /api/autopilot/recommendations/{rec_id}/propose` with `{operator, action_type}`. A recommendation can become **one pending approval request** for a fixed follow-up action of its rule family:

| Rule prefix | Allowed follow-ups |
|---|---|
| `prod.`, `recovery.`, `render.` | `diagnostic.readonly.run` (ops_overview, 7 days), `report.operational.generate` (7 days), `operator.notify` |
| `quality.`, `topic.` | `report.operational.generate`, `operator.notify` |
| `api.` | `diagnostic.readonly.run` (ops_daily_summary), `operator.notify` |
| anything else | none: handle it by hand |

**Safety properties:**
- **Deterministic payloads:** payloads come only from rule-generated fields (rule id, title, problem, severity, id), validated by the registry schema.
- **AI text is never used:** the recommendation's free-text `action` and its optional Gemini explanation are never used for a payload or to choose an action. Asking for an unmapped or prohibited action gives 403 and a `BRIDGE_REFUSED` audit event.
- **Level-gated:** the control plane refuses the proposal below RECOMMEND for the action's category. Only open recommendations (NEW, ACKNOWLEDGED or APPROVED) qualify.
- **The bridge cannot approve:** proposals are recorded as `recommendation-bridge`, a reserved identity that can never approve. Approval and execution happen in Autonomy Control.

## 4. Execution safety

| Requirement | Mechanism |
|---|---|
| Stale approvals | Requests expire after the action's TTL. Approvals are HMAC-signed and bound to the payload hash; a changed payload invalidates the approval |
| Duplicate actions | UNIQUE open `dedupe_key` (one open request per action and payload). A COMPLETED or FAILED request never runs again |
| Concurrency | An atomic claim (`UPDATE ... WHERE status='APPROVED'`) inside `BEGIN IMMEDIATE` |
| **Execution leases (new)** | `autonomy_execution_leases`, keyed by `dedupe_key` and taken in the claim transaction, records the owner token, **owner process id** and heartbeat |
| **Heartbeat (new)** | The owner refreshes the lease every 15 s while the handler thread is alive |
| **Timeout safety (new)** | When an attempt times out, the request becomes FAILED but the lease is **held until the handler thread really ends** (the release is registered on the thread's future). Re-proposing the same action and payload therefore cannot run it again while the first thread may still be running |
| **Worker ownership (new)** | A lease whose heartbeat is older than 120 s is reclaimed **only if its owner process is no longer running** (Windows `OpenProcess`). A stale heartbeat with a live owner is never taken over. Reclaims are audited as `LEASE_RECLAIMED` |
| **Circuit breaker (new)** | 3 consecutive failed attempts of one action type (FAILED, TIMED_OUT, VERIFY_FAILED or INTERRUPTED) block it for 60 minutes; the reason is shown in the audit and the dashboard |
| Retry limits and **backoff (new)** | `retry_limit` per action, with a bounded backoff (2 s × n, at most 10 s) before each retry; only transient errors retry |
| Kill switches | Checked at the gate and again before every attempt; activating one mid-run cancels the remaining attempts |
| Startup safety | The startup kill switch is latched at process start; a missing or invalid configuration means active |
| Interrupted execution | `EXECUTING` past its budget becomes FAILED (`INTERRUPTED`), never re-run; a dead owner's lease is reclaimed |
| Production processes | Never restarted automatically: process restarts are PROHIBITED actions, and a stale heartbeat never triggers a restart |
| Audit | Hash-chained, append-only `autonomy_audit_events` (UPDATE and DELETE triggers abort): proposal, approval, claim, attempts, verification, outcome, leases, refusals |

## 5. Dashboard: the Autopilot tab

The tab is read-mostly; every value comes from `GET /api/autopilot` (persisted state). It shows:
- mode, global level and execution possible or blocked;
- kill switches;
- pending approvals, and approved requests awaiting execution;
- per-category level, effective level, observe/recommend/execute, and **why it is blocked**;
- open recommendations with their mapped follow-ups;
- executions with outcomes, live leases (stale-heartbeat warning) and open circuit breakers;
- recent decisions from the audit trail, with refused and blocked events highlighted;
- the full action policy with tiers;
- Self-Healing, Community draft and publishing status;
- audit-chain verification.

**Its only write is "Propose"**, which creates a *pending* request (shown as such). Approve, reject, execute, levels, standing approvals and kill switches remain in Autonomy Control. A test asserts that the Autopilot script never calls execute, approve or set-level.

## 6. Operational runbook

| Task | How |
|---|---|
| Check the state | Open **Autopilot**. Mode OFF plus kill switch `startup_config` is the default and safe state |
| Stop everything immediately | Create `backend/data/AUTONOMY_DISABLED`, or turn the dashboard kill switch on in Autonomy Control. This stops autonomy only, not production |
| Allow proposals | Autonomy Control: set the global level and the category to RECOMMEND. Proposals then queue; nothing executes |
| Allow approved execution (a separate decision) | Create `backend/data/autonomy_startup.json` with `{"autonomy_execution":"enabled"}`, restart the dashboard, and set the category to APPROVED_EXECUTION. Each request still needs an approval |
| Low-risk standing approval (optional) | Autonomy Control → standing approval for an autonomous-eligible action, with a daily cap and an expiry |
| A circuit breaker is open | Investigate the failing action (Executions table). The breaker closes by itself after 60 minutes |
| A lease is stuck (stale heartbeat) | It is reclaimed automatically once its owner process has ended. While the owner runs, nothing is taken over: inspect that process; do not delete the lease by hand |
| An interrupted execution | It shows as FAILED (`INTERRUPTED`). Review it, then propose and approve a new request if still needed |

## 7. Tests

`backend/tests/test_autopilot_332g.py`: 31 cases. Results are in `STAGE_3_32E_TEST_RESULTS.md` §9.

## 8. Limitations

- Python threads cannot be killed. A timed-out handler keeps running until it returns, and its lease prevents any overlap meanwhile.
- PID reuse: if the owner process ended and a new process got the same id, the lease is still treated as owned (fail safe: no takeover). It is released when that process ends.
- The bridge covers 6 rule families. Other recommendations stay advice-only.
- Autonomy stays OFF. Enabling it, granting standing approvals or creating `autonomy_startup.json` is a separate, explicit operator decision.
