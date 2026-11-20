# Stage 3.32D: Recovery Policy

## Principles

1. **Production recovery belongs to the existing scheduler, retry policy and operator.** Self-healing never retries, resumes or re-renders a job, restarts a process, or changes schedules, deadlines, retry policies, configuration, flags or OAuth.
2. **Self-healing produces evidence and staged, reversible results.** It diagnoses, confirms whether a provider has recovered, and re-downloads a failed asset into quarantine. It never places anything into a production job.
3. **Nothing runs without the control plane:** registry, prohibition, levels, kill switches, signed approval, limits, claim, timeout, retry limit, verification and audit.
4. **Success means verified.** A function returning without an exception is not success.

## Planning decision table

| Question | Source | Effect |
|---|---|---|
| Recovery possible? | Classification plus available actions | DETERMINISTIC / CONFIGURATION: no autonomous recovery (operator-only steps listed) |
| Safe? | Worker activity | A worker executing the job: nothing is proposed (`UNSAFE`); the incident is re-planned at the next scan |
| Transient or deterministic? | Classification plus recurrence | Deterministic: "another attempt would fail the same way" |
| Another attempt useful? | Class | TRANSIENT: useful, but retries remain the retry policy's job |
| Inside recovery window? | Checkpoint `recovery_expiration_at` | Reversible actions only while inside; unknown counts as not inside (fail closed) |
| Worker active? | Status RUNNING/RETRYING or a heartbeat ≤ 5 min old | Blocks all actions |
| Already recovering? | Status QUEUED / RETRY_SCHEDULED / HANDOFF_PENDING / RETRYING; open self-healing request | Defer to the scheduler; no duplicates |
| Lock held? | `selfheal_resource_locks` | Blocks that action |
| Resources and cost | Static per action | Shown on every proposal (YouTube quota 0, Gemini 0) |
| Approval needed? | Registry | Always. Standing approval only for LOW diagnostics |

## Limits

| Limit | Value |
|---|---|
| Proposals per incident | 6 |
| Failed recovery actions per incident before no more are proposed | 2 |
| Repeating the same diagnostic for an incident | not within 24 h |
| Daily limits (control plane) | diagnostics 96, provider retries 12, staged reacquisitions 12 |
| Retry limits | diagnostics 1, recovery actions 0 |
| Timeouts | 60 s, 45 s, 90 s |
| Notifications | 5 per scan, 24 per day, 6 h cooldown per key |
| Scans | operator-triggered only, no loop, at least 5 minutes apart unless forced |

## Concurrency and idempotency

- **Control-plane claim.** One atomic conditional UPDATE: one execution per request.
- **Duplicate prevention.** Partial unique indexes on open requests (control plane) and open recoveries (self-healing). The planner refuses an action that is already proposed.
- **Resource locks.** Acquired all-or-nothing per execution: `job:<id>` (or `incident:<id>`), plus `provider:<name>` or `asset:<url hash>`. Released on finish; expire after (timeout × (retries + 1) + 60 s).
- **Engine `execute` re-checks preconditions.** If they fail, the recovery becomes `UNSAFE` and its request is cancelled. Handlers re-read the live job state, so a request executed from Autonomy Control is protected too.

## Timeouts, interruption and retention (final audit)

- **A timeout is not proof that an operation stopped.** Python cannot stop the worker thread. The recovery is recorded FAILED with "the operation may still have been running". Its resource lock stays held until the thread ends (or the lock expires), so no second execution can overlap it. Network operations also have their own socket timeouts and a 60 s download deadline.
- **A process stop during execution** is reconciled by the control plane to FAILED (INTERRUPTED) and never re-run. The partial file is removed, by the action itself or by quarantine retention.
- **Quarantine retention:** staged files are kept 7 days and reports 30 days. Notification keys are kept 30 days. Finished recoveries are capped at 20,000.

## Verification policy

| Action | Handler verification | Engine independent check |
|---|---|---|
| Diagnostic | Result names the diagnostic and incident with status ok; a report file exists in the reports folder with a matching SHA-256 | Same, re-read from disk |
| Provider retry | One uncached HTTP 200 with the provider's documented JSON structure (errors, cached or malformed answers fail) | Stored result consistent |
| Staged reacquisition | Download guards passed (allow-listed public hosts on every hop, 25 MB / 60 s, media type, signature, Pillow decode); file inside the staging folder; SHA-256 matches on re-read; positive dimensions | File still present and unchanged |

Outcomes:
- **Verification FAILED:** the recovery is FAILED and compensation removes the staged file or report.
- **Cannot verify (e.g. ffprobe unavailable for a video):** the result is `UNVERIFIED` and the recovery is FAILED.
- **Recovery `RECOVERED`:** the action was verified. It does not complete the production job; `persistent_jobs` is never written.

## Status model

**Incident path:** DETECTED → DIAGNOSING → DIAGNOSED (or UNSAFE) → mirrors its open recoveries (RECOVERY_PROPOSED / AWAITING_APPROVAL / APPROVED / EXECUTING / VERIFYING) → RECOVERED / FAILED / EXPIRED / CANCELLED / DIAGNOSED.

**Recurrence:** if the failure recurs after a result, the incident re-opens (only results finished after the latest occurrence count).

**Recovery path:** RECOVERY_PROPOSED → AWAITING_APPROVAL → APPROVED → EXECUTING → VERIFYING → final status.

| Final status | Meaning |
|---|---|
| RECOVERED | A recovery action was executed and verified |
| DIAGNOSED | A diagnostic completed and was verified |
| FAILED | Execution or verification failed, or the result was unverified |
| EXPIRED | The approval request expired |
| CANCELLED | Rejected or cancelled by the operator |
| UNSAFE | Preconditions no longer held at execution |

## Learning policy

- Every finished recovery is recorded with its approval outcome, execution result, verification result and final status.
- A later occurrence of the same signature marks earlier outcomes as recurred.
- **The planner stops proposing an action** that failed on ≥ 2 attempts for a signature, or whose verified successes were each followed by recurrence.
- The 3.32C recommendation engine reports success patterns, ineffective strategies and repeated deterministic defects.
- **Nothing changes production retry policies automatically.**
