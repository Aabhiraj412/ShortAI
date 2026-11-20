# Stage 3.32C: Recommendation Rules

**How rules work:**
- Every rule lives in `backend/app/autonomy/recommendations/rules.py` and reads only Stage 3.32A data, over a 7-day window by default.
- **Observation** is what the data shows, always factual.
- **Hypothesis** is why it may happen. Its strength is CONFIRMED (the data shows the mechanism), STRONG or WEAK.
- When the minimum evidence is missing, a rule records an *insufficient evidence* note instead of a recommendation.
- Suggested actions are text only. None changes retry policies, schedules, recovery deadlines, OAuth scopes, flags, or duplicate or quality checks.

**Dedupe key:** `rule_id:subject`. TTL: 72 h unless stated otherwise.

## A. Production reliability

| Rule (subject) | Detects | Minimum evidence | Trigger | Severity | Hypothesis (strength) |
|---|---|---|---|---|---|
| `prod.failure_rate_rising` (window) | Increasing production failures | ≥ 4 finished cycles in the last 24 h and ≥ 8 earlier in the window | Last-24 h non-completion rate ≥ 20 points above the earlier rate | high | A new or worsening cause started recently (WEAK) · TTL 24 h |
| `prod.recovery_window_expirations` (window) | Recovery-window expirations | ≥ 10 finished, ≥ 5 expired | Expired ÷ finished ≥ 20% | critical if ≥ 35%, else high | Stage delays: STRONG if ≥ 60% of expirations stopped at one recorded stage, otherwise WEAK ("cannot be attributed") |
| `prod.repeated_stage_failure` (stage) | Repeated failures at the same stage | ≥ 3 failures with a recorded stage | A stage with ≥ 3 failures and ≥ 40% of recorded failures | high if ≥ 5, else medium | The dominant cause at that stage (STRONG if a cause is known, else WEAK) |
| `prod.abnormal_duration` (window) | Abnormal production duration | ≥ 6 completions with times (≤ 24 h) | ≥ 3 completions longer than max(3 × median, median + 30 min) | medium | Waiting on a stage or retries (WEAK) |
| `prod.approaching_deadline` (window) | Jobs repeatedly approaching recovery deadlines | ≥ 6 completions mapped to active-schedule windows | ≥ 3 and ≥ 15% of completions used ≥ 80% of their window | medium | Windows are tight for the production time (STRONG) |

## B. Renderer intelligence

| Rule (subject) | Detects | Trigger | Severity | Deterministic vs transient |
|---|---|---|---|---|
| `render.failure_pattern` (`cause:template`) | FFmpeg errors, padding-dimension failures, render timeouts, Lemo-Opuscar failures, media acquisition, Windows file locks, renderer-compliance blocks | A 3.32A failure pattern (normalized message, grouped across jobs) with cause in that set and count ≥ 2 | high if deterministic or count ≥ 4, else medium | Padding "cannot be smaller than input": **CONFIRMED deterministic**. Non-transient retry class: STRONG deterministic. Same message ≥ 3× across ≥ 2 jobs: WEAK deterministic. `window.READY` timeout: WEAK (could be either). WinError 32: WEAK transient/environmental. When it is deterministic but the retry policy says TRANSIENT, the text says so and that **the policy is not changed**. |
| `render.abnormal_duration` (window) | Abnormal rendering duration | ≥ 6 completed renders with timings; ≥ 2 renders over 2 × median | low | WEAK |
| `render.media_acquisition` (window) | Media acquisition failures | `media_acquisitions_failed` ≥ 10 | low | WEAK |

## C. Topic intelligence (Stage 3.31 candidate ledger)

| Rule (subject) | Detects | Trigger | Severity | Hypothesis |
|---|---|---|---|---|
| `topic.exhaustion` | Topic exhaustion | ≥ 2 topic-exhaustion failures | high | Pool too narrow for the production rate (STRONG) |
| `topic.low_acceptance` | Low candidate acceptance | ≥ 30 rejections and acceptance < 20% | medium | Query or source quality (WEAK) |
| `topic.dominant_rejection_reason` (reason) | Repeated rejection reasons | ≥ 30 rejections and one reason ≥ 60% | low | Systematic mismatch with that criterion (STRONG) |
| `topic.provider_concentration` (provider) | Provider concentration | ≥ 10 accepted and one provider ≥ 60% of them | medium | Discovery depends on one provider (STRONG) |
| `topic.unproductive_provider` (provider) | Providers contributing no eligible candidates | ≥ 10 candidates created, 0 accepted | low | Queries do not match the eligibility criteria (STRONG). Supersedes `topic.unproductive_queries` for the same provider. |
| `topic.unproductive_queries` (provider) | Unproductive search queries | ≥ 3 queries returning articles but 0 candidates | low | Results are filtered before candidate creation (WEAK) |
| `topic.latest_run_rejections` (provider) | Whole contribution rejected in the latest run | Latest run: ≥ 10 created, 0 accepted | low | STRONG if another provider holds ≥ 60% of accepted candidates (concentration stated), else WEAK · TTL 24 h |
| `topic.classifier_disagreement` | Classification disagreement (v1 vs shadow v2) | ≥ 10 compared and ≥ 50% disagreement | low | Different vocabularies; the data does not show which is right (WEAK). v2 stays in shadow. |
| `topic.duplicate_detection` | Potential duplicate-detection weakness | ≥ 20 candidates; exhaustion failures > 0; (RECENTLY_USED + duplicates) ≥ 15% | medium | Early filtering may miss used topics, so the later integrity gate refuses them (WEAK). Neither check is changed. |

## D. API and provider intelligence

| Rule (subject) | Detects | Trigger | Severity | Notes |
|---|---|---|---|---|
| `api.youtube_errors` (`method:category`) | OAuth scope / auth errors; repeated YouTube errors | Scope, auth or permission category: ≥ 1. Others: ≥ 2 | medium (scope); medium / low | Scope: **CONFIRMED**, action risk HIGH; "review whether the workflow needs it; scopes are not changed" |
| `api.quota_pressure` (bucket) | YouTube quota anomalies | Google block active (**CONFIRMED**), or estimated use ≥ 80% of the documented default limit (WEAK, labelled estimate) | high | Never invents Google-reported quota; extension requests need the operator · TTL 24 h |
| `api.request_volume` | Unusual request volume | ≥ 4 days of data; today ≥ 200 units and > 2 × median of previous days | medium | WEAK · TTL 24 h |
| `api.provider_errors` (provider) | Provider request failures | ≥ 3 errors and ≥ 30% of queries | medium | WEAK |
| `api.http_504` | Repeated HTTP 504 | ≥ 2 failures whose message contains 504 / gateway timeout | medium | WEAK |
| *(covered by `topic.unproductive_provider`)* | Providers with very few eligible candidates | — | — | — |

## E. Recovery intelligence

| Rule | Detects | Trigger | Severity | Hypothesis |
|---|---|---|---|---|
| `prod.recovery_window_expirations` | Recovery-window expirations | see A | | |
| `recovery.window_consumed` (by_cycle, **one aggregated card**) | Cycles repeatedly consuming their entire window | Any cycle with ≥ 2 expirations | high if one cycle ≥ 4 or total ≥ 10, else medium | A slot-specific cause if one cycle stands out; otherwise a shared cause (WEAK) |
| `recovery.ineffective_retries` | Ineffective retries; repeated failure after retry | ≥ 5 needing recovery and ≥ 40% failed after recovery | medium | Deterministic errors cannot be retried away (WEAK); the retry policy is not changed |
| `recovery.long_duration` | Abnormally long recovery | Median time to recover > 240 min | low | STRONG if re-arm events explain it (quota handoffs), else WEAK |

## F. Content quality

| Rule | Detects | Trigger | Severity | Notes |
|---|---|---|---|---|
| `quality.qc_failures` (template) | Quality-control failures | QC pattern ≥ 3 | high if ≥ 5, else medium | Duration variant: "script length / narration pacing; QC stays as it is" (STRONG) |
| `quality.script_fallback` | Production consistency | ≥ 5 scripts with a source and ≥ 50% from the fallback writer | medium | WEAK |
| `quality.repeated_creative_pattern` (category) | Repeated creative patterns | ≥ 8 produced or selected topics and one category ≥ 50% | low | WEAK |
| `quality.engagement_and_hooks` | Low-performing content, hook quality, retention | — | — | **Always "insufficient evidence"**: YouTube Analytics data is not available. Nothing is invented. |

## Grouping and deduplication

- **Root-cause grouping** comes from Stage 3.32A: messages are normalized (numbers, addresses and paths removed; trailing parentheticals dropped) and grouped by (cause, template). Three FFmpeg messages with different filter addresses become one recommendation (tested end-to-end through the real 3.32A service).
- **One recommendation per key.** A repeated detection updates the open recommendation; a material change in priority, confidence or severity is audited as `RECOMMENDATION_UPDATED`.
- **Rejected:** suppressed for 7 days. **Resolved, then detected again:** a new recommendation that names the earlier one (`previous_id`).
