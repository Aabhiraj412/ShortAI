# Remediation: stability and autonomy (before Stage 4.01)

**Status (2026-11-27 IST):**
- Stage 3.37 and WS1 are committed (`13e846b`, `7b4f632`), pushed and deployed (04:34 IST).
- WS1 (startup race), WS2–WS7 and the attention feed are implemented and tested in the dev worktree. Their deployment status is in §10.
- Evidence comes from the production database (read-only or a snapshot), the dashboard logs, the health-monitor ledger, and reproduction harnesses.

## 1. Starting state (measured, not assumed)

| Item | Finding |
|---|---|
| Stage 3.37 | Implemented and uncommitted. Its full suite (1834 passed, 4 baseline failures) had completed; nothing was deployed. |
| Running code | Daemon PID 3032 and dashboard PID 4920 both on `fed2845`. `main` was at `a7b0b81` (junk-cleaner fix), so the `STALE_CODE` warning was genuine. |
| Dashboard | Liveness probes timed out: 77 % availability since 2026-11-26 03:00 UTC, p50 1.1–2.3 s per hour until 17:00 UTC. Trivial endpoints took 7–10 s; `/api/control-center/deployment` took 19.75 s. The dashboard used about one full CPU core: one thread, the event loop, had 2027 CPU-seconds since start. |
| Restarts | One restart in the last day (the earlier 27 are historical). Every deploy-time Task Scheduler launch died (`LastTaskResult 1`), and the monitor relaunched it about one minute later. |
| Autonomy | Global OBSERVE. The startup switch was active (no `autonomy_startup.json`). That counted as a kill switch, so self-healing and recommendation runs were **BLOCKED** every time, and nothing executed. |
| Topics | 9 of 23 runs on 2026-11-26 had at most one eligible topic, and several failed the integrity gate. |
| Providers | Gemini showed as "CONFIGURED". In fact 16 of the last 20 scripts used the extractive fallback. |

## 2. WS1: dashboard and daemon stability

**Root causes (each one reproduced):**
1. **Windows Proactor event loop.**
   - Aborted browser connections produced `WinError 64` in `IocpProactor.accept` and `ConnectionResetError` in `_call_connection_lost` (21 occurrences in the error log).
   - Reproduction (`loop_repro.py` + `load_reset.py`: same client, polling plus 6 reset connections per second): Proactor served **16–20** requests in 45 s; the Selector loop served **152–156**. The repeated "requests finish in ~10 s bursts" pattern matches production.
   - Fix: `asyncio:SelectorEventLoop` on Windows (`run_dashboard.dashboard_loop_setting`). `CRAZZYDOCS_DASHBOARD_LOOP=proactor` reverts.
2. **Blocking work on the event loop.**
   - 101 of 220 async routes never awaited, so their SQLite and subprocess work ran on the loop.
   - The 53 read-only GET handlers among them are now `def` (threadpool). Liveness stays on the loop by design. A regression test keeps it that way.
3. **`/api/control-center/resources`** spawned `nvidia-smi` and `tasklist` and slept 100 ms on every poll of every tab. It is now shared for 10 s.
4. **Deploy-time launches died silently.**
   - With `--open-browser`, the browser thread and the main thread wrote `dashboard_startup_status.json` through one shared `.tmp` file within 2 ms of each other. The second `os.replace` raised before `uvicorn.run`, and under pythonw the traceback was lost (exit code 1).
   - Reproduced on the old code: **216 of 400** concurrent writes raised `PermissionError`.
   - Fix: a lock, per-thread temp files, bounded retries, and the writer never raises. `run_dashboard` now logs any unhandled exception.
5. **pythonw has no stdout.** The console log handler was removed in that case.

**New diagnostics:** `GET /api/health/loop` reports event-loop lag and a bounded stack sample of the loop thread (`core/loop_diagnostics.py`). It costs under 1 % of a core.

**Measured after deploy (04:35 IST, dashboard PID 17252):**
- liveness 3 ms (was 5–10 s);
- `/api/control-center/deployment` 43 ms (was 19.75 s);
- loop lag p50 0 ms, p95 16 ms, max 110 ms; loop thread 99 % idle;
- dashboard CPU 0.3 % of one core (was about 90 %).

The old process degraded with uptime, so §10 lists the long-run re-check.

**Daemon:**
- The Stage 3.37 coordinator tick is throttled to once per 60 s.
- When long-form is idle (the default) it does not sample the host.
- The tick now runs as a **background task** (at most one in flight) and is never awaited by the recovery loop.
- Reason: the full suite exposed `test_daemon_does_not_exit_while_render_in_progress` failing intermittently (1 of 3 runs), because the first awaited tick delayed recovery dispatch and the controlled-restart check. After the fix it passed 5 of 5 runs, and a test now guards it.

## 3. WS2: autonomy control states and kill switch

Three states, in `control_plane.kill_switches()` and `control_state()`:

| State | When | What runs automatically |
|---|---|---|
| `EMERGENCY_STOP` | Dashboard kill switch, sentinel file, `CRAZZYDOCS_AUTONOMY=off`, or unreadable configuration. Independent of the startup config; can be activated without the database. | Nothing. |
| `RESTRICTED` | No operator startup config (absent, invalid, or not `enabled`). This is the **production state now**. | `POLICY_AUTOMATIC[RESTRICTED]`: operational report, record recommendation, read-only diagnostic, local inbox notification, self-healing diagnostic, read-only provider re-check. Each needs its category at least OBSERVE. |
| `NORMAL` | Operator-created `autonomy_startup.json` with `{"autonomy_execution":"enabled"}`. | The above, plus staged asset re-acquisition (needs RECOMMEND). Every other action keeps its explicit or standing approval and level. |

Rules for policy approvals:
- They are signed like standing approvals (`approved_by = policy:<STATE>`).
- They are re-checked at the gate and before every attempt.
- They follow daily limits, the circuit breaker and handler postcondition verification.
- Turning a category OFF disables them.

Production dispatch, publishing, configuration, credentials, restarts and deletion are **never** in the policy. Those are not even permitted actions.

Scheduler ownership (3.36) is unchanged: it still needs `autonomy_startup.json`, APPROVED_EXECUTION and standing approvals. No `autonomy_startup.json` was created. No level, standing approval or kill switch was changed.

**Tests:** the legacy 3.32B/C/D fixtures disable the policy so they keep verifying the explicit approval machinery. Three legacy expectations changed on purpose: "missing startup config = BLOCKED" became "missing startup config = RESTRICTED". Each is documented in the test.

## 4. WS3: topic discovery

Evidence from 48 runs on 2026-11-25/26:
- **Discovery breadth is fixed.** Every run sees exactly 52–53 candidates. `topic_discovery_rotation` has 0 rows (rotation is inactive). Wikipedia search returned 481 duplicates out of 552.
- **The pool drains through the day.** Eligible candidates fall from about 10 to 0–1 per run.
- **The identity registry removes most of what ranking left.** 3–6 identity rejections per run: subjects already selected by another job passed ranking and enrichment, inflated `eligible_count`, and only the final integrity gate removed them.

Fixes (no flags, no gate weakened):
1. **Identity pre-check before enrichment.** The registry's `topic_conflict` for the current job runs at discovery, the news-lead path and recovery. The final `filter_fresh` integrity gate is unchanged. Rejections are recorded with stage `identity_precheck` and the conflicting job.
2. **Ledger re-sighting no longer overwrites stored data** (3.31 report §9.10 item 1). Empty summary, category, entities, score breakdown and v2 prediction keep the stored values. Each run's v2 prediction is also kept as a `classification/PREDICTED` event.

**Activated with operator approval (2026-11-27 05:45 IST):** the implemented and tested flags `production_query_rotation` and `exhaustion_recovery` (3.31C/D) are now **on**.
- Change: only those two keys were added to `creative_policy_config.json`; the previous file is backed up.
- The daemon restarted (PID 4692) to load them.
- Effect is to be measured on eligible counts and integrity-gate failures. Topics are still never recycled.

## 5. WS4: provider status accuracy

`provider_audit.production_evidence()` is read-only. `state_with_evidence()` gives each provider a state and a one-line reason:

| Provider | Was | Now (evidence) |
|---|---|---|
| Gemini | CONFIGURED | **DEGRADED**: 4 of the last 20 scripts came from the model. All other drafts failed with HTTP 429 or 503/504 (14 × 429, 10 × 503/504 in the last five jobs). |
| Edge TTS | CONFIGURED | HEALTHY: voiced 19 completed productions in 24 h. |
| GNews / Wikipedia + Phys.org / NASA (topics) | UNVERIFIED / CONFIGURED | Candidates and acceptances per family over the last 24 runs. NASA as a topic source: 72 candidates, **0 accepted**. |
| Internet Archive | NOT_USED (with 17 OK requests) | AVAILABLE_NOT_SELECTED: reachable, but none of its assets was selected in the last 20 renders. |
| NASA imagery | CONFIGURED | HEALTHY: 1 asset in the last 20 renders. |
| SMTP | UNVERIFIED | HEALTHY: 21 notifications sent in 24 h. |
| Pexels | KEY_MISSING | Unchanged (no key was invented). |

Credential values are never returned; a test covers it.

## 6. WS5: recommendation centre

**Root causes:**
- The recommendation engine was BLOCKED by the startup switch.
- The bridge only proposed actions when an operator clicked.

**Now:**
- Analysis runs in RESTRICTED.
- `Autopilot.auto_bridge()` runs as an observation-cycle step (`auto_actions`). It executes the policy-automatic follow-ups of open recommendations through `ControlPlane.submit` (every gate, signed policy approval, verification, audit).
- It runs at most 4 per cycle.
- Each (recommendation, action) runs once, persisted in `autonomy_auto_bridge`, and the same action + payload at most once per 6 h, so findings cannot loop.
- `operator.notify` only fires for high or critical findings.
- Every option is classified as executed, blocked (with reason), approval required, or informational.

## 7. WS6: self-healing

**Root cause:** `state()` returned BLOCKED whenever any switch was active, so it was blocked permanently in production.

**Now:**
- It is BLOCKED only on an emergency stop.
- At OBSERVE it submits only its policy-automatic actions: diagnostics and the read-only provider re-check.
- Approval-required recoveries are counted (`approval_required`) and wait for RECOMMEND.

Existing guards are unchanged:
- never retries or re-renders jobs, and QC-rejected content is never retried;
- resource locks, duplicate recovery prevention, bounded attempts, circuit breaker;
- independent verification; "recovered" means executed **and** verified.

## 8. WS7: creative quality feedback loop

Evidence (171 evaluated videos):

| | Model script (n=100) | Extractive fallback (n=70) |
|---|---|---|
| Overall | 89.7 | 76.2 |
| Storytelling | 79.3 | 24.7 |
| Hook | 53.1 | 24.0 |
| Ending payoff | 80.1 | 41.1 |
| Pacing | 93.2 | 62.3 |

Recurring weaknesses: hook 96 %, storytelling 88 %, ending 83 %. Failures in 7 days: creative 9 (`CHECK_A_TOPIC_NARRATION`), infrastructure 12 (`RECOVERY_WINDOW_EXPIRED`).

**Applied (measurable, zero-cost):**
- When **every** model fails with transient errors, the grounded script writer runs **one deferred round** after 150 s before falling back (`CRAZZYDOCS_SCRIPT_DEFERRED_RETRY_SEC`, 0 disables).
- That is at most 12 calls per script.
- Validation and authentication failures still fall back at once.

**Measured by the loop:**
- `creative_intel/feedback.py` and `GET /api/creative-intel/feedback` cover quality by script source, trends (n ≥ 20 per window and |Δ| ≥ 3, otherwise "insufficient evidence"), recurring weaknesses, creative vs infrastructure failures, and proposals.
- The `ws7_deferred_script_retry` intervention is recorded once by the hourly creative tick. Its model-share before/after is reported only when there are enough videos.
- Nothing claims improvement yet.

Creative-policy changes (for example a hook experiment) stay operator proposals.

## 9. Operator attention (section 4)

`GET /api/operations/attention` and the Autopilot tab's first card, *Needs your attention*, show three lists:
- **Needs you:** emergency stop, missing credentials, approvals outside the policy, human decisions (creative proposals, topic-supply flags) and recoveries that failed after their permitted attempts. Each appears once.
- **Done automatically:** policy and standing executions with their outcome.
- **Blocked:** refusals with their exact reasons.

Existing page controls are kept. Removing every per-page instruction is left to the Stage 7 redesign.

## 10. Validation, deployment and open items

- **Tests (new):**
  - WS1: 12
  - WS2/5/6: 22
  - WS3: 6
  - WS4: 10
  - WS7: 11
  - attention feed: 5
  - Stage 3.37: 43
- **Full-suite results:** see the handover §2. The baseline is 4 known failures.
- **Benchmarks:** coordinator overhead (Stage 3.37 doc §6); WS1 loop comparison (§2 above).
- **Deployed:** Stage 3.37 + WS1 (`7b4f632`) at 04:34 IST. Verified: deployment `OK` (no stale code), Selector loop, coordinator ticking, 24-cycle schedule revision `ccf0bd04f159` unchanged, autonomy unchanged.
- **First live Shorts cycle on the new daemon:** `cycle_6_20261127_0500` acquired the shared render slot at 05:00:00 (`SLOT_ACQUIRED`, queue SHORTS), released it at 05:11:55, and completed.
- **Full suite on the remediation tree, first run:** 1898 passed, 5 failed (the 4 baseline failures plus the daemon test above, since fixed), 174 skipped. The rerun result is in the handover §2.
- **Open:**
  1. Re-check dashboard CPU and loop lag after many hours of uptime.
  2. Measure the effect of the topic-supply flags, active since 05:45 IST.
  3. Gemini free-tier limits remain external. The deferred retry's effect is measured, not assumed.
  4. The long-form executor and the concurrency benchmark (Stage 3.37) are still open.

## 11. Follow-up: operational acceptance (2026-11-27, 06:00-07:00 IST)

**Live evidence after `9e5abf1`:**

| Item | Evidence |
|---|---|
| Topic flags | Both ON in the config (preserved; previous file backed up). `cycle_7` discovery recorded `production_query_rotation: on` and `exhaustion_recovery: on`. Rotated queries; the rotation table has 7 rows (it was empty). 12 eligible topics (0–1 in the evening before); 7 recently-used rejections (17–27 per run before). This is one cycle, not yet a verdict; see the handover for later cycles. |
| Self-healing (RESTRICTED) | First observation cycle on the new code (00:41 UTC): **11** `selfheal.diagnostic.run`, approved as `policy:RESTRICTED`, executed and SUCCEEDED with verified recovery records. The cycle before the deploy was BLOCKED. |
| Recommendation Center | It ran (12 findings), but the bridge executed **0**. This was **no eligible action, not a broken path**: at level OBSERVE the 3.32C engine records findings as one audit observation and stores no recommendation. **Fixed:** when the policy covers `recommendation.record` (RESTRICTED and NORMAL at OBSERVE or higher), recommendations are stored, so their automatic follow-ups run. No level was raised. |
| Gemini deferred retry | `cycle_7`: the deferred round fired (rounds 1 and 2, 12 calls), and all 12 returned HTTP 429. It cost 150 s with no gain. The 429s run from about 01:00 IST until the free-tier daily reset (13:30 IST), so they are quota exhaustion, which waiting cannot fix. **Fixed:** the round now runs only if round 1 saw an overload or timeout (5xx/408). An all-429 failure falls back at once and records `deferred_retry_skipped`. Improvement is not yet demonstrated (0 of 1). |
| Pixabay | HTTP 400 was reported as `invalid_key`, yet the same key downloaded successfully seconds later. The failing queries were whole headlines (`How much CO₂ will stay? …`), and Pixabay rejects `q` longer than 100 characters. **Fixed:** queries are normalised (NFKD, ASCII, at most 100 characters, cut at a word boundary). 401/403 → `invalid_key`. 400 → `bad_query` once the key has worked, otherwise `invalid_key_or_query`. Degraded providers now show `ok/failed; last error <code> (HTTP n): <next step>`. |
| Pexels | `KEY_MISSING` in the provider audit and listed once under *Needs you* with `PEXELS_API_KEY`. No key was invented and nothing was disabled. |
| Shorts, ownership, publishing, OAuth | 24 cycles, schedule revision `ccf0bd04f159` (active = loaded). Autopilot OFF, scheduler owns production. `automatic_private_scheduled`, no public auto-release. OAuth CONNECTED. `cycle_6` and `cycle_7` both took and released the shared render slot, completed, and were uploaded private and scheduled. |
| Dashboard health since deploy | 90/90 probes HEALTHY, 100 % availability, liveness p50 5.8 ms, p95 50 ms. Loop lag p95 16 ms, loop thread idle. Day before: 79.9 %, p50 567 ms. |
| Dashboard CPU | **Root cause found** (worker-thread sampler after the `69ca792` deploy: 22 of 58 busy samples were in `production_health_dashboard.get_disk_health`). Every one-minute `/api/dashboard/health` poll walked the whole media tree: 61,331 files, 22.1 GB, **7.0 CPU-s per poll**, growing by about 10k files a day. That poll is hidden from the access log by the quiet-polling filter. It explains both the 60 s bursts and the old process climbing to a full core. The same endpoint also ran a full `PRAGMA integrity_check` on every poll. **Fixed:** the media footprint is scanned in the background at most every 30 min (0.6 s, `os.scandir`), and the per-poll call is 0.01 ms. The integrity check is cached for 30 min on a read-only connection. |
| Daemon | 0.2 % of a core, 38 MB. |

**Dashboard (operator experience):**
- A single header badge, *Needs you: N*, colour-coded by severity, opens the *Needs your attention* feed.
- The Recommendation Center intro no longer says "nothing is executed".
- The repeated "Levels, standing approvals and kill switches are managed in Autonomy Control" tails are gone from the Recommendations, Self-Healing and Autopilot status lines.
- The "Approve, reject or execute them in Autonomy Control" prompt is replaced.
- All controls are kept. Genuine confirmations for operator-initiated actions (approve, propose, start/stop) remain, as they are not routine prompts.

**Creative QC vs infrastructure:** `/api/creative-intel/feedback` keeps them apart (7 days: creative `CHECK_A_TOPIC_NARRATION` 9; infrastructure `RECOVERY_WINDOW_EXPIRED` 12). The retry policy still classifies `QCBlocked` as deterministic, so it is never retried as an infrastructure failure.

**Follow-up results (2026-11-27, 07:00–09:00 IST):**

- **Recommendation Center live** (07:41 observation cycle, after `69ca792`): 15 recommendations stored. 4 automatic follow-ups executed and SUCCEEDED (operational report, read-only diagnostic, 2 local notifications for high-severity findings). 1 duplicate was skipped by the 6 h cooldown.
- **Topic flags.** These 3 runs are consistent, but not yet a verdict; the evening runs are the test.

  | | Same hours, 11-26 | 07:00–09:00, 11-27 |
  |---|---|---|
  | Eligible topics | 7.3 | 13.3 |
  | Recently-used rejections | 25.7 | 7.0 |
  | Categories among eligible | 0–1 | 5–8 |
  | Integrity failures | 0 | 0 |

  On 11-26, 19:00–21:00 had 0 eligible topics and 3 integrity failures.
- **`test_daemon_does_not_exit_while_render_in_progress` is intermittent before and after this work:** 1/30 on `a7b0b81` (pre-remediation) and 1/30 now.
  - The 3.36 Autopilot tick's first call takes about 250 ms, which leaves 4–6 dispatches against the test's minimum of 3.
  - My coordinator tick had added contention (Autopilot 2nd/3rd calls at 40–110 ms). It now starts 30 s after daemon start, and those calls are back to 8–11 ms, as before.
  - Recorded as a known intermittent test with this evidence. It is not counted as a baseline failure.
