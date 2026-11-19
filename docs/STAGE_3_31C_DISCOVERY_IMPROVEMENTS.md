# Stage 3.31C: Discovery Intelligence Improvements

**Status:** implemented and tested. **Disabled.** The flags `production_query_rotation`, `gnews_candidates` and `wikipedia_in_the_news` all default to `off`. Not deployed; not committed.

## A. Production query rotation

**Root cause (verified in code).** `discover_live_candidates` rotated queries with `history_offset = len(validation_history.load_records())`. The orchestrator records validation history only outside PRODUCTION (`crazzydocs_master_orchestrator.py`: `if execution_context != ExecutionContext.PRODUCTION and val_hist is not None`), so in production the offset stays frozen (7 live records).
- Every cycle used the same Wikipedia query per category, the same NASA query ("black hole accretion") and the same featured-feed date (3 days ago).
- Test `test_without_rotation_production_repeats_the_same_queries` reproduces this.

**Fix (flag `production_query_rotation: on`):**
- Persisted counters in `topic_discovery_rotation` (`RotationStore` in `candidate_ledger.py`) replace `history_offset`:
  - `wikipedia_search:<category>`: query index. Once every query of a category has been used, the next **result page** is requested (`sroffset` 0 → 6 → 12).
  - `nasa_library`: NASA query index.
  - `wikipedia_featured`: feed date, rotating through 1–7 days back.
  - `category_order`: rotates the order categories are tried in when recent counts tie.
- **Deterministic:** the same database state gives the same queries.
- **Survives restarts:** stored in SQLite.
- **Failure-safe:** a counter advances only after its request *succeeded*, and all advances are written in one transaction at the end of the run. A timed-out or empty response never advances or corrupts the state.
- **No new cycles or requests:** the same number of requests per cycle as before; only the query text and page change.

## B. GNews integration

**Root causes (verified).**
1. Ten top headlines per category almost never contain the same story from two outlets. Offline replay of the three cached responses: 30 articles, 0 corroborated events.
2. GNews candidates were appended last to a list cut at 18, so they were dropped whenever Wikipedia and RSS filled it.

**Fix (flag `gnews_candidates: on`).** `_gnews_candidates_v2` and `gnews_client.independent_source_count`:

| Rule | Behaviour |
|---|---|
| Provenance | Each headline cluster is recorded in the ledger with outlet, URL and publication time. |
| Stale / duplicate articles | Existing `dedupe_and_cluster` (≤ 3 days, URL dedupe); the number dropped is recorded. |
| Irrelevant | Sport, entertainment, politics (v2 out-of-scope lexicon) or blocked terms are rejected as `LOW_RELEVANCE`. |
| Syndication | `independent_source_count` counts each distinct headline once. Wire copies with the same headline carried by several outlets count as one, and several articles from one outlet count as one. |
| ≥ 2 independent outlets | Candidate as before (`multi_source_corroborated_headline`, factual reliability 7.5). |
| Single outlet | **Not evidence.** It becomes a *lead*: one Wikipedia search on the headline's words (≥ 2 shared significant words required); the matching article, after the normal duplicate check and enrichment, becomes the candidate. Evidence is the article (`NEWS_LEAD_ENCYCLOPEDIC_EVIDENCE`); the news URL is kept only as "discovery provenance, not evidence". The headline itself is recorded as `INSUFFICIENT_EVIDENCE` with `converted_to`. |
| Lead budget | At most 2 lead lookups per run (`GNEWS_MAX_LEADS`); further single-source headlines are recorded as `INSUFFICIENT_EVIDENCE`. |
| Rate limits | Unchanged: 3 `top-headlines` requests per run, 6 h cache, local daily budget of 90. Errors are recorded per category with no retry storm. |
| Pool ordering | `assemble_candidate_pool` reserves slots for small families (`gnews` 3, `wikipedia_itn` 2) and fills the rest in the original order. Dropped candidates are recorded as `POOL_CAPACITY_LIMIT`. Ranking order is unaffected. |

**Factual verification is not weakened.** A single-source headline never becomes a candidate on its own. The 7.5 factual gate and the duplicate gates are unchanged.

With the flag off, the existing 3.30 path (`_gnews_candidates`, corroborated only) runs exactly as before. The test confirms the legacy path is used.

## C. Wikipedia "In the news" (flag `wikipedia_in_the_news: on`)

- Parsed from the **same featured-feed response** discovery already fetches, so no extra request is made (tested: one feed call).
- Each entry: the story text is the discovery lead; the first linked article that is not blocked, a country, a city, a capital, a list or a deaths page is the subject.
- Malformed entries (not a dict, no links, no story) are skipped and counted in the run notes. Entries with no eligible link are recorded as `LOW_RELEVANCE`.
- Candidates go through the normal enrichment, duplicate, factual and category gates under provider family `wikipedia_itn`, with reserved pool slots.
- Wikipedia is **not** treated as verification: these candidates are labelled `SINGLE_SOURCE_ENCYCLOPEDIC`, like every Wikipedia candidate. Topic-level verification is 3.31g.

## D. Provider contribution metrics

When the candidate ledger is on, every run stores per provider family in `topic_discovery_runs.provider_stats_json`:

- `queries`
- `articles` (items retrieved)
- `candidates_created`
- `rejected`
- `accepted`
- `duplicates`
- `errors`
- `unique_accepted`: accepted candidates no other family proposed in the same run

The run also stores the rotation values used (`rotation_json`) and the discovery plan (queries, page offsets, feed date, NASA query; `notes_json`).

## Tests: `backend/tests/test_discovery_improvements_331c.py` (16 tests)

| Area | Test |
|---|---|
| Frozen-rotation defect reproduced (flag off) | `test_without_rotation_production_repeats_the_same_queries` |
| Rotation advances each production cycle | `test_rotation_advances_each_production_cycle` |
| Persists across restart; deterministic | `test_rotation_persists_across_restart_and_is_deterministic` |
| Failed requests do not advance or corrupt | `test_failed_request_does_not_advance_rotation` |
| Result paging after all queries are used | `test_search_paging_after_all_queries_used` |
| Feed date rotation | `test_featured_feed_date_rotates` |
| Syndication / independence | `test_independent_sources_and_syndication` |
| Single-source lead → encyclopedic evidence, provenance kept | `test_gnews_single_source_headline_becomes_lead_with_encyclopedic_evidence` |
| Corroborated kept, sport rejected | `test_gnews_corroborated_event_kept_and_irrelevant_rejected` |
| Stale dropped; lead budget bounded | `test_gnews_stale_articles_dropped_and_lead_budget_bounded` |
| Rate-limit / failure handling | `test_gnews_failure_and_rate_limit_never_break_discovery` |
| GNews no longer lost to pool ordering | `test_pool_assembly_keeps_reserved_families` |
| Flag off uses the legacy GNews path | `test_gnews_flag_off_uses_legacy_path` |
| In the news: no extra request, malformed entries, country link refused | `test_in_the_news_candidates_reuse_feed_and_handle_malformed` |
| In the news off | `test_in_the_news_off_ignores_section` |
| Provider metrics persisted | `test_provider_metrics_are_persisted` |

All provider responses are mocked; no API quota was used.

## Limitations

- GNews leads add up to 2 Wikipedia searches plus 6 enrichment requests per run when enabled.
- The "≥ 2 shared words" match is lexical, so a lead can resolve to a broader article (for example the general topic rather than the specific event).
- Rotation "shadow" mode is not offered. With the ledger on, the plan actually used is recorded every run, which already shows the frozen behaviour.
