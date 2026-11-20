# Stage 3.32C: AI Recommendation Engine (implementation)

**Status:** implemented and tested in the development worktree on top of `c4e1cf5`. **Not committed, not deployed.** It is recommendation-only.

## 1. Architecture

```
3.32A Operational Intelligence (read-only, cached 60 s)
   │  sections: production_health · failure_intelligence · renderer_reliability · topic_intelligence ·
   │            api_intelligence · recovery_analytics   +  bundle rows (jobs, runs, productions, registry) + failures
   ▼
signals.collect()            1. Signal collection: one bounded 7-day read; a broken section is isolated
   ▼
rules.evaluate()             2. Pattern detection  3. Root-cause hypothesis  4. Recommendation text
   │                            25 rule functions, 28 finding types, 6 categories; "insufficient evidence" instead of guesses
   ▼
scoring.confidence/priority  5. Confidence + priority, with every component returned
   ▼
store.upsert/expire/supersede 6. Deduplication  7. Persistence (autonomy_recommendations, _runs)
   ▼
engine.RecommendationEngine  8. Control-plane integration (3.32B): level + kill switches gate every run;
   │                            every decision -> append-only audit (action type recommendation.record)
   ▼
Recommendation Center (dashboard)  ── operator: acknowledge / agree / reject (reason) / resolve (evidence)
                                       (never executable; executable requests stay in Autonomy Control)
```

**Rules are deterministic.** The baseline is deterministic and explainable. Gemini is used only to reword findings in plain language: it is optional, **off by default**, and not needed for anything (see §6).

## 2. Files

| File | Change |
|---|---|
| `backend/app/autonomy/recommendations/__init__.py` | **New**: package doc |
| `backend/app/autonomy/recommendations/signals.py` | **New**: `Signals`, `collect()` (reuses 3.32A; no new analytics queries) |
| `backend/app/autonomy/recommendations/rules.py` | **New**: `Finding`, 25 rule functions (28 finding types + 1 data-availability check), `evaluate()` |
| `backend/app/autonomy/recommendations/scoring.py` | **New**: confidence and priority models |
| `backend/app/autonomy/recommendations/store.py` | **New**: tables, dedupe/upsert, expiry, supersession, status transitions, runs, trend |
| `backend/app/autonomy/recommendations/engine.py` | **New**: gating, run orchestration, audit, `GeminiExplainer`, operator actions, `get_engine()` |
| `backend/app/autonomy/control_plane.py` | **Modified, 1 line**: `recommendation` added to the reserved actor prefixes, so the engine's identity can never approve, change permissions or toggle kill switches |
| `backend/app/main.py` | **Modified**: 8 `/api/recommendations*` endpoints |
| `backend/app/templates/dashboard.html` | **Modified**: "Recommendations" tab (Recommendation Center) |
| `backend/tests/test_recommendation_engine_332c.py` | **New**: 33 test functions (35 cases) |
| `docs/STAGE_3_32C_*.md` | **New**: this document, plus the rules, confidence model and test results |

## 3. Database

The tables are created on first use, the same pattern as the 3.32B tables. **They do not exist in production yet**, because the engine has never run there.

| Table | Purpose | Constraints |
|---|---|---|
| `autonomy_recommendations` | One row per recommendation. Every required field: id, category, title, description, detected problem, evidence, observations, root-cause hypothesis and its strength, suggested action, expected benefit, risk, confidence (score and label), priority (score and label), scoring components, created, expiry, related jobs, status, plus who changed the status, when, why and with what evidence. | **Partial UNIQUE** `dedupe_key WHERE status IN (NEW, ACKNOWLEDGED, APPROVED)`: one open recommendation per issue. Indexes on status/priority, key/status time and status/expiry. |
| `autonomy_recommendation_runs` | One row per completed or OBSERVE run: findings, created, updated, expired, superseded, suppressed, insufficient-evidence list, source availability | index `started_at` |

**Status history** goes to the 3.32B append-only, hash-chained audit trail, keyed by recommendation id:
- engine events: `RECOMMENDATION_CREATED`, `_UPDATED` (material change only), `_EXPIRED`, `_SUPERSEDED`, `RECOMMENDATION_RUN_COMPLETED`, `_RUN_BLOCKED`;
- operator events: `_ACKNOWLEDGED`, `_APPROVED`, `_REJECTED`, `_RESOLVED`.

## 4. Recommendation lifecycle

| Status | Set by | Meaning |
|---|---|---|
| `NEW` | engine | First detection |
| `ACKNOWLEDGED` | operator | Seen |
| `APPROVED` | operator | "I agree with the suggested action". **Records agreement only; nothing runs.** |
| `REJECTED` | operator (reason required) | Not re-raised for 7 days; after that a new recommendation links to it |
| `RESOLVED` | operator (evidence required) | If detected again, a new recommendation notes the regression |
| `EXPIRED` | engine | Not re-detected within the rule's expiry period (24–72 h) |
| `SUPERSEDED` | engine | Replaced by a more specific finding about the same subject (`superseded_by`) |

**Deduplication.** Repeated detection of the same issue (`rule_id:subject`) **updates** the open row: evidence, scores, occurrences, `detections + 1`, `last_detected_at`, expiry. It never adds a second card. A test reruns the engine and checks that the IDs are unchanged and the detections are 2. The database index enforces this too.

## 5. Control-plane integration (3.32B)

| Requirement | Implementation |
|---|---|
| Global and category levels | Effective level of the `recommendations` category = min(global, category). **OFF:** nothing runs and nothing is written (not even tables). **OBSERVE:** analysis runs; one `OBSERVATION_RECORDED` audit event; no recommendation stored. **RECOMMEND or higher:** recommendations are stored. |
| All three kill switches | Any active switch (dashboard, sentinel, startup config) **pauses the engine**: no analysis; a `RECOMMENDATION_RUN_BLOCKED` audit event. Existing recommendations stay readable and operators can still triage them. |
| Permitted action type | Runs only if `recommendation.record` is a permitted registry entry in the `recommendations` category; audit events carry that action type |
| Audit | Every create, material update, expiry, supersession, run, blocked run and operator decision |
| Never executes | The engine calls no control-plane `propose`, `approve`, `execute`, `submit`, `set_level`, kill-switch or standing-approval function (a source scan test checks this); no approval requests or executions are created (tested) |
| No self-permission or self-approval | Status changes go through `validate_operator`, which refuses reserved identities. `recommendation…` was added to the reserved prefixes in this stage (a test found the gap). |
| No silent configuration change | The engine only reads levels. **Production stays OFF** until an operator raises `global` and `recommendations` to RECOMMEND in Autonomy Control. |

**Important consequence:** in production, the startup kill switch (C) is active while `backend/data/autonomy_startup.json` is absent. The engine therefore stays paused even after its levels are raised, until that file exists. This follows the instruction to respect all three switches. If you would rather run recommendations (which never execute anything) while execution stays disabled, that is a one-line policy decision: let the startup switch gate execution only. It has not been changed.

## 6. Gemini (optional wording)

- **Enablement.** Enabled only when `CRAZZYDOCS_RECOMMENDATION_EXPLAINER=gemini` is set for the dashboard process. The default is off: no client is created and no call is made.
- **Limits.** At most 3 explanations per run, only for new or materially changed recommendations, stored with an evidence fingerprint, so nothing is re-explained.
- **Wording only.** The prompt asks to keep the facts and the uncertainty and to add nothing; the output is stored in `explanation` and labelled "(Gemini)".
- **Failure handling.** Failures are audited (`RECOMMENDATION_EXPLAINER_FAILED`) and ignored; recommendations are unaffected (tested with a failing generator).

## 7. API

| Endpoint | Auth |
|---|---|
| `GET /api/recommendations?status=open\|history\|all\|<STATUS>&category=&limit=` | localhost |
| `GET /api/recommendations/trend?days=1..60` | localhost |
| `GET /api/recommendations/{id}` (detail, including audit history) | localhost |
| `POST /api/recommendations/run {operator, force}` | localhost + same-origin + JSON + CSRF token |
| `POST /api/recommendations/{id}/acknowledge` / `approve {operator, note?}` | same |
| `POST /api/recommendations/{id}/reject {operator, reason}` | same (reason required) |
| `POST /api/recommendations/{id}/resolve {operator, evidence}` | same (evidence required) |

There is no execution endpoint.

## 8. Dashboard: "Recommendations" tab

- **Header:**
  - banner: "Advice only … nothing is executed. Executable approval requests live in Autonomy Control";
  - operator field;
  - Analyse now;
  - Refresh.
- **KPIs:** active, critical, high priority, new in 24 h, engine state (ACTIVE / OBSERVE / OFF / BLOCKED, with the reason).
- **Breakdown and trend:** by category and confidence, plus the 14-day trend.
- **List:**
  - Filters: open / history / all, and category.
  - Each card shows priority (colour-coded), category, confidence label and score, evidence type ("Confirmed observation" / "Strongly supported hypothesis" / "Weak hypothesis"), status, title and observation.
  - **Details** shows: the detected problem, the hypothesis with its strength, the suggested action and its risk, the expected benefit, the supporting evidence with sources, the historical observations, the related jobs, the scoring components, and timestamps and status history.
  - Buttons: Acknowledge, Agree (confirmation says nothing is executed), Reject… (reason), Mark resolved… (evidence).
- **Insufficient evidence:** a collapsible list of the checks that had too little data in the last analysis.
- **Loading:** the tab loads only when opened, then refreshes every 120 s while open. Analysis runs only on "Analyse now"; there is no background loop.
- **Rendering safety:** all values are escaped; IDs go through `data-` attributes.

## 9. Performance

- **One cached read per run.** A run is one Stage 3.32A bundle read (bounded 7-day window, already cached for 60 s), then pure in-memory rules. On a copy of the live database (83 cycles), a forced run took 0.27 s cold and 0.14 s warm.
- **Small writes:** ≤ ~30 upserts per run in one transaction, plus a few audit rows.
- **Throttle:** a non-forced run within 5 minutes of the last is skipped. A run lock prevents concurrent runs.
- **No polling or extra load:** no background polling, no new dependency, no local model.

## 10. Results on real data

**Setup:** a dry run on a *scratch copy* of today's backup, not the live database, with levels raised only in that copy. It produced **22 recommendations**: 1 critical, 5 high, 15 medium, 1 low.

**All four example findings were derived from the data:**
- **FFmpeg padding:** "FFmpeg padding dimension failure repeated 3×", **confirmed deterministic**. The retry policy calls it TRANSIENT; the recommendation says so without changing the policy. Suggested action: "Inspect the Priority B adaptation dimension calculation".
- **Wikipedia:** "All 24 wikipedia_search candidates were rejected in the latest run", together with "Eligible topics come mostly from physorg_rss" (19 of 30 accepted, 63%).
- **Recovery windows:** "35 of 83 finished production cycles (42.2%) ended because their recovery window expired". Priority CRITICAL, but **LOW confidence / weak hypothesis**, because the stage at expiry is mostly not recorded.
- **OAuth scope:** "2 YouTube videos.update request(s) failed because of insufficient_scope" (confirmed observation). Suggested action: review whether the workflow needs it; scopes are not changed.

**Other findings:** QC duration failures (14×), topic exhaustion (8), the THUMBNAIL stage concentration, 9 cycles repeatedly consuming their windows (one aggregated card), ineffective retries, the extractive-fallback script share, the classifier v2 disagreement (92%), unproductive providers and media acquisition failures.

## 11. Known limitations

- **The production engine is paused** until the levels are raised **and** the startup kill switch is cleared (§5).
- **Hypotheses only.** Root causes are hypotheses derived from aggregates. Where the data cannot attribute a cause (for example, expirations whose stage was not recorded), the recommendation says so and its confidence is LOW.
- **No engagement analytics.** Hook quality, retention and engagement are not analysed: YouTube Analytics data is not part of the 3.32A data (reported as insufficient evidence, never estimated).
- **Discovery history.** Topic rules cover only runs since the candidate ledger was enabled (2026-11-20 05:00 IST).
- **Recovery-time distortion.** "Recoveries take a very long time" is dominated by the 17–18 Nov quota re-arms. The recommendation states this (STRONG hypothesis) rather than presenting it as a production problem.
- **Thresholds are first-pass values** (rule document). They should be reviewed after a few days of real use; rejected recommendations are suppressed for 7 days, which also measures precision.
