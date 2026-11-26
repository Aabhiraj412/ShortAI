# Stage 3.31: formal 48-hour shadow-mode observation

Status: **OBSERVATION_COMPLETE**: valid 48-hour window, 48 of 48 cycles observed. Final report in §9 (written 2026-11-27).

## 1. Decision: no valid 48-hour window existed

Inspected on 2026-11-23 at 23:45 IST (read-only):
- `topic_discovery_runs`: 79 runs;
- `topic_candidate_ledger`: 341 rows;
- `topic_candidate_events`: 8,931 rows;
- jobs, lifecycle events, scheduler log and config.

| Period (IST) | Flags recorded per run | Cycles | Why it is not a valid 48 h window |
|---|---|---|---|
| 2026-11-20 05:00 → 2026-11-21 02:00 | ledger `on`, classifier v2 `shadow` | 22 consecutive | Ended by a configuration change. The config file was modified 2026-11-21 02:35 IST, adding `demand_signals: shadow` and `scoring_v2: shadow`. Too short. |
| 2026-11-21 03:00 → 2026-11-22 14:00 | + demand_signals, scoring_v2 `shadow` | 36 consecutive | Ended by a **host outage**: no log lines, jobs or lifecycle events between 2026-11-22 14:15 and 2026-11-23 02:43 IST. |
| 2026-11-22 15:00 → 2026-11-23 02:00 | none | **0 of 12** | Outage. Cycles 16–24 (Nov 22) have no job at all. Cycles 1–3 (Nov 23) were reconciled as `RECOVERY_WINDOW_EXPIRED` when the daemon started at 02:43:45. |
| 2026-11-23 03:00 → 2026-11-23 23:00 | same four flags | 21 of 21 | Continuous, but only 21 h. Used as the **pre-observation baseline**, not counted. |

The first ledger recording (`cycle_6_20261120_0500`) does not start a valid window: the configuration changed 22 cycles later, and the outage broke continuity.

## 2. Formal observation window

| Item | Value |
|---|---|
| **Start** | **2026-11-24 00:00:00 IST**: slot of `cycle_1_20261124_0000`, the first cycle after verification |
| **End (exclusive)** | **2026-11-26 00:00:00 IST** |
| Eligible hourly cycles | 48: `cycle_1_20261124_0000` … `cycle_24_20261125_2300` |
| Earliest valid completion | after the 23:00 IST cycle on 2026-11-25 finishes. The report is run at or after 2026-11-26 00:00 IST. |

## 3. Configuration and process identity at start

**Config file** `backend/data/creative_policy_config.json`:
- sha256 `c30e79bf10508f2afc34049e1490bf90745f9c90451abd0162035f17b4458713`;
- modified 2026-11-21 02:35 IST;
- not committed (local configuration, preserved as-is).

**`topic_intelligence` block:**

| Flag | Value | Effect |
|---|---|---|
| `candidate_ledger` | `on` | expected |
| `category_classifier_v2` | `shadow` | expected; recorded only |
| `demand_signals` | `shadow` | **not in the expected list**. Shadow only: candidates are recorded in run notes, and the list is emptied before pool assembly (`curiosity_topic_engine.py`, step 6). |
| `scoring_v2` | `shadow` | **not in the expected list**. Shadow only: the v2 score is recorded in run notes; selection uses v1 (`is_on` gate). |
| `production_query_rotation`, `gnews_candidates`, `wikipedia_in_the_news`, `exhaustion_recovery`, `expanded_categories` | `off` (absent) | as required |

The two extra shadow flags were left as they are. Changing them would itself be a configuration change mid-baseline, and neither influences selection.

**GNews note:** a `gnews` provider family appears in some runs. This is the **pre-3.31 legacy GNews supplement** (`_gnews_candidates`, used whenever `gnews_candidates` is off), part of the approved production path. It is not the 3.31 GNews feature, which stays off.

**Process identity:**
- **Scheduler daemon:** PID 7680, started 2026-11-23 02:43:45 IST, loaded code revision `c493bb11f6355bcd` (current, not stale), heartbeat OK.
- **Dashboard:** PID 7704. **Worker:** idle at start.
- **Schedule:** active and loaded revision `ccf0bd04f159` (24 cycles, CUSTOM profile).
- **Git:** HEAD `417076c` when the window started (this record and the read-only script were committed after it; neither touches `backend/app`, so the code revision is unchanged).
- **Database:** `backend/studio.db`, 22,110,208 bytes, `quick_check` ok.
- **Counts at start:** ledger 341, events 8,931, runs 79.

## 4. Persistence confirmed (pre-observation baseline, 21 cycles, 2026-11-23 03:00–23:00)

- **Runs:** every run has a `topic_discovery_runs` row with `flags_json`, provider stats and outcome. All 21 produced.
- **Candidate events:** 2,553 written (≈ 54–56 candidates per cycle).
- **Shadow evidence:** every run carries `scoring_v2` and `demand_shadow` notes.
- **Category v2 shadow predictions:** recorded on enriched candidates (58 predictions: 37 CLASSIFIED, 20 UNKNOWN, 1 OUT_OF_SCOPE). 36 disagreed with v1 (most are v2 `UNKNOWN` where v1 assigned a category).
- **Production decisions:** these are the v1 category and the v1 score. The v2 fields are never used for selection while the flag is `shadow`.

The first in-window cycle is checked in §6.

## 5. Known telemetry limitations (found during inspection; not fixed)

1. **The ledger row is overwritten on re-sighting.** The ledger keeps one row per candidate. When a candidate is seen again in a later run and rejected before enrichment, the update writes blank `category_code` and `category_v2_json` (`candidate_ledger.py`, `_upsert_candidate`). Its earlier v1/v2 categories are lost. In the baseline, **6 of 21 selected topics** had lost them by 23:45.
   - **Not fixed:** fixing it changes production code, needs a daemon restart and would invalidate this observation.
   - **Mitigation:** an hourly read-only snapshot (`--snapshot`) keeps each run's category state. Approved by the operator and scheduled as the Windows task `CrazzyDocs-Stage331-ShadowSnapshot`:
   - runs hourly at :40 (between cycles), 2026-11-24 00:40 → 2026-11-26 00:40 IST;
   - ends 2026-11-26 01:30 and is deleted automatically a day later;
   - reads the database read-only; writes only `logs/stage331_observation/runs.jsonl`;
   - first run 2026-11-24 00:05 IST (result 0). It captured all 80 runs to date, including `cycle_1_20261124_0000`, though runs before the window were captured after some overwrites.
2. **v2 runs only on enriched candidates.** Candidates rejected at `rss_filter`, `pre_enrichment`, `discovery_filter` or pool assembly never receive a v2 prediction. This is by design, not a gap, but it means v2 coverage is roughly the eligible and enriched set, not all candidates.
3. **Wikimedia rate limits continue** (baseline: 178 HTTP 429, 53 budget exhaustions in 21 cycles). They are recorded in `notes_json.wikimedia_requests`.
4. **One run without an outcome** (`cycle_10_20261122_0900`, before the window, interrupted by a recovery); the analysis lists these as `incomplete_runs`.

## 6. First in-window cycle

`cycle_1_20261124_0000` discovery: 00:00:01 → 00:04:11 IST.
- **Flags recorded:** exactly the four in §3.
- **Candidates:** 55 (13 eligible).
- **Events:** 123 written.
- **Shadow v2 predictions:** 16.
- **Notes:** `scoring_v2`, `demand_shadow` and `wikimedia_requests` present.
- **Selected:** "Ancient Carthage", v1 History. v2 shadow returned `UNKNOWN` and was not used.

The production job was running normally at 00:04 IST (`STORY_DEVELOPMENT`).

Ledger writes and shadow predictions are confirmed persisting inside the window.

## 7. How to produce the final report (after 2026-11-26 00:00 IST)

```
python backend/scripts/stage331_shadow_observation.py --start 2026-11-24T00:00 --end 2026-11-26T00:00 \
    --db backend/studio.db --snapshots logs/stage331_observation --json logs/stage331_observation/final.json
```

The script is read-only, needs no restart and reports:
- eligible, observed and missing cycles (with job status for each gap);
- config revisions and violations per run;
- candidates per cycle;
- rejection reasons by stage;
- duplicate-gate behaviour;
- v1 versus v2 disagreements (from the ledger and, when snapshots exist, per run);
- v2 distribution;
- provider contributions and errors;
- Wikimedia 429s;
- run and job outcomes;
- growth and integrity.

**Invalidating events** (the window must restart if any occur):
- a flag change in `flags_json`;
- any `MUST_BE_OFF` flag turned on;
- a missing cycle caused by a daemon or host outage.

A failed production cycle (QC block, integrity gate) is a production outcome, not an observation gap.

## 8. Boundaries during observation

No feature activation, no config change, no restart, no extra cycles, workers or renderers. Shadow predictions are never used for decisions.

## 9. Final report (window 2026-11-24 00:00 → 2026-11-26 00:00 IST)

All figures come from `backend/scripts/stage331_shadow_observation.py --snapshots logs/stage331_observation`, run read-only on 2026-11-27. The raw result is in `logs/stage331_observation/final.json`. Production decisions and shadow predictions are reported separately throughout.

### 9.1 Window validity
| Check | Result |
|---|---|
| Eligible hourly cycles | 48 (`cycle_1_20261124_0000` … `cycle_24_20261125_2300`) |
| Cycles observed (discovery run recorded) | **48 / 48**; no missing cycles, no gaps |
| Incomplete runs (no outcome) | 0 |
| Configuration | identical in all 48 runs: ledger `on`, classifier v2 `shadow`, demand_signals `shadow`, scoring_v2 `shadow`. Every feature that must be off was off. Config file hash unchanged (`c30e79bf…`). |
| Process identity | scheduler daemon PID 7680 for the whole window (started 2026-11-23 02:43, restarted only on 2026-11-26 at 23:24 for a later deployment). A second launch at 2026-11-24 22:23 was refused by the instance lock (see 9.8). |
| Per-run category snapshots | 48 / 48 captured by the hourly read-only task (last run 2026-11-26 00:40, result 0; the task has expired) |

### 9.2 Candidate ledger completeness
- 48 discovery runs and 5,937 candidate events.
- 42–55 candidates per cycle, average 52.9 distinct; the 42 and 43 occurred when Wikipedia feeds timed out.
- 228 new candidates first seen in the window. 284 distinct candidates were seen in total.
- Every run carries provider statistics, `scoring_v2` notes, `demand_shadow` notes and Wikimedia request statistics.
- **Limitation (confirmed):** the ledger row keeps only the latest state of each candidate. Its v1/v2 categories are blanked when the candidate is seen again before enrichment: 199 of the 284 window candidates have no stored v2 prediction now. The per-run snapshots hold the complete record (569 predictions), and 9.3 uses them.

### 9.3 Category classifier v1 vs v2 (shadow only, never used for selection)
| | All enriched candidates (per-run snapshots) | Selected topics (48) |
|---|---|---|
| v2 predictions | 569 | 48 |
| v2 CLASSIFIED / UNKNOWN / OUT_OF_SCOPE / AMBIGUOUS | 323 / 187 / 56 / 3 | 25 / 19 / 3 / 1 |
| Agree with v1 | 165 (29%) | 15 (31%) |
| Disagree with v1 | 404 (71%) | 33 (69%) |

**Two very different kinds of disagreement:**
1. **v2 declines to classify (about 60% of disagreements).** It returns UNKNOWN (confidence ≤ 0.42) or OUT_OF_SCOPE where v1 assigned a category. The pairs are History→none (113), Science→none (56), Everyday Science→none (54). These are mostly topics v1 forced into a broad bucket, such as "Toy Story", "Magnus Carlsen", "Mairzy Doates" and "Georgia Waffle House cooling" → v1 *History*. With v2 `on`, these would have been rejected or fallen back to their discovery hint. **22 of the 48 produced topics** had a non-classifying v2 result (19 UNKNOWN, 3 OUT_OF_SCOPE), and one more was AMBIGUOUS.
2. **v2 assigns a different category (about 40%).** Many look like corrections of v1:
   - Greek government-debt crisis: Engineering → Business and Economics (0.97);
   - Western Electric: Engineering → Business (0.98);
   - Monopoly: History → Business (1.0);
   - La Ciudad Blanca: History → Archaeology (0.98);
   - CO₂ removal: Everyday Science → Environment and Climate (1.0).

   Some are debatable:
   - Verrazzano–Narrows Bridge: Engineering → Geography;
   - Riemann hypothesis: Mathematics → Science (0.57);
   - Thomas Edison: Engineering → Business (0.66);
   - Phaistos Disc: Archaeology → Geography (0.56).

   The most frequent v2 re-labelling is Engineering → Business and Economics (37).

**v2 confidence:** a large share of predictions sit at the UNKNOWN floor (0.333). 35 of the 85 ledger-retained predictions fall below 0.5.

### 9.4 Rejection analysis (actual production decisions)
2,206 rejection events in the window:

| Reason | Count | Main stage |
|---|---|---|
| RECENTLY_USED (duplicate / cooldown) | 1,232 | pre-enrichment 991, identity gate 181, duplicate gate 60 |
| INSUFFICIENT_EVIDENCE | 769 | RSS filter 525, enrichment 244 |
| LOW_RELEVANCE | 179 | discovery filter |
| POOL_CAPACITY_LIMIT | 26 | enrichment cap |

- **Duplicate-topic integrity:** 241 rejections at the identity and duplicate gates, plus 991 earlier at pre-enrichment. No cycle ran out of eligible topics: 2–13 eligible per run, average 7.0, never 0. This is unlike the 2026-11-21 exhaustion, and consistent with the 3.35b Wikimedia fix.
- Duplicate rules were not changed.

### 9.5 Provider contribution
| Provider family | Candidates | Accepted | Duplicates | Errors | Present in runs |
|---|---|---|---|---|---|
| wikipedia_search | 1,149 | 150 | 852 | 0 | 48 |
| physorg_rss | 720 | 135 | 60 | 0 | 48 |
| wikipedia_mostread | 337 | 14 | 198 | 0 | 46 |
| wikipedia_onthisday | 138 | 14 | 91 | 0 | 46 |
| wikipedia_tfa | 46 | 15 | 31 | 0 | 46 |
| nasa_library | 144 | 0 | 0 | 0 | 48 |
| gnews (legacy supplement, not the 3.31 feature) | 7 | 7 | 0 | 0 | 7 |
| wikipedia_featured | 0 (feed is used for enrichment) | – | – | 2 | 48 |

- **Wikimedia requests:** 3,281 ok, 154 not found (404), 261 rate-limited (429) with 235 retried, 26 budget exhaustions.
- **Provider failures:** 2 wikipedia_featured fetch errors. In 2 runs the mostread / onthisday / tfa feeds were missing, which explains the 42/43-candidate cycles.
- **NASA library** produced 144 candidates and **0 accepted** across 48 runs, worth a look.

### 9.6 Production during the window (actual outcomes)
- **44 produced, 4 failed.**
- All 4 failures were QC blocks: `CHECK_A_TOPIC_NARRATION`, where the narration lacked topic keywords, followed by the recovery window closing. They were cycle 10 on Nov 24, and cycles 18, 20 and 23 on Nov 25.
- No failure was caused by the ledger or the shadow features.
- No missed or skipped cycles.

### 9.7 Database growth and integrity
- `quick_check`: ok.
- **Window growth:**
  - 5,937 events (about 0.95 MB of event payload);
  - 48 runs (about 0.38 MB of run JSON, mostly shadow notes);
  - 228 new ledger rows.
- **Rate:** about 0.65 MB per day for the ledger tables, about 240 MB per year at this rate, so no retention issue in the short term. `studio.db` grew from 22.1 MB to 37.9 MB over the same period, but most of that is other tables (lifecycle events, notifications, Community, analytics).

### 9.8 Production impact
- **Shadow features had no measurable effect on selection:**
  - Topics that v2 marked UNKNOWN or OUT_OF_SCOPE were still produced (22 cases).
  - No demand candidate entered the pool (0 demand-stage events).
  - The v2 score's top candidate differed from v1's in 35 of 48 runs, while selections followed the v1 path.
- **Discovery time** includes Wikimedia pacing: up to about 140 s of waits per run, from the 3.35b fix, not from 3.31.
- **One unrelated incident:** on 2026-11-24 at 22:17–22:26 the cycle-report email code held the scheduler loop for about 8 minutes. That is a storage audit inside the email path, since fixed in commit 507d26c. The startup monitor saw the stale heartbeat and tried a second daemon launch, which the lock refused. No cycle was affected.

### 9.9 Is the evidence sufficient for a later controlled experiment?
- **Candidate ledger:** yes. It persisted completely for 48/48 cycles under a constant configuration and process, with no production impact. It is ready to stay `on`. The re-sighting overwrite should be fixed before ledger categories are used for analysis: per-run category history, or not blanking categories on update.
- **Category classifier v2:** sufficient to *design* an experiment, not to activate it.
  - It disagrees with v1 on about 70% of candidates. About 60% of those are UNKNOWN / OUT_OF_SCOPE, which would reject or re-route about 23 of 48 (48%) of what production actually made.
  - Activating it as-is would sharply reduce the eligible pool. Recall the 2026-11-21 topic exhaustion.
  - Its confident re-labellings (≥ 0.95) look like genuine improvements.
  - A reasonable next experiment: use v2 only when it is CLASSIFIED with confidence ≥ 0.9, otherwise keep v1, and measure pool size and category balance in shadow first.
- **demand_signals / scoring_v2** (also in shadow throughout; not part of the requested scope): recorded every run. v2 scoring would have changed the top-ranked candidate in 35 of 48 runs, so any trial needs its own shadow analysis of the resulting category and duplicate mix.

**No experimental feature was activated** on the basis of this observation, and none should be without separate authorization.

### 9.10 Limitations and recommendations
1. Fix the ledger re-sighting overwrite (code change; needs a deployment). Until then, keep `--snapshot` when category analysis matters.
2. Store the v2 prediction per run in the events (or a small history table), so the snapshot task is not needed.
3. Investigate NASA library's 0% acceptance and the recurring `CHECK_A_TOPIC_NARRATION` QC blocks (4 of 48 cycles).
4. Wikimedia 429s remain about 7% of requests; pacing absorbs them, but they add up to about 2 minutes to discovery.
5. The observation used the configuration that was active (four flags). The two extra shadow flags did not influence selection, but a strictly two-flag baseline was not observed.
