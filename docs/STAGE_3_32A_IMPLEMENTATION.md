# Stage 3.32A: Operational Intelligence (implementation)

**Status:** implemented and tested in the development worktree. **Not committed, not deployed.** It is read-only with respect to production.
**Base:** `e14efc5` (deployed). Worktree: `.claude/worktrees/crazzy-docs-verify-capabilities-ac634e`, branch `claude/scheduler-redesign-render-fix`.

## 1. Architecture

```
dashboard "Operations" tab ──GET──► /api/ops/intelligence?days=N          (localhost only)
                                    /api/ops/intelligence/{section}?days=N
                                    /api/ops/daily-summary?date=YYYY-MM-DD
                                              │  asyncio.to_thread
                                              ▼
                        OperationalIntelligence (backend/app/services/operational_intelligence.py)
                          bundle(window) ── one bounded read per window, cached 60 s ──┐
                            sqlite  file:...studio.db?mode=ro  + PRAGMA query_only=ON  │
                            files   production_progress_store.json (mtime-cached),     │
                                    cache/gnews/usage.json, provider_probes.json        │
                          sections: production_health · failure_intelligence ·          │
                                    renderer_reliability · topic_intelligence ·         │
                                    api_intelligence · recovery_analytics ·             │
                                    reliability_trends · daily_summary  ◄───────────────┘
```

- **One service, one loader.** `bundle()` reads every source once per window. All eight sections derive from that bundle, so a full dashboard refresh costs one set of bounded queries. A repeat within 60 s costs nothing.
- **Reuse, not duplication.** No new tables, no copied records.
  - Existing tables: `persistent_jobs`, `production_lifecycle_events`, `creative_identity_registry`, `youtube_api_requests`, `youtube_quota_blocks`, and the Stage 3.31 ledger tables.
  - Existing files: `production_progress_store.json` (written by `production_observability`), the GNews usage file, the provider probes.
  - Existing logic: `retry_policy.classify_error` (failure class), `youtube_quota.BUCKETS` / `pacific_day` (quota buckets and day), and the candidate-ledger vocabulary.
- **`QuotaLedger` is not instantiated.** Its constructor runs `CREATE TABLE IF NOT EXISTS`, which counts as a write. The same per-bucket aggregation is done with a read-only query.
- **Each section's metadata.** Every section returns:
  - `status`: `actual` (every source read), `partial` or `unavailable`;
  - `sources`;
  - `estimated`: metric names that are estimates;
  - `unavailable`: `{metric: reason}`;
  - `notes`.

  Unknown values are `null` with a reason, never `0`.

## 2. Files

| File | Change |
|---|---|
| `backend/app/services/operational_intelligence.py` | **New** (906 lines): service, root-cause catalog, template normalizer, sections, daily summary, cached singleton `get_service()` |
| `backend/app/main.py` | **Modified**: 3 GET endpoints, localhost-only (`require_local_control`), input validation, run in a worker thread |
| `backend/app/templates/dashboard.html` | **Modified**: "Operations" tab (button, section, styles, `refreshOps` and helpers, tab hook, 120-s refresh that runs only while the tab is open) |
| `backend/tests/test_operational_intelligence_332a.py` | **New**: 17 tests |
| `docs/STAGE_3_32A_IMPLEMENTATION.md`, `docs/STAGE_3_32A_METRICS_REFERENCE.md`, `docs/STAGE_3_32A_TEST_RESULTS.md` | **New** |

## 3. Dashboard ("Operations" tab)

- **Header:**
  - window selector: 24 h / 7 days / 30 days;
  - refresh button;
  - generation time;
  - database availability;
  - truncation notice;
  - legend: "estimate" / "unavailable".
- **KPI row:**
  - system health today: HEALTHY / DEGRADED / CRITICAL / UNKNOWN;
  - production success %;
  - running / queued;
  - production time (average, median, long-running excluded);
  - recovery success;
  - renderer success.
- **Charts** (pure CSS, no library):
  - daily success-rate columns;
  - 24 columns for success by scheduled IST hour.

  Red marks a day or hour below 50%; grey means no finished cycles.
- **Daily operational summary:** health, anomalies coloured by severity, and recommendations labelled "for operator review only; nothing is executed".
- **Recent failure patterns:** count, root cause, class(es), normalized pattern, last seen (IST), example jobs.
- **Cards:** renderer reliability, recovery statistics, topic discovery (with a provider contribution table and classifier v1 vs v2), API usage and quota (YouTube bucket table with usage bars; Gemini and Google-reported quota shown as *unavailable* with the reason).
- **Safety in the browser:** all values coming from the server are HTML-escaped (`escHtml`).
- **Loading:** the tab loads only when opened or refreshed, never on page load, so other tabs are unaffected.

**Verified:**
- with real (read-only) data in a static preview served on `localhost:8765`, using real JSON and mocked `fetch`;
- the page JavaScript parses in Node;
- no duplicate function names (the cause of the 3.30 "4 min" bug).

## 4. Database access strategy

- **Read-only by construction:**
  - URI `file:<path>?mode=ro` plus `PRAGMA query_only = ON`;
  - a missing database raises and is reported as unavailable; it is never created.
- **Short reads.** `studio.db` uses the rollback (DELETE) journal, so readers briefly hold a shared lock. Each read uses one connection with a 2-s busy timeout, and the queries are small.
- **Bounded:**
  - every query is time-windowed (1–30 days) and capped with `LIMIT` (5,000 jobs; 50,000 events, API rows and candidate events);
  - truncation is reported in `truncated`.
- **Production cycles only.** Job queries are restricted to `id LIKE 'cycle\_%'`, which excludes test and legacy jobs.
- **Caching.** A TTL cache (60 s, at most 64 entries) and an mtime cache for the 1.2 MB progress store, so repeat refreshes do not re-parse.
- **Failure isolation.** A locked or unreadable database marks sections unavailable. One section's exception never hides the others.
- **Measured on the live database (read-only):** full 7-day overview 150–180 ms cold, about 75 ms warm. The database file hash was unchanged by the analytics.

## 5. Safety verification

| Requirement | How it is met / verified |
|---|---|
| No production writes | `mode=ro` + `query_only`; tests prove INSERT and CREATE fail on the service connection and the file hash is unchanged |
| No production actions | The module calls no scheduler, retry, publishing, control or OAuth function. A test scans the source for forbidden calls (`run_cycle_generation`, `request_retry`, `upload_short`, `set_production_control_state`, `request_daemon_restart`, `QuotaLedger(`, `urlopen`, `requests.`). |
| No network | `urlopen` patched to fail during the test; none is called |
| Localhost only | `require_local_control`; a test confirms a non-loopback client gets 403 |
| No interference with writers | Concurrency test: a writer thread commits 150 jobs and events while 25 overviews run, with no writer errors |
| No config, flag, schedule or `topic_history.json` change | None touched (see the Git status in the results document) |
| No restart or deploy | None performed |

## 6. Known limitations

- **Per-attempt stage durations** are not stored. Stage timings come from the latest attempt in the progress store.
- **Recovery "time to recover"** measures first recorded failure to completion. Cycles re-armed days later (the 17–18 Nov quota handoffs) dominate the mean and median; `recovered_within_60_min` gives the operational view.
- **Failure classes** come from the existing retry policy. It stamps the recurring FFmpeg padding error as TRANSIENT although it behaves deterministically. That is reported as found, not reclassified.
- **Gemini:** only "scripts written by Gemini" is measurable; call counts are not recorded anywhere.
- **Google-reported quota** is unavailable without Cloud Monitoring access. Remaining quota is an estimate against documented default limits.
- **Topic analytics** cover only runs since the candidate ledger was enabled (2026-11-20 05:00 IST).
- **Expired cycles** often stored their status as their stage. Those are reported as `NOT_RECORDED`.
- **Indexes.** `production_lifecycle_events` has no timestamp index; window filters scan the table (about 950 rows today, about 130k a year at 24 cycles a day, still sub-100 ms). Adding an index is a schema change and was deliberately not done; it is proposed for when needed.
