# Stage 3.35: YouTube Analytics ingestion and audience learning

## 1. Metrics (YouTube Analytics API v2, verified against this channel)

| Group | Metrics | Status on this channel (2026-11-21, read-only dry run) |
|---|---|---|
| Core (top-videos report, `dimensions=video`) | views, engagedViews, estimatedMinutesWatched, averageViewDuration, averageViewPercentage | Available for 22 of 66 published videos; the other 44 are `not_yet_populated` (YouTube reports with a lag of about 2 days) |
| Engagement | likes, comments, shares, subscribersGained | Available; comments, shares and subscribers are genuinely 0 (`available_zero`, never confused with missing) |
| First-7-days window | the core and engagement metrics for publish day to day 7 (fixed, comparable across videos) | 1 video eligible so far (11 views, 47.6 % average viewed) |
| Retention | `audienceWatchRatio` by `elapsedVideoTimeRatio` (summarised at 10/25/50/75/100 %) | `not_yet_populated`: YouTube returns no curve at this view count |
| Traffic sources | views by `insightTrafficSourceType` | Available (for example YT_SEARCH 5, YT_CHANNEL 4, YT_OTHER_PAGE 2) |
| Impressions / impression CTR | Not offered by the YouTube Analytics API (YouTube Studio only) | **Unavailable**: not collected, not invented |

**Statuses per metric:** `available`, `available_zero`, `not_yet_populated`, `unavailable`, `request_failed`. A missing value is never stored as 0.

## 2. Ingestion

- **Lifetime-to-date snapshots:** daily while a video is up to 28 days old, weekly after that. One row per video per day; re-running the same day fetches nothing (idempotent).
- **First 7 days:** recorded once per video when day 7 is complete and reported (video age 8 days or more). Stored as `window_kind = first_7_days`.
- **Batching and budget:** 50 videos per query; at most 120 API calls per run; quota and 5xx errors retried with backoff; the budget is reported.
- **Sync state:** last attempt, last success, counts and last error, stored in the settings table (`youtube_analytics_sync_state`).
- **Schedule:** the existing daily job at 07:15 IST, followed by a learning refresh.
- **Storage:** the existing `video_performance` table (an additive `window_kind` column); no new analytics database.

## 3. Data model and associations

`learning_records` (one per published video) joins:
- the video ID and production job ID;
- topic and category;
- hook style and hook text (3.32 creative record);
- craft scores (14 dimensions);
- duration and publish hour (IST);
- scene composition from the rendered plan;
- topic v1/v2 scores and provider (3.31 ledger);
- performance (first-7-days window when available, else lifetime-to-date, flagged).

Script fingerprints and render manifests are linked through the job ID.

## 4. Learning methodology (`creative_intel/learning.py`)

- **Claims need at least 30 videos with first-7-days metrics.** Below that, everything is marked **preliminary**.
- **Groups:** category, hook type, duration bucket, publish hour, footage presence, photo presence. A group comparison needs at least 10 videos per group and a relative difference of at least 15 %.
- **Robust statistics:** medians and winsorised means (p90); outliers above Q3 + 3 IQR are flagged, not removed.
- **Craft against audience:** Spearman correlations of craft dimensions with average % viewed and views, with a permutation p-value, labelled "correlation, not causation".
- **Confidence labels:** insufficient (n < 5), low (n < 10), medium (n < 30), high (n ≥ 30).
- **Recommendations:**
  - each carries evidence, sample size, confidence, expected benefit and limitations;
  - status `proposal` or `insufficient_data`;
  - `applied: false` and `requires_authorization: true`;
  - reproducible from the run's input hash; runs are logged in `learning_runs`.
- **Topic learning:** category performance feeds 3.31K scoring v2's `historical_performance` signal (5 % weight, neutral below 3 videos per category). It stays in **shadow**: no flag was changed.

## 5. Current data coverage

| Measure | Value |
|---|---|
| Published videos tracked | 66 |
| With analytics data | 22 |
| With first-7-days metrics | 1 (needs 29 more for claims) |
| Total views (lifetime) | 134; the most-viewed video has 12 |

**Learning readiness: preliminary.** Recommendations currently list what each still needs. A video becomes eligible 8 days after publication, and most of the 66 were published from 2026-11-14 onward, so the first-7-days sample should pass 30 videos around 2026-11-28.
