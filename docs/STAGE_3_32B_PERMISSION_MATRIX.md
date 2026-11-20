# Stage 3.32B: Permission Matrix

**Source of truth:** `backend/app/autonomy/registry.py`, as code constants. No API, configuration or database row can add an action type, change a spec, or un-prohibit one.

## 1. Levels

The effective level of an action is `min(global level, category level)`. The default is `OFF` everywhere.

| Level | Observe (`record_observation`) | Propose (`propose`, queue) | Execute |
|---|---|---|---|
| `OFF` | no (silently not recorded) | no (`PROPOSAL_REFUSED`, 403) | no |
| `OBSERVE` | yes, audited `OBSERVATION_RECORDED` | no | no |
| `RECOMMEND` | yes | yes, as `PENDING` | no (`EXECUTION_BLOCKED`) |
| `APPROVED_EXECUTION` | yes | yes; auto-approved only with a valid standing approval | yes, with a valid explicit or standing approval, no kill switch active, and within limits |

**Approval is not autonomy.** A human can approve a PENDING request at any level. Approval alone never executes anything, and execution still requires APPROVED_EXECUTION.

## 2. Categories

| Category | Purpose |
|---|---|
| `reporting` | Generate operational reports from read-only production data |
| `recommendations` | Record recommendations for the operator |
| `diagnostics` | Run read-only diagnostics; also the category for engine observations |
| `notifications` | Notify the operator through the local dashboard inbox (no e-mail or external message) |
| `community_drafts` | Prepare YouTube Community post drafts locally; never publishes |

## 3. Permitted action types

**Preconditions common to every execution**, re-checked immediately before it:
- no kill switch active;
- effective level is APPROVED_EXECUTION;
- approval valid, signed and unexpired;
- payload hash matches the approval;
- daily limit not reached;
- not already executing (atomic claim).

| Action ID | Category | Risk | Required permission | Required approval | Extra precondition | Timeout | Retries | Daily limit | Request TTL | Verification | Rollback / compensation |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `report.operational.generate` | reporting | LOW | `autonomy.reporting.execute` | STANDING_OR_EXPLICIT | database readable | 60 s | 1 | 24 | 24 h | Report file exists under `data/autonomy/reports`, parses, SHA-256 matches, `request_id` matches | Delete the report file this execution created |
| `recommendation.record` | recommendations | READ_ONLY | `autonomy.recommendations.execute` | STANDING_OR_EXPLICIT | — | 10 s | 0 | 100 | 72 h | Result holds the approved title and text exactly | None needed (a record); cancel before execution |
| `diagnostic.readonly.run` | diagnostics | LOW | `autonomy.diagnostics.execute` | STANDING_OR_EXPLICIT | diagnostic is allow-listed | 60 s | 1 | 96 | 24 h | Result names the diagnostic with status ok | None needed (read-only) |
| `operator.notify` | notifications | LOW | `autonomy.notifications.execute` | STANDING_OR_EXPLICIT | — | 10 s | 1 | 48 | 24 h | Inbox file has a line for this request with the approved subject | Append a retraction line (the inbox is append-only) |
| `community.draft.prepare` | community_drafts | MEDIUM | `autonomy.community_drafts.execute` | **EXPLICIT_EACH** | — | 30 s | 1 | 6 | 48 h | Draft file exists with status `DRAFT`, `publishable: false` and the approved text | Delete the draft file this execution created |

**Payload schemas.** Unknown fields, wrong types, values out of range and control characters are all rejected.

| Action | Fields |
|---|---|
| `report.operational.generate` | `date` (YYYY-MM-DD, optional), `days` (int 1–30, optional) |
| `recommendation.record` | `title` (≤ 160, required), `text` (≤ 2000, required), `evidence_ref` (≤ 200) |
| `diagnostic.readonly.run` | `diagnostic` ∈ {`ops_overview`, `ops_daily_summary`, `audit_chain_verify`, `kill_switch_status`} (required), `days` (1–30) |
| `operator.notify` | `subject` (≤ 120, required), `message` (≤ 2000, required), `severity` ∈ {info, low, medium, high} |
| `community.draft.prepare` | `text` (≤ 1500, required), `topic` (≤ 200), `source_job_id` (`[A-Za-z0-9_.:-]+`, ≤ 100) |

**Standing approvals:**
- only for `STANDING_OR_EXPLICIT` types;
- 1–720 hours;
- `max_per_day` between 1 and the type's daily limit;
- granted and revoked only by a human operator, and audited (`PERMISSION_CHANGED`);
- used only when the category is at APPROVED_EXECUTION;
- revoking or expiring a grant blocks requests it had already approved.

## 4. Permanently prohibited action types

These are refused at every entry point: propose, approve, standing grant and execute.
- An attempt is audited as `PROHIBITED_ACTION_BLOCKED`.
- The API answers 403.
- No handler can be registered for any of them: both `ControlPlane.__init__` and `handlers.py` refuse at import.

| Action ID | Prohibits | Production functions / routes it guards |
|---|---|---|
| `production.scheduler.start` | Starting the production scheduler | scheduler enable / production start; `/api/control-center/scheduler/enable`; `/api/control-center/production/start` |
| `production.scheduler.stop` | Stopping, pausing or resuming the scheduler | `/api/control-center/scheduler/disable`; `/api/control-center/production/stop`, `/pause`, `/resume` |
| `production.cycle.create` | Creating or triggering production cycles or jobs | `run_cycle_generation`; `/api/pipeline/run`; `/api/jobs/run`; `/api/jobs/recover` |
| `production.schedule.change` | Changing production or publication schedules | schedule draft / activate; unified-schedule activate; publication-schedule activate; `reload_scheduler` |
| `render.worker.launch` | Launching extra render workers or changing concurrency | render workers; `/api/control-center/concurrency` |
| `worker.lock.bypass` | Bypassing or releasing worker / server locks | `ServerLock`; worker single-flight locks |
| `recovery.deadline.change` | Changing, extending or bypassing recovery deadlines or the retry policy | `recovery_settings`; `retry_policy`; operator-retry deadline checks |
| `topic_history.modify` | Modifying topic history or the creative-identity registry | `topic_history.json`; `creative_identity_registry`; `content_ledger` |
| `duplicate_detection.bypass` | Bypassing duplicate detection | creative identity / duplicate checks |
| `quality_checks.bypass` | Bypassing content-quality, QC, integrity or renderer-compliance checks | `qc_service`; `visual_quality_gate`; renderer compliance |
| `oauth.credentials.change` | Changing OAuth clients, credentials or tokens; disconnecting the channel | `youtube_connection_service`; `secure_storage`; `.env.local` |
| `oauth.scopes.change` | Requesting or changing OAuth scopes | YouTube OAuth consent flow |
| `youtube.video.publish` | Uploading, publishing, rescheduling or deleting videos | `upload_short`; `update_or_reschedule_remote_video`; publishing-queue approve |
| `youtube.community.publish` | Publishing a public Community post | any Community posts API call |
| `production.config.modify` | Modifying production configuration, feature flags, operating mode or publishing policy | `creative_policy_config.json`; `/api/control-center/production-config`; operating mode; publishing policy |
| `process.daemon.restart` | Restarting or stopping the daemon | `/api/control-center/deployment/restart` |
| `process.dashboard.restart` | Restarting or stopping the dashboard | `CrazzyDocs-Health-Dashboard` task; `run_dashboard.py` |
| `production.data.delete` | Deleting production data, media or records | `studio.db` tables; media; junk cleaner live mode |
| `git.branch.change` | Changing Git branches | git checkout / switch |
| `git.destructive` | Destructive Git operations | git reset / push --force / clean / rebase |
| `autonomy.self_policy.change` | Autonomy changing its own levels, standing approvals or kill switches, or approving itself | `set_level`, `grant_standing_approval`, kill switch, approve: human operators only |

**Enforcement beyond the registry:**
- **Self-policy.** Operator names starting with `autonomy`, `standing-approval`, `system`, `engine` or `control-plane` are refused by `approve`, `reject`, `cancel`, `set_level`, standing grants and the kill switch.
- **Module boundary.** The autonomy package imports none of the guarded modules. A test parses every module with `ast` and allows only `backend.app.autonomy.*`, `backend.app.core.paths` and `backend.app.services.operational_intelligence` (read-only).

## 5. API permission requirements

| Endpoint | Method | Local only | Same origin | JSON | CSRF token | Explicit confirmation |
|---|---|---|---|---|---|---|
| `/api/autonomy/session` | GET | ✓ | ✓ | — | issues it | — |
| `/api/autonomy/status`, `/permissions`, `/registry`, `/kill-switches`, `/approvals`, `/approvals/{id}`, `/executions`, `/audit` | GET | ✓ | — | — | — | — |
| `/api/autonomy/approvals/{id}/approve` | POST | ✓ | ✓ | ✓ | ✓ | `confirm: true` + exact `payload_hash` |
| `/api/autonomy/approvals/{id}/reject`, `/cancel` | POST | ✓ | ✓ | ✓ | ✓ | reason required |
| `/api/autonomy/approvals/{id}/execute` | POST | ✓ | ✓ | ✓ | ✓ | `confirm: true` |
| `/api/autonomy/permissions/levels` | PUT | ✓ | ✓ | ✓ | ✓ | `confirm: true` + reason |
| `/api/autonomy/permissions/standing-approvals` | POST | ✓ | ✓ | ✓ | ✓ | `confirm: true` + reason |
| `/api/autonomy/permissions/standing-approvals/{type}/revoke` | POST | ✓ | ✓ | ✓ | ✓ | reason required |
| `/api/autonomy/kill-switch/activate` | POST | ✓ | ✓ | ✓ | ✓ | reason required (fast, no extra step) |
| `/api/autonomy/kill-switch/deactivate` | POST | ✓ | ✓ | ✓ | ✓ | `confirm_text: "RESUME AUTONOMY"` + reason |
| `/api/autonomy/assessment/run` | POST | ✓ | ✓ | ✓ | ✓ | operator name |

**There is no generic execution endpoint.** `execute` takes only a stored request id. Its body schema is exactly `{operator, confirm}`, and extra fields give 422.
