# Stage 3.31A: Candidate Intelligence Ledger

**Status:** implemented and tested. **Disabled** (flag `candidate_ledger` defaults to `off`). Not deployed; not committed.

## Problem (audit §4)

Discovery decisions were held only in memory (`last_discovery_audit`), so a rejected candidate, its score or the reason it lost could not be traced afterwards. The only evidence was a handful of log lines.

## What was built

**`backend/app/content/candidate_ledger.py`** (new) contains three parts:

| Part | Role |
|---|---|
| `DiscoveryTrace` | Collects every candidate decision and per-provider counts during one discovery run, in memory. |
| `NullTrace` | Used while the flag is off. Every call is a no-op, so the current production path does no extra work and writes nothing. |
| `CandidateLedger` | Persists a trace in **one transaction** to four additive SQLite tables. |

The trace records every point where a candidate can be dropped:

- discovery filters
- cross-provider dedupe
- pre-enrichment duplicate check
- enrichment
- RSS filters
- GNews relevance and corroboration
- the pool cap
- scoring gates
- the duplicate gate
- the category gate
- ranking
- the creative-identity gate
- selection
- production outcome

### Tables

All tables are created with `CREATE TABLE IF NOT EXISTS`, and only when the flag is on. No existing table is altered.

| Table | Key | Content |
|---|---|---|
| `topic_candidate_ledger` | `candidate_id` | Identity and display: title, normalized title, summary, category (v1), v2 classification. Provenance: provider, provider family, source URL, sources, publication date. Dates: first/last discovered, times seen. Scoring: entities, score, score breakdown. Decision: duplicate result and match, verification status, selection status, rejection reason and stage, selected job. Outcome: final outcome and reason. |
| `topic_candidate_events` | `event_id` (hash of content) | Append-only decision history per run. `INSERT OR IGNORE`, so retries never duplicate events. |
| `topic_discovery_runs` | `run_id` (= job id) | Attempts, flags, provider metrics, rotation used, recovery report, discovery plan, counts, selected candidate, outcome. |
| `topic_discovery_rotation` | `provider_key` | Rotation counters (used by 3.31C). |

### Identity and idempotency

- `candidate_id = "tc_" + sha256(provider_family | normalized_title)[:20]`. The same subject from the same provider family always maps to one row; the same subject from two families gives two rows that can be joined on `normalized_title`.
- `run_id` is the job id. A retried job re-uses it:
  - `attempts` increments;
  - `times_seen` does not;
  - identical decisions produce identical `event_id`s and are ignored.
- A new job that sees the same candidate increments `times_seen`.

### Status model

`DISCOVERED → ELIGIBLE | REJECTED → SELECTED → PRODUCED | FAILED`

- `PRODUCED` is final. A later rediscovery keeps the row PRODUCED and records the attempt as an event (`TRANSITION_IGNORED`).
- Invalid jumps (for example ELIGIBLE → PRODUCED) are refused and recorded.
- Candidates still under evaluation (`DISCOVERED`, `ELIGIBLE`) never carry a rejection reason.

### Structured rejection reasons

- The requested set: `DUPLICATE_EXACT`, `DUPLICATE_SEMANTIC`, `INVALID_CATEGORY`, `LOW_RELEVANCE`, `INSUFFICIENT_EVIDENCE`, `SOURCE_UNRELIABLE`, `ALREADY_PUBLISHED`, `RECENTLY_USED`, `LOW_STORY_POTENTIAL`, `TOPIC_EXHAUSTION_FILTER`, `PROVIDER_FAILURE`.
- One addition: `POOL_CAPACITY_LIMIT`, for candidates cut by the pool cap. It was needed to make the GNews truncation visible.
- An unknown reason raises `ValueError`. That is a programming error and is caught by the tests.

Mapping from existing gates:

| Existing gate | Recorded reason / stage |
|---|---|
| "List of …" titles or blocked terms | `LOW_RELEVANCE` / `discovery_filter` |
| Same title earlier in the run | `DUPLICATE_EXACT` / `cross_provider_dedupe` |
| `check_content_collision` text containing "cooldown" or "produced N days ago" | `RECENTLY_USED` |
| … containing "exact" or "fingerprint" | `DUPLICATE_EXACT` |
| … any other similarity | `DUPLICATE_SEMANTIC` |
| Enrichment returned nothing | `INSUFFICIENT_EVIDENCE` / `enrichment` |
| Composite 0 because factual < 7.5 | `SOURCE_UNRELIABLE` / `scoring_gate` |
| Composite 0 for another reason | `INSUFFICIENT_EVIDENCE` / `scoring_gate` |
| Creative-identity registry | `RECENTLY_USED` / `identity_gate`, match = conflicting job |
| v2 category gate (3.31B, `on`) | `INVALID_CATEGORY` / `category_gate` |

- `ALREADY_PUBLISHED`, `LOW_STORY_POTENTIAL` and `TOPIC_EXHAUSTION_FILTER` are defined but not emitted yet. No current gate decides those, and none was invented. Story gates are planned for 3.31f.
- Verification status is labelled honestly:
  - `SINGLE_SOURCE_ENCYCLOPEDIC` (Wikipedia, NASA)
  - `SINGLE_SOURCE_PRESS_SUMMARY` (Phys.org)
  - `MULTI_SOURCE_CORROBORATED_HEADLINE`
  - `NEWS_LEAD_ENCYCLOPEDIC_EVIDENCE`
  - `UNVERIFIED_HEADLINE`

  Nothing is marked "verified": topic-level verification is 3.31g.

### Integration points

- **`CuriosityTopicEngine`:**
  - `begin_run(job_id)`; `_start_trace()` and `_flush_trace()` inside `discover_and_rank_live_topics`;
  - `record_identity_rejections`, `record_selection`, `record_outcome`;
  - all wrapped so that a ledger error is logged and never raised.
- **`CrazzyDocsMasterOrchestrator`:** calls `begin_run` before discovery, the identity-rejection hook after `filter_fresh`, `record_selection` after the topic is chosen, and `record_outcome` (PRODUCED after `record_content`, FAILED in the exception handler). Each call is looked up with `getattr`, so test doubles without the methods still work.

### Safety

- A database error (locked, corrupt, missing table) is logged once and discovery continues. Missing tables are recreated on the next write.
- Under pytest the live `studio.db` is refused (`live_db_refused`), mirroring the identity-registry guard.
- Writes are bounded: one transaction per flush; at most 300 candidates per run (discovery normally sees fewer than 60).
- `topic_history.json`, `content_ledger` and `creative_identity_registry` are not written by this feature (tested).

## Tests: `backend/tests/test_candidate_ledger_331a.py` (12 tests)

| Area | Test |
|---|---|
| Flag off writes nothing | `test_flag_off_writes_nothing` |
| Persistence, provenance, scores, no forced reason | `test_every_candidate_is_persisted_with_provenance_and_scores` |
| Structured reasons (duplicate, short extract, list page) | `test_rejection_reasons_are_structured` |
| Duplicate ingestion and retry idempotency | `test_duplicate_ingestion_and_retry_are_idempotent` |
| Status transitions, PRODUCED final, invalid jump refused | `test_selection_status_transitions` |
| FAILED outcome | `test_failed_outcome_is_recorded` |
| Provider failure | `test_provider_failure_is_recorded_and_discovery_continues` |
| Database recovery (dropped table, corrupt file, locked database) | `test_database_recovery` |
| History preserved (`content_ledger`, `topic_history.json`) | `test_historical_records_are_preserved` |
| Live database refused under pytest | `test_live_database_is_refused_under_pytest` |
| Stable ids, reason mapping, provider families | `test_stable_ids_and_reason_mapping` |
| Provider metrics | `test_provider_metrics_count_unique_contributions` |

## Enabling (not done)

Add `"topic_intelligence": {"candidate_ledger": "on"}` to `backend/data/creative_policy_config.json`, then restart the daemon (flags are read at engine construction). The ledger is purely observational: it does not change which topic is selected.

**Open decision:** retention for `topic_candidate_events`. At 24 cycles a day this is roughly 100–200 events per cycle. No retention period has been assumed.
