# Stage 3.32D: Self-Healing Operations — Implementation

**Status:** implemented and tested in the development worktree.
- Not activated, not deployed, not committed.
- Production is unchanged: the `self_healing` category is OFF (it inherits OFF) and the startup kill switch is active.

## 1. Architecture

```
3.32A Operational Intelligence (read-only bundle + failure list)
        │
        ▼
EvidenceSource ── per-job detail (mode=ro): checkpoint recovery state, attempt history, events, renderer, progress record
        │
        ▼
classification ──► DiagnosticEngine ──► RecoveryPlanner ──► SelfHealingEngine ──► ControlPlane.submit / approve / execute
 (signature +       (evidence,           (window, worker,     (gating, incidents,     (3.32B: registry, prohibition,
  recurrence)        correlation,         scheduler state,     proposals, sync,        levels, kill switches, signed
                     root-cause status)   locks, learning,     verification,           approval, payload hash, limits,
                                          cost, approval)      learning, notify)       claim, timeout, retries, audit)
                                                                    │
                                                                    ▼
                         self_healing handlers (run / verify / compensate) ──► services.py adapters (only I/O)
                                                                    │
                                                                    ▼
                         selfheal_* tables (incidents, recoveries, outcomes, locks, notifications, scan runs)
                                                                    │
                                                                    ▼
                         3.32C recommendation rules (read-only learning: success patterns, ineffective strategies,
                         repeated deterministic defects)
```

**What is reused, not duplicated:**
- **3.32A:** failure occurrences, normalization (`error_template`, `pattern_key`) and root-cause groups.
- **3.32B control plane:** gating, approval signing, claim/timeout/retry/verify execution, the audit chain and the dashboard-inbox notifications.
- **Existing retry policy:** its classification is reported for comparison only and never changed.
- **Existing services:** `AssetDownloader`, the media provider clients and the ffprobe path helper.

**There is no new scheduler and no background loop.** A scan runs only when an operator presses *Scan now* (or a future, separately approved caller asks). Job retries, re-renders and restarts stay with the existing scheduler and the operator.

## 2. Files

**New package: `backend/app/autonomy/self_healing/`**

| File | Purpose |
|---|---|
| `classification.py` | Six-class classifier (signature catalogue + recurrence) |
| `evidence.py` | Read-only evidence access |
| `diagnostics.py` | Evidence collection, correlation, root-cause status |
| `planner.py` | Recovery planner, resource keys, URL/provider/query validation |
| `store.py` | `selfheal_*` tables, locks, notification limits, learning, bounded history |
| `services.py` | Adapters (files, ffprobe, providers, downloader); replaceable in tests |
| `handlers.py` | Handlers for the three permitted action types |
| `engine.py` | Orchestration, operator actions, sync, independent verification, notifications, optional Gemini wording |

**Modified files**

| File | Change |
|---|---|
| `autonomy/registry.py` | New category `self_healing`, 3 permitted action types, 5 new prohibited types |
| `autonomy/handlers.py` | Registers the self-healing handlers |
| `autonomy/control_plane.py` | Reserved identities `self-healing*` / `selfheal*`; reserved-name matching ignores separators |
| `recommendations/engine.py`, `signals.py`, `rules.py` | Read-only learning input + 3 rules |
| `main.py` | 7 endpoints |
| `templates/dashboard.html` | Self-Healing tab |

**Tests**
- New: `backend/tests/test_self_healing_332d.py`.
- Updated: the 3.32B import allow-list now includes the self-healing handler module.

## 3. Failure classification

See `STAGE_3_32D_FAILURE_CLASSIFICATION.md`.

## 4. Diagnostic engine

**Evidence collected for each incident:**
- error message, exception type, stage, job, attempt number, first and last seen;
- log context (lifecycle events and the progress-record error);
- related failures with the same signature;
- renderer record, recorded media/asset metadata, recovery history (attempts, recovery reason, checkpoint recovery state).

Anything not recorded is listed under `missing` and lowers `completeness`.

**Correlation:**
- occurrences, distinct jobs and affected stages;
- the same error on retries of this job;
- recurring dimensions (only when the error text contains them, otherwise reported as unknown);
- whether earlier retries succeeded;
- whether the failure is likely deterministic.

**Root-cause status**
- `CONFIRMED_MECHANISM`: the error states *what* failed; *why* is explicitly "not proven".
- `SUSPECTED`
- `UNKNOWN`

## 5. Recovery planner

For each incident the planner answers:
- whether recovery is possible and safe;
- transient or deterministic, and whether another attempt is useful;
- whether the job is inside its recovery window (from the checkpoint; unknown if not recorded);
- whether a worker is active (status RUNNING/RETRYING or a fresh heartbeat);
- whether the job is already being recovered (scheduler statuses QUEUED/RETRY_SCHEDULED/HANDOFF_PENDING/RETRYING, or an open self-healing request);
- held locks, required resources, expected cost (time, network requests, YouTube quota 0, Gemini 0) and the approval requirement.

**Proposals are refused when:**
- a worker is active (the incident is `UNSAFE`);
- the action is already proposed;
- a diagnostic already completed within 24 h;
- a lock is held;
- the incident limit is reached (6 proposals, or 2 failed recovery actions);
- learning shows the action is ineffective for the signature.

**Reversible actions** additionally need an open recovery window and no scheduler recovery in progress.

**Operator-only steps** name the prohibited registry entry they correspond to, e.g. `recovery.job.retry` or `render.video.rerender`.

## 6. Action registry

See `STAGE_3_32D_PERMISSION_MATRIX.md`.
- **Permitted:** `selfheal.diagnostic.run` (LOW, standing approval allowed); `selfheal.provider.retry_readonly` and `selfheal.asset.reacquire_staged` (MEDIUM, explicit approval for every request).
- **Prohibited:** job retry, re-render, worker restart, asset replacement inside a job, and generic code/shell execution, in addition to the 21 prohibitions from 3.32B.

## 7. Control-plane integration

**Every action is proposed with `ControlPlane.submit/propose` and runs only through `ControlPlane.execute`.** The nine required checks are each enforced:

| Check | Where it is enforced |
|---|---|
| 1. Registry entry exists | `require_permitted` |
| 2. Not prohibited | `require_permitted` + `_gate` |
| 3. Global level permits | `_effective` |
| 4. Category level permits | `_effective` |
| 5. No kill switch active | `_gate`, re-checked before each attempt |
| 6. Approval valid | HMAC-signed approval, payload hash, expiry |
| 7. Job and resource preconditions | Planner; engine pre-check before execute; handler re-reads the live job state |
| 8. No duplicate in progress | Unique open-request indexes in both tables; atomic claim; resource locks |
| 9. Limits not exceeded | Daily limits, retry limits, per-incident caps |

**Identity rules**
- **Engine identity** `self-healing-engine` is reserved. Reserved-name matching ignores spaces, `.`, `_`, `@` and `-`, so variants like `Self.Healing` are also refused.
- **No self-approval or self-change:** the engine can never approve, reject, execute as an operator, change levels, grant standing approvals or touch kill switches (tested for 10 identity variants through the engine, the control plane and the API).
- **Attribution:** every request the engine creates records `proposed_by=self-healing-engine`.

## 8. Approval flow

1. **Proposal.** A scan at RECOMMEND or above creates a PENDING request, and the recovery is shown as `AWAITING_APPROVAL`.
2. **Approval.** The operator approves on the Self-Healing tab or in Autonomy Control. The approval is bound to the payload hash shown and signed.
3. **Execution.** It runs only when all of these hold:
   - the operator presses *Execute*;
   - the `self_healing` level is `APPROVED_EXECUTION`;
   - no kill switch is active;
   - the job and resource preconditions still hold. If they do not, the recovery becomes `UNSAFE` and its request is cancelled by that operator.
4. **Standing approvals** are possible only for LOW-risk diagnostics. They are granted by a human with a cap and an expiry.

## 9. Verification

There are two layers.

**1. Handler `verify`, inside the control-plane claim.** It returns a verdict:
- `VERIFIED`, `FAILED` or `UNVERIFIED`;
- on failure, compensation removes the action's own artefacts.

**2. Engine independent check (`VERIFYING`), run on the stored result without re-executing anything:**
- the report or staged file still exists and its SHA-256 matches;
- the diagnostic result is consistent;
- the provider response was parsed.

**Rules:**
- `RECOVERED` (recovery actions) and `DIAGNOSED` (diagnostics) require both layers.
- A completion without a verdict is `UNVERIFIED`, and it is recorded as `FAILED`, never as success.
- The production job is never marked completed; `persistent_jobs` is never written.

## 10. Learning

`selfheal_outcomes` stores, per recovery:
- signature, class, findings, action, approval outcome, execution result, verification result and final status;
- whether the failure recurred afterwards (`recurred`, `recurrence_count`).

**Recurrence re-opens the incident.**

**How learning is used:**
- The planner stops proposing actions that failed on ≥ 2 attempts, or whose successes were followed by recurrence every time.
- The 3.32C engine reads this data read-only (it never creates tables) through 3 new rules:
  - `recovery.selfheal_success_pattern`
  - `recovery.selfheal_ineffective`
  - `recovery.repeated_deterministic_defect`
- **Retry policies are never changed.**

## 11. Dashboard

The new **Self-Healing** tab shows:
- KPIs: open incidents, deterministic incidents, awaiting approval, recovery success rate and mode;
- the failure classes and recovery statistics;
- the awaiting-approval queue with Approve (showing the payload and hash), Reject (reason required) and Execute;
- incident cards with diagnostics (root-cause status, findings, correlation, planner checks, proposed and operator-only actions, evidence, approved diagnostic results, recoveries and the audit trail);
- recovery history and the learning table.

Writes use the 3.32B CSRF token and operator identity. The tab refreshes only while it is open (every 2 minutes).

## 12. Notifications

Notifications use the existing autonomy notification channel, `operator.notify` (the dashboard inbox; no e-mail). They are gated by the `notifications` level and need approval like any action.

**Kinds:**
- critical failure;
- repeated deterministic error;
- recovery-window expiration;
- one aggregated "requests await approval" notice per scan;
- recovery failed;
- recovery verified.

**Limits:**
- a 6-hour cooldown per key;
- at most 5 per scan and 24 per day;
- when the notifications level is below RECOMMEND, nothing is queued; these are counted as `suppressed_level`.

The production `NotificationManager` (e-mail/desktop) is not used: autonomy notifications are defined in 3.32B as inbox-only.

## 13. Gemini

Gemini is off unless `CRAZZYDOCS_SELFHEAL_EXPLAINER=gemini` is set. When enabled:
- it writes at most 2 wording explanations per scan for new incidents;
- it never decides or executes anything;
- failures are ignored.

## 14. Database changes

Six new tables, created on first use by the engine (not by the recommendation engine):
- `selfheal_incidents`
- `selfheal_recoveries`, with a partial unique index on open (incident, action, payload)
- `selfheal_outcomes`
- `selfheal_resource_locks`
- `selfheal_notifications`
- `selfheal_scan_runs`

History is bounded:
- terminal incidents older than 90 days are pruned;
- at most 5,000 incidents and 10,000 outcomes are kept;
- locks expire.

No production table is altered.

## 15. Performance

Dry run on a scratch copy of the 2026-11-20 backup, not the live database:

| Scan | Time | Result |
|---|---|---|
| Cold | 1.3–2.2 s | 13 failure records, 10 incidents, 7 diagnostic proposals |
| Incremental rescan | 0.12–0.35 s | 0 new incidents |

There is no polling loop, no heavy model and no paid service. The default scan makes 0 network requests.

## 16. Activation (requires explicit approval; not done)

Activation needs an operator to do all of the following:
1. Create `backend/data/autonomy_startup.json`.
2. Restart the dashboard.
3. Raise the global and `self_healing` levels.
4. Optionally grant a standing approval for diagnostics.
