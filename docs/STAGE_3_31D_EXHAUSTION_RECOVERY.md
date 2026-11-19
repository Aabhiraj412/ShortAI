# Stage 3.31D: Topic Exhaustion Recovery

**Status:** implemented and tested. **Disabled** (flag `exhaustion_recovery` defaults to `off`). Not deployed; not committed.

## Problem (audit §11)

On 2026-11-17, cycles 15, 16, 17 and 20 failed with `DuplicateCreativeIdentity`. The ranked pool held one or two candidates, each already reserved by an earlier failed cycle. The failure is classified PERMANENT and fails immediately, with no attempt to look further.

## What was built

### `CuriosityTopicEngine.recover_from_exhaustion(accept, deadline_utc, reserve_seconds, …)`

1. **Detect.** The orchestrator calls it in two places:
   - when the first pass returns no ranked topic;
   - when the creative-identity filter rejects every ranked topic.
2. **Inspect.** It uses what the first pass already saw: titles (never re-evaluated), the categories and queries used, page offsets and the feed date.
3. **Expand, step by step**, stopping as soon as an acceptable topic exists:

   | Step | Action |
   |---|---|
   | 1. `next_page_same_queries` | Next result page of the queries just used |
   | 2. `next_query_each_category` | The next query of each category just searched |
   | 3. `additional_categories` | Up to 4 categories not searched this run, least recently produced first |
   | 4. `additional_feed_dates` | Two featured-feed dates not read this run: featured article, most-read, on-this-day |

4. **Re-evaluate with the unchanged safety rules:**
   - the ledger duplicate / cooldown pre-check;
   - enrichment (extract and sentence minimums);
   - `score_candidate` (factual gate, duplicate gate);
   - the v2 category gate when enabled;
   - finally `accept`, which the orchestrator sets to `identity_registry.filter_fresh`.

   Nothing bypasses duplicate protection, and no unreliable topic is forced through.
5. **Report.** Every run gets a `TopicExhaustionReport`: steps (proposals, evaluated, eligible, accepted), requests, candidates evaluated, elapsed time, budget and the reason it stopped. It is logged and, with the ledger on, saved in `topic_discovery_runs.recovery_json`.

### Bounds

Defaults are in `ExhaustionLimits`; each can be overridden in the config.

| Bound | Default | Enforcement |
|---|---|---|
| Discovery attempts (steps) | 4 | step loop |
| HTTP requests | 60 | guarded getter raises a budget stop |
| Candidates enriched and scored | 24 | per-candidate check |
| Wall-clock time | 180 s | guarded getter; each request timeout is also capped to the remaining budget |
| Recovery deadline | job's `cycle_state.recovery_expiration_at` minus `min_attempt_seconds` (the retry policy's existing "time a production attempt needs") | budget = min(180 s, deadline − now − reserve). If ≤ 0, recovery is **skipped** with no requests |
| Per-provider politeness | 0.2 s between requests | guarded getter sleeps |

No loop can run beyond these bounds, and there is no retry inside recovery. The test HTTP override is restored afterwards.

### Orchestrator (`crazzydocs_master_orchestrator.py`)

- `_topic_exhaustion_recovery(job_id, identity_registry, is_full_live)`:
  - returns `[]` immediately unless the run is live and the flag is on;
  - reads the recovery deadline from `persistent_jobs` through the existing `retry_policy.recovery_deadline_utc`;
  - passes the identity filter as `accept`;
  - catches its own errors, so the original failure is never masked.
- `_exhaustion_suffix()` adds `[TOPIC_EXHAUSTED: recovery exhausted after N step(s), R request(s), C candidate(s) evaluated; stopped by …]` to the existing failure message. With the flag off, the messages are byte-identical to today's.
- Ordering is unchanged: identity filtering → recovery → hook → `identity_registry.reserve` → script gate → render (tested).
- When recovery is exhausted, the failure is still `DuplicateCreativeIdentity` (PERMANENT) or the original `RuntimeError`. Retry classification was deliberately not changed; see "Open decisions".

## Tests: `backend/tests/test_exhaustion_recovery_331d.py` (11 tests)

| Area | Test |
|---|---|
| Flag off: no requests, `disabled` | `test_flag_off_does_nothing` |
| Recovers from the next page; produced topics stay excluded; recorded in the run | `test_recovers_from_next_page_without_bypassing_duplicate_protection` |
| Identity gate still applies; informative exhaustion | `test_identity_gate_still_applies_and_failure_is_informative` |
| Request and candidate bounds | `test_request_and_candidate_bounds[max_requests / max_candidates]` |
| Time budget | `test_time_budget_and_politeness` |
| Recovery deadline: skipped when too late, capped otherwise | `test_recovery_deadline_is_respected` |
| HTTP override restored | `test_recovery_restores_test_http_override` |
| Orchestrator hook inert when off or unsupported | `test_orchestrator_hook_is_inert_when_flag_off` |
| Orchestrator recovers, reports, never runs for non-live | `test_orchestrator_recovers_and_reports` |
| Gate order unchanged | `test_orchestrator_source_order_unchanged` |

During development a test fixture produced near-identical fake names ("Silaki31" / "Silaki101"). The real duplicate detector rejected them as recent duplicates, so recovery correctly found nothing. This confirms recovery cannot bypass duplicate protection, and is a live example of the lexical false positives from the audit (to be addressed in 3.31e).

## Operational notes

- **Worst-case added time:** 180 s within a cycle with a 45-minute recovery window. The deadline cap guarantees recovery never consumes the time a production attempt needs.
- **Worst-case extra requests:** 60 Wikipedia requests per exhausted cycle. These happen only when the cycle would otherwise fail.

## Open decisions (not changed)

- Should an exhausted cycle stay PERMANENT, or become a deferred retry within its window? Feeds change during the day.
- Whether to raise or lower the default bounds once the ledger shows real recovery behaviour.
