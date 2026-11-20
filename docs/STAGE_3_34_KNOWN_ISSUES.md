# Stage 3.34: known issues and remaining risks

| # | Issue | Evidence | Impact | Recommended action |
|---|---|---|---|---|
| 1 | **Recovery-window expiry still dominates historical losses** | 7-day view: 36 expired of 97 cycles. Most are from 2026-11-17/18, before the 3.29–3.32H fixes. Since 2026-11-19 18:30 IST: 25 completed, 3 expired; last 24 h: 23 completed, 1 expired (95.8 %) | Lost cycles when a failure cannot be retried inside 45 minutes | Keep watching the daily report; re-evaluate after 7 days on 3.34 code |
| 2 | **Topic-narration QC rejection** (`CHECK_A_TOPIC_NARRATION`) | `cycle_15_20261120_1400`: the narration lacked enough topic keywords (DETERMINISTIC) | One expired cycle in 24 h | Review the threshold against creative-evaluation data; not changed in this stage |
| 3 | 3 of 10 asset downloads failed in the live cycle | Live 03:00 cycle: "7 acquired, 3 failed"; the production continued | None (enough assets); reduces visual variety | Inspect failure reasons in the next creative reports (the download errors are now retried and cleaned) |
| 4 | Antivirus and indexer locks can exceed the retry budget (about 4.7 s) | Not observed beyond one occurrence | Would still fail that operation (now logged clearly) | Exclude `media\productions` from real-time scanning if it recurs |
| 5 | Stage display depends on the progress store being fresh (15 minutes) | Design | When the store is stale the checkpoint stage is shown (flagged `live_stage_stale`) | None |
| 6 | Pre-existing test failures (4) | `test_dashboard_html_page_served`, `test_end_to_end_orchestration_run`, two `test_youtube_branding` | Test-only | Separate cleanup milestone |
| 7 | `test_stage_3_19_creative_identity_e2e.py` hardcodes the main checkout path | Trips the worktree isolation guard when selected alone | Test-only | Make it path-relative |
| 8 | Community API publishing unavailable | Needs Google pilot access and a `youtube.force-ssl` re-consent | Manual handoff remains | Operator action with Google |
| 9 | No audience analytics yet | `video_performance` has 0 rows | 3.31K historical performance stays neutral; 3.32 cannot learn from viewers | Stage 3.35 prerequisite |

## Stage 3.35 prerequisites (YouTube Analytics and Autonomous Learning)

1. **YouTube Analytics access:** the `yt-analytics.readonly` scope must be granted by the operator (an OAuth consent change: separately authorized). Then `video_performance` collection (3.30) starts filling.
2. **Enough data:** at least 30 videos with 7-day metrics before any learning claim. Category priors (3.31K) need at least 3 videos per category.
3. **Joins already in place:** `creative_records` (job_id → youtube_video_id, creative decisions and craft scores), `topic_candidate_ledger` (v1/v2 scores, demand evidence), `topic_demand_signals`, and render manifests.
4. **Decisions waiting:** after the 48 h shadow review, whether to enable `expanded_categories`, `demand_signals` and `scoring_v2`; and whether to run a `hook_type` experiment.
5. **Autopilot:** in OBSERVE. RECOMMEND only after its readiness passes and the operator authorizes it.
