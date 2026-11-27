# Operator-dependency reduction

**Goal:** CrazzyDocs does routine local work on its own, and asks the owner only when it cannot safely proceed without them: an external authorization, a creative or other human decision, a security boundary, or an irreversible action.

**Not in scope:** unrestricted Autopilot or public publishing (both unchanged and still off). No new scheduler, worker or daemon was added. Every gate that blocked execution before still blocks it. What changed:
1. How the control state is represented and reported.
2. One bounded local recovery joined the automatic policy.
3. Attention and self-healing reporting are reconciled with live state.

## 1. Audit: where operator requests came from (live, 2026-11-27 11:00–12:30 IST)

| Source | What it showed | Genuine? | Root cause |
|---|---|---|---|
| Header pill `Autonomy: BLOCKED (1 kill switch)` and the kill-switch cards on the Autonomy, Autopilot and controller views | The missing optional `autonomy_startup.json` was listed as an **active kill switch** (`kill_switches.active = ["startup_config"]`), next to the real emergency switches | No | `kill_switches()` put the startup configuration in the same list as the dashboard switch and the sentinel file. The actual control state was RESTRICTED, not an emergency stop. |
| Self-Healing and Recommendation state (`kill_switches: ["startup_config"]`) | Same confusion | No | Same field |
| Autopilot controller *"has not ticked for more than 3 minutes (scheduler daemon down or running older code)"* | A daemon failure | No: the daemon was healthy (deployment status CURRENT, heartbeat fresh) | `tick()` returned before writing its heartbeat whenever Autopilot was OFF. The last heartbeat was PID 3032 at 02:36 IST, two daemon restarts earlier. |
| Needs you: *"Topic supply is running low… decide on the inactive flags production_query_rotation and exhaustion_recovery"* | A decision request | No: both flags have been ON since 05:45 IST | The evidence counted the last 24 discovery runs including 17 from before the activation. The 7 runs since activation had 9–14 eligible topics each. |
| Needs you: *Pexels: key missing* | A credential request | Optional only | Pixabay and the other photo providers cover the work, so nothing is blocked. |
| Needs you: *Creative decision: weak_hook / storytelling / ending_payoff* | Decisions | **Yes**: human creative judgement | Shown as generic "decision"; now typed `creative_decision`. |
| Self-Healing *"14 open incidents"* | 14 current problems | No: all 14 last occurred on 26 Nov, none is current | DIAGNOSED counted as open. A completed diagnostic was also counted as "succeeded" next to real recoveries. |
| Recommendation Center *"advice for the operator… nothing is executed"* together with *"routine follow-ups run automatically"*; mode RECOMMEND at level OBSERVE | Contradiction | n/a | Copy predates the remediation policy. |
| Availability: two percentages (health card, report) | Different numbers, no window | n/a | Neither stated its observation window; outages were not listed. |

## 2. Action-level inventory and the local autonomy policy

**A = autonomous local**, **B = controlled autonomous** (signed code policy, bounded, verified, reversible), **C = operator-authorized**.

| Action | Local / external | Reversible | Permission before | Now | Class |
|---|---|---|---|---|---|
| Read-only diagnostics (`diagnostic.readonly.run`, `selfheal.diagnostic.run`) | Local | Yes (read-only) | Policy-automatic in RESTRICTED | Same | A |
| Operational reports, recording recommendations, local notifications | Local | Yes | Policy-automatic in RESTRICTED | Same | A |
| Provider re-check (`selfheal.provider.retry_readonly`) | External read-only | Yes | Policy-automatic in RESTRICTED | Same | A |
| Topic discovery, scoring, dedup; script; Shorts TTS and render; Hindi localization steps; QC; tests | Local (free providers) | n/a | Scheduler / coordinator; never gated by the control state | Same (verified unchanged) | A |
| Retry of transient failures | Local | n/a | Scheduler retry policy (deterministic QC failures are never retried) | Same | A |
| Stale worker and job recovery | Local | n/a | Scheduler recovery loop and worker registry (process identity and ownership verified) | Same | A |
| Safe temp-file cleanup | Local | Yes (reversible quarantine, `restore(run_id)`) | Daily dry run; live run needs an explicit confirmation | Same | A (dry run) / C (live run) |
| **Staged asset re-acquisition** (`selfheal.asset.reacquire_staged`) | External download into a local quarantine | **Yes**: never placed into a job; compensation deletes its staging folder | NORMAL only, at RECOMMEND (effectively never in production) | **Policy-automatic in RESTRICTED and NORMAL at OBSERVE.** Bounds: 12 a day, 25 MB, 60 s, allow-listed HTTPS hosts, URL must be in the incident evidence, job idle. Verified by signature, decode and SHA-256. | **B** |
| Production dispatch (`production.*.dispatch`) | Local | n/a | APPROVED_EXECUTION, standing approvals and `autonomy_startup.json` | Same | C (operator enables Autopilot) |
| Community draft preparation | Local | Yes | Explicit approval | Same | C (content in the owner's voice) |
| Publishing (YouTube video, Community), OAuth or scope changes, new channel | External, irreversible | No | Prohibited / operator | Same | C |
| Kill switches, autonomy levels, standing approvals, startup config | Security boundary | n/a | Operator | Same | C |
| Deletion without recovery, git destructive, restarts, schedule or config changes, spending | Irreversible or security | No | Prohibited | Same | C |

## 3. Control-plane concepts (code, API, dashboard)

| Concept | Field | Meaning |
|---|---|---|
| Emergency stop (kill switches) | `kill_switches.active`, `any_active`, `emergency_stop` | The dashboard switch, the sentinel file, or `CRAZZYDOCS_AUTONOMY=off` at start (`startup_environment`). Any one stops **all** autonomous execution, including the automatic policy. |
| Optional startup configuration | `kill_switches.startup_config` `{enabled, state: ENABLED / ABSENT / INVALID / NOT_ENABLED, detail, path}` | Not a kill switch. Absent means control state RESTRICTED. |
| Execution gate | `kill_switches.execution_gate_closed`, `execution_gate` (one sentence) | Closed on an emergency stop **or** without the startup configuration. Every internal gate for actions outside the built-in policy uses it, so **blocking behaviour is unchanged**. |
| Control state | `control_state` (in status, permissions, Autopilot, recommendations and self-healing state) | EMERGENCY_STOP / RESTRICTED / NORMAL |
| Restricted policy | `POLICY_AUTOMATIC[RESTRICTED]` | The allow-listed local actions above. Each still needs its category at least OBSERVE, the daily limits, the circuit breaker and postcondition verification. |
| Autopilot controller state | `/api/autopilot/controller` | OFF / STARTING / ON / STOPPING / BLOCKED. OFF does not stop the scheduler, the coordinator, self-healing diagnostics or the automatic policy. |
| Global phase, category levels, standing approvals | unchanged | Operator-owned |

`status.autonomy_enabled` now means "some category observes or more, and no emergency stop". RESTRICTED still runs its policy, so the header pill shows `Autonomy: OBSERVE · restricted`, not `BLOCKED`.

## 4. Self-healing: outcome-driven reporting

Each incident carries `outcome` (`backend/app/autonomy/self_healing/outcomes.py`) with four separate facts:

- **`diagnostic_succeeded`**: a verified diagnostic. It is never a recovery.
- **`recovery_executed`**: an allow-listed action ran.
- **`issue_resolved`**: the recovery was **verified** and the failure has not recurred since.
- **`output_completed`**: the job is COMPLETED in `persistent_jobs`.

`resolved_by` names who fixed it. When the scheduler's own retry completed the job, self-healing takes no credit. `current` is true while the job is active, or while the last occurrence is under 6 h old and nothing is resolved or completed. Older incidents are **history**, reported but not counted as current. Deterministic QC failures are still never retried (scheduler retry policy, unchanged).

**Live result:** 14 "open" incidents became **0 current and 14 historical** (11 diagnosed, 3 detected, 0 recovery actions).

## 5. Attention feed

- **Topic supply is reconciled with the live flags.** When both flags are ON, only runs that ran with both flags count, and the operator is never asked to decide on flags that are ON. A decision appears only with at least 6 such runs showing low supply, and then asks about widening sources.
  - Live result: *healthy* (7 runs, none low).
- **Missing credentials:** one whose work is covered by a working fallback moves to `optional_improvements` (shown as "Optional: nothing is blocked"). A missing credential with no fallback stays a request, typed "External authorization".
- **Item types:** creative proposals are `creative_decision` (human judgement), and each item shows its type.
- **Availability:** each summary states its window (first to last probe) and lists every outage interval with its start, recovery time and reason. The report states that its figure covers the report's period.

## 6. Remaining operator decisions

These are genuine; nothing local can resolve them.

1. Creative decisions: the weak hook, storytelling and ending dimensions (human creative judgement).
2. The Hindi channel: connect its own Google account. Hindi publishing is a separate authorization.
3. Optional: a Pexels key (Pixabay works without it).
4. Optional: `autonomy_startup.json` and APPROVED_EXECUTION, only if the owner wants Autopilot to dispatch production. The scheduler already produces every cycle without it.
5. Community API publishing: Google pilot access and the `youtube.force-ssl` scope (external).

## 7. Remediation closure (2026-11-27, 20:20–21:30 IST)

Builds on §1–§6 (deployed in `2c31dc8`). The live state before the closure work was verified, not assumed:
- control state RESTRICTED (not an emergency stop);
- needs-you held only the 3 creative decisions; Pexels was listed as optional;
- 22 actions had run automatically; the controller was responding, as daemon PID 20704.

| Item | Change | Evidence |
|---|---|---|
| **Controller health** | It used to count as "responding" on heartbeat age alone. Now `ProductionController.controller_health` requires three things: a fresh heartbeat (≤ 180 s), a **live process**, and that process being the **registered scheduler daemon** (the deployment record written at daemon start). Otherwise it reports the exact case: no tick, stale record (dead PID), or possible PID reuse (a live PID that is not the daemon). Status exposes `heartbeat_age_sec`, `pid_alive`, `daemon_pid` and `identity`. | Live, read-only: identity "scheduler daemon", PID 20704, heartbeat age 35–44 s. A dead PID gives "stale record"; another live PID gives "possible PID reuse". Tests cover three consecutive ticks while OFF. |
| **Self-healing outcomes** | Six distinct facts per incident: diagnostic succeeded, recovery **approved** (policy / standing / explicit), recovery executed, recovery **verified**, issue resolved (verified with no recurrence), output completed (`resolved_by` credits the scheduler when its retry finished the job). The stages run in that order; the dashboard shows each count. | Tests: approved ≠ executed ≠ verified ≠ resolved; a rejection never counts as approved. |
| **Readiness metric** | The Autopilot readiness availability check states its own window (the phase period), its denominator (probes) and its outage count. It is never silently compared with the health card's last 24 h, which also lists every outage. | Test |
| **Attention groups** | Each request carries a `group`: security / approval, external authorization (credentials, Hindi publishing authorization), creative decision, or local operations. The feed returns counts per group, and the dashboard sorts and labels by group. | Test |
| **Autonomous Hinglish localization** | Inside the existing coordinator tick, every 10 min: when the LOCALIZATION queue is enabled and idle, it enqueues the newest completed English Short (last 26 h, English artifacts present, no localization yet). The cap is `localization_auto_daily_max` per IST day (default **6**, 0–24, coordinator config; 0 turns it off). Renders still run only in Shorts gaps in the shared slot; QC, duplicate mapping and the publishing block are unchanged; operator enqueue still works. | Tests: newest first, test jobs and missing files skipped, one at a time, cap and switch, never runs with injected test executors. |

**Not changed:** the 24-cycle schedule, the English pipeline, OAuth, channel identities, the publishing policy, autonomy levels, standing approvals and kill switches. No new scheduler, daemon or worker; no paid service.

**Still requiring authorization (genuine):**
- creative decisions (hook, storytelling, ending);
- connecting the Hindi channel and authorizing Hindi publishing;
- Autopilot production dispatch (`autonomy_startup.json`, APPROVED_EXECUTION, standing approvals);
- Community API access (Google pilot and `force-ssl` scope);
- any destructive, irreversible, spending or account-level action.
