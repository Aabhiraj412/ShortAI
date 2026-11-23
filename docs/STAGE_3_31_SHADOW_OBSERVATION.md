# Stage 3.31: formal 48-hour shadow-mode observation

Status: **OBSERVATION_STARTED** (not complete; do not report results before the end time).

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
   - **Mitigation:** an hourly read-only snapshot (`--snapshot`) keeps each run's category state. The snapshot is not scheduled yet; it needs operator approval.
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
