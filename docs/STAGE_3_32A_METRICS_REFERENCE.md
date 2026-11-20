# Stage 3.32A: Metrics Reference

**Common definitions:**
- **Window:** the last N days (1–30), or one IST calendar day for the daily summary.
- **Production cycle:** a `persistent_jobs` row whose id starts with `cycle_`, bucketed by `created_at`.
- **Finished:** status `COMPLETED`, `FAILED`, `RECOVERY_WINDOW_EXPIRED` or `ABORTED_PRE_RESET`.
- **Status:**
  - `A` = actual: computed from recorded data;
  - `E` = estimate;
  - `U` = may be unavailable, returned as `null` with a reason, never as zero.

## A. Production health (`production_health`)

| Metric | Source | Calculation | St. | Limitations |
|---|---|---|---|---|
| `total_cycles` | persistent_jobs | rows in window | A | — |
| `completed`, `failed`, `recovery_window_expired`, `aborted` | persistent_jobs.status | counts | A | — |
| `running` | status ∈ RUNNING, RETRYING, PAUSED | count | A | Snapshot at query time |
| `queued` | status ∈ QUEUED, RETRY_SCHEDULED, HANDOFF_PENDING, PENDING | count | A | Snapshot |
| `completion_pct` | — | completed ÷ finished × 100 | A/U | `null` when nothing finished |
| `avg_production_minutes` | actual_start_at → completed_at | mean over completions ≤ 240 min | A/U | Longer completions (re-armed later) are excluded and counted in `long_running_completions` |
| `median_production_minutes` | same | median over all completions | A/U | — |
| `avg_stage_seconds` | progress store `stages.*` | mean of completed − started per stage | A/U | Latest attempt only |
| `stage_failure_distribution` | current_stage of FAILED / EXPIRED | counts; a stage equal to a status becomes `NOT_RECORDED` | A | — |

## B. Failure intelligence (`failure_intelligence`)

**Failure occurrence:**
- every `CYCLE_DISPATCH_FAILED` event that has error text;
- plus each failed, expired or aborted job's final `error_details`, when it adds a new (job, pattern) pair;
- plus expired jobs with no error, as `recovery_window_expired`.

| Metric | Calculation | St. |
|---|---|---|
| `total_failures` | occurrences | A |
| `by_root_cause` | ordered regex catalog, first match wins: `file_lock`, `topic_exhaustion`, `renderer_compliance`, `render_timeout`, `ffmpeg`, `renderer`, `quota`, `auth`, `media_acquisition`, `duplicate_content`, `qc`, `tts`, `script_generation`, `network`, `stop_requested`, `recovery_window_expired`, `other`, `unrecorded` | A |
| `by_class` | the `[CLASS]` prefix stamped by the retry policy, otherwise `retry_policy.classify_error`. Values: TRANSIENT, PERMANENT, DETERMINISTIC, POST_RENDER_FAILURE, EXPIRED (deadline outcome), UNCLASSIFIED. | A |
| `transient`, `permanent_or_deterministic` | from `by_class` | A |
| `by_stage`, `most_failure_prone_stage` | job stage at failure (`NOT_RECORDED` excluded from "most") | A |
| `patterns` | grouped by (root cause, normalized template). Normalization removes the class prefix, paths, hex ids and addresses, quoted strings and numbers, and keeps the first line. The grouping key also drops a trailing parenthetical and is cut at 100 characters. Each group carries count, classes, most-severe class, example jobs (≤ 6), first and last seen. | A |
| `repeated_patterns` | patterns with count ≥ 2 | A |
| `daily_trend` | occurrences per IST day per cause | A |

**Limitation:** the class is the retry policy's judgement. For example, the FFmpeg padding error is stamped TRANSIENT.

## C. Renderer reliability (`renderer_reliability`)

| Metric | Source | Calculation | St. |
|---|---|---|---|
| `renders_completed` / `renders_failed` / `render_success_pct` | progress store `stages.video_rendering.status` for production cycles started in window | counts; ok ÷ (ok + failed) | A/U |
| `avg_render_seconds` | `video_rendering` started → completed | mean | A/U |
| `produced_by_renderer`, `lemo_opuscar_produced` | creative_identity_registry.renderer_config_json for PRODUCED rows | counts | A |
| `ffmpeg_errors`, `renderer_errors`, `renderer_compliance_blocks`, `render_timeouts`, `file_lock_errors`, `media_acquisition_errors` | failure catalog (B) | occurrence counts per cause | A |
| `media_acquisitions_failed`, `media_unique_images` | progress store `assets.failed_acquisitions` / `unique_images` | sums | A/U |
| `daily_trend` | rendering ok / failed per IST day | — | A |

## D. Topic intelligence (`topic_intelligence`)

The whole section is unavailable when the Stage 3.31 ledger tables are absent.

| Metric | Source | Calculation | St. |
|---|---|---|---|
| `discovery_runs`, `candidates_recorded`, `avg_candidates_per_run`, `avg_eligible_per_run` | topic_discovery_runs | counts and means | A |
| `acceptance_pct` | topic_candidate_events | ELIGIBLE ÷ (ELIGIBLE + REJECTED) decisions | A |
| `rejection_reasons` | topic_candidate_events (REJECTED) | counts by structured reason | A |
| `provider_contribution`, `provider_daily_trend` | runs.provider_stats_json | sums per provider family (queries, articles, candidates, accepted, rejected, duplicates, errors, unique) | A |
| `selected_category_distribution` | ledger rows SELECTED / PRODUCED | counts by v1 category | A |
| `classifier_v1_vs_v2` | ledger `category_v2_json` vs `category` | agree / disagree / unknown / out_of_scope | A/U |
| `classifier_disagreement_pct` | — | (disagree + unknown + out_of_scope) ÷ total | A/U |
| `classifier_disagreements` | — | up to 20 examples | A |
| `topic_exhaustion_failures` | failure catalog | count | A |
| `low_pool_runs` | runs with eligible ≤ 1 | count | A |
| `exhaustion_recoveries` | runs.recovery_json.outcome | counts (empty while 3.31D is off) | A |

## E. API intelligence (`api_intelligence`)

| Metric | Source | Calculation | St. |
|---|---|---|---|
| `youtube_requests`, `…_by_method`, `youtube_units_by_bucket`, `youtube_daily_units` | youtube_api_requests (sent requests only) | counts / sums of documented costs | A |
| `youtube_errors` | error_category | `method:category` counts | A |
| `youtube_quota_today_pacific` | same table, current Pacific day; `youtube_quota.BUCKETS` | used = Σ cost; remaining = documented default limit − used; % used; `google_block_active` from youtube_quota_blocks | **E** |
| `google_reported_quota` | — | — | **U** (needs Cloud Monitoring access) |
| `scripts_by_source`, `gemini_scripts` | creative_identity_registry.script_source | counts (`llm:*` → gemini) | A |
| `gemini_call_count` | — | — | **U** (not recorded) |
| `research_provider_requests` | topic_discovery_runs | queries and errors per provider | A/U (only while the ledger is on) |
| `gnews_requests_by_utc_day` | cache/gnews/usage.json | local counter | A/U |
| `provider_probe_failures` | provider_probes.json | last failure per provider | A/U (only after an on-demand probe) |

## F. Recovery analytics (`recovery_analytics`)

A cycle **needed recovery** if `retry_count > 0`, it had a `RETRY_POLICY_REARMED` or `OPERATOR_RETRY_REQUESTED` event, it was dispatched more than once, or it had a recorded dispatch failure.

| Metric | Calculation | St. |
|---|---|---|
| `jobs_needing_recovery`, `recovered` (those ending COMPLETED), `failed_after_recovery` (ending FAILED / EXPIRED) | counts | A |
| `recovery_success_pct` | recovered ÷ needing recovery | A/U |
| `recovery_window_expiration_pct` | expired ÷ finished | A/U |
| `avg_time_to_recover_minutes`, `median_…` | first `CYCLE_DISPATCH_FAILED` → `PRODUCTION_CYCLE_COMPLETED` | A/U |
| `recovered_within_60_min` | count of the above ≤ 60 min | A |
| `retry_count_distribution`, `total_retries` | persistent_jobs.retry_count | A |
| `rearm_events`, `operator_retries` | lifecycle events | A |

## G. Reliability trends (`reliability_trends`)

| Metric | Calculation |
|---|---|
| `daily_success` | per IST day: completed, not completed, success % (finished cycles) |
| `hourly_success_ist` | the same per scheduled IST hour |
| `daily_failures_by_cause` | from B |
| `daily_renderer` | from C |
| `daily_providers`, `daily_topic_discovery` | from D |

## H. Daily summary (`/api/ops/daily-summary`)

It contains production, major failures (top 5 patterns), repeated problems, API usage, recovery, renderer, topics, anomalies and recommendations.

**Anomaly rules:**

| Rule | Severity |
|---|---|
| Day completion ≥ 20 points below the 7-day rate (with ≥ 4 finished) | high |
| ≥ 3 expired cycles | medium |
| A failure pattern ≥ 3 times | high if DETERMINISTIC / PERMANENT, else medium |
| Any YouTube error category | medium |
| A quota bucket ≥ 80% (estimate), or an active Google block | high |
| Topic-exhaustion failures | high |
| Low-pool runs | medium |
| Provider errors | low |
| Failed renders | high |

**Health:**
- `UNKNOWN`: no finished cycles, or the data is unavailable;
- `HEALTHY`: completion ≥ 80% and no high-severity anomaly;
- `DEGRADED`: completion ≥ 50%;
- `CRITICAL`: completion below 50%.

Recommendations are text for the operator. Nothing is executed.
