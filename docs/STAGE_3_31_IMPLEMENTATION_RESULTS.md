# Stage 3.31 Phase 2: Implementation Results (3.31A–D)

**Date:** 2026-11-20 (IST)
**Branch / worktree:** `claude/scheduler-redesign-render-fix` in `.claude/worktrees/crazzy-docs-verify-capabilities-ac634e`, based on `589c2b5`.
**Delivery state:** **uncommitted, unpushed, undeployed**, as instructed. Every new feature is **off**.

## 1. Pre-implementation safety check (recorded before any change)

| Check | Result |
|---|---|
| Worktree branch / HEAD | `claude/scheduler-redesign-render-fix` @ `589c2b5`, clean (only untracked `media/`) |
| Main checkout | `main` @ `589c2b5` |
| Pre-existing user changes (main checkout) | ` M backend/app/content/topic_history.json` (+613 lines, written by production); untracked `backend/backups/`, `backups/`. **None touched**; all work happened in the worktree. |
| Daemon / dashboard | `/api/control-center/deployment` = OK; daemon PID 6680 CURRENT at `089ba03cc6a1558f`; dashboard PID 9736; worker idle |
| Active jobs | none (no QUEUED / RUNNING / RETRYING / RETRY_SCHEDULED / HANDOFF_PENDING) |
| Active schedule | profile `CUSTOM`, revision `ccf0bd04f159`, 24 cycles, 45-min recovery windows. **Not modified**, and not assumed to be intentional (audit §0). |
| Production control | RUNNING ("Resumed by operator via dashboard", 03:15 IST) |
| Database schema / migrations | Modules create their own tables with `CREATE TABLE IF NOT EXISTS` and additive `ALTER TABLE … ADD COLUMN` (TopicLedger, CreativeIdentityRegistry, QuotaLedger). Followed the same convention. |
| Backups | Online SQLite backup + `git bundle` into `D:\Agents\CrazzyDocs_backups\` (deploy procedure). Not needed here: nothing touched the live DB. |
| Live config | `backend/data/creative_policy_config.json` has **no** `topic_intelligence` block, so every flag is off. Not modified. |

## 2. Files

**New**

| File | Purpose |
|---|---|
| `backend/app/content/topic_intelligence_flags.py` | Six flags (off / shadow / on) + exhaustion bounds, read from the existing creative-policy config |
| `backend/app/content/candidate_ledger.py` | 3.31A trace, ledger tables, structured reasons; 3.31C rotation store |
| `backend/app/content/category_classifier.py` | 3.31B v2 classifier |
| `backend/tests/topic_fakes_331.py` | Offline fakes (HTTP routing, temporary databases) |
| `backend/tests/test_candidate_ledger_331a.py` | 12 tests |
| `backend/tests/test_category_classifier_331b.py` | 26 tests |
| `backend/tests/test_discovery_improvements_331c.py` | 16 tests |
| `backend/tests/test_exhaustion_recovery_331d.py` | 11 tests |
| `docs/STAGE_3_31A_CANDIDATE_LEDGER.md`, `…31B…`, `…31C…`, `…31D…`, this file | Documentation |

**Modified**

| File | Change |
|---|---|
| `backend/app/content/curiosity_topic_engine.py` | Flags; trace hooks at every drop point; rotation; v2 category hook; GNews v2 + news leads; "In the news"; pool assembly; `_score_and_filter` (extracted from `discover_and_rank_live_topics`, same logic); recovery; orchestrator hooks. With flags off, the code paths are behaviourally identical. |
| `backend/app/content/gnews_client.py` | `independent_source_count()` (additive) |
| `backend/app/orchestration/crazzydocs_master_orchestrator.py` | `begin_run`, identity-rejection, selection and outcome hooks (`getattr`-guarded); `_topic_exhaustion_recovery` and `_exhaustion_suffix`. Failure messages are unchanged when the flag is off. |

Not modified: the scheduler, retry policy, renderer, Pop Info, publishing, OAuth, the schedule, any config, `topic_history.json`, and any existing test.

## 3. Database changes

Additive only. They are created lazily, **only** when `candidate_ledger` or `production_query_rotation` is turned on:

- `topic_candidate_ledger`
- `topic_candidate_events`
- `topic_discovery_runs`
- `topic_discovery_rotation`

No existing table is altered. No live database record was read for writing, deleted or rewritten: all tests use temporary databases, and the live database is refused under pytest.

## 4. Feature flags

They live in `backend/data/creative_policy_config.json` under `"topic_intelligence"`. All default to `off`.

| Flag | Modes | Effect when enabled |
|---|---|---|
| `candidate_ledger` | off / on | Persists every candidate decision and per-provider metrics (observational only) |
| `category_classifier_v2` | off / **shadow** / on | Shadow records the v2 category; on assigns it and rejects out-of-scope / unknown subjects |
| `production_query_rotation` | off / on | Persisted query, page, feed-date and NASA rotation |
| `gnews_candidates` | off / on | GNews v2 (syndication-aware, single-source → encyclopedic lead) + reserved pool slots |
| `wikipedia_in_the_news` | off / on | "In the news" candidates from the existing feed response + reserved slots |
| `exhaustion_recovery` | off / on | Bounded extra discovery before failing; limits under `exhaustion_limits` |

Flags are read when the topic engine is constructed, so a change takes effect after a daemon restart. No feature activates merely because its code is deployed.

**Recommended activation order (needs your approval each time):**
1. `candidate_ledger: on` + `category_classifier_v2: shadow` for at least 48 h. This is observation only.
2. `production_query_rotation: on` + `exhaustion_recovery: on`. These increase supply first.
3. `gnews_candidates`, `wikipedia_in_the_news`.
4. `category_classifier_v2: on`. This one shrinks the pool, so it goes after the supply fixes.

## 5. Tests

**Focused (new):** 65 passed (A 12, B 26, C 16, D 11), about 90 s.
- All provider responses are mocked; no API quota was used.
- No production cycles, no renders, no uploads.

**Existing topic tests**, rerun after the changes with flags off:
- `test_creative_identity_registry`
- `test_stage_3_21h/i/k/m`
- `test_pixabay_gnews_330`
- `test_creative_identity_near_duplicates`

Result: 74 passed, 21 skipped (the same skips as before), 0 failed.

**Full suite:** see §5.1.

**Coverage of the new components.** `pytest-cov` is not installed and was not added to the production venv. Line coverage was measured with a small `sys.settrace` tracer over the four new test files:

| Component | Lines covered |
|---|---|
| `category_classifier.py` | 124 / 124 (100%) |
| `candidate_ledger.py` | 236 / 277 (85.2%) |
| `topic_intelligence_flags.py` | 40 / 49 (81.6%) |
| New / changed functions in `curiosity_topic_engine.py` | 336 / 419 (80.2%) |

Uncovered lines are mainly defensive `except` branches and the ledger's read helpers.

**Test-environment finding.** From Git Bash, `PYTHONPATH="$W;…"` with `/d/…` paths is **not** understood by Windows Python. Imports silently resolved to the main checkout's code through the venv. All Stage 3.31 runs used Windows paths (`D:\…`), and the imported module path was verified to be the worktree. Earlier stages' test commands should use Windows paths too.

### 5.1 Full-suite result

`pytest backend/tests` with the worktree on `PYTHONPATH` (Windows paths), flags off:

| Result | Count |
|---|---|
| Passed | **931** (including the 65 new tests) |
| Failed | **4**, all pre-existing (see below) |
| Skipped | 173 (unchanged) |
| Runtime | 572 s (9 min 32 s) |
| Newly introduced failures | **0** |

The 4 failures were reproduced on a clean, temporary checkout of `589c2b5` (without the 3.31 changes). The same 4 tests fail there, so they are the known baseline failures, not regressions:

- `test_production_dashboard_browser.py::test_dashboard_html_page_served`
- `test_stage_3_8_integration.py::test_end_to_end_orchestration_run` (QC blocks the non-Lemo-Opuscar test renderer by design)
- `test_youtube_branding.py::test_source_youtube_icon_exists`
- `test_youtube_branding.py::test_dashboard_html_contains_branding_elements`

The temporary checkout was removed afterwards. No existing test was modified.

## 6. Remaining defects and limitations

- Lexical duplicate detection is unchanged (3.31e). It still misses paraphrases and rejects near-identical names; seen again during test development.
- No topic-level fact verification yet (3.31g). Wikipedia and Phys.org candidates remain single-source and are labelled so.
- `ALREADY_PUBLISHED`, `LOW_STORY_POTENTIAL` and `TOPIC_EXHAUSTION_FILTER` are defined but not emitted; no current gate decides them.
- The v2 classifier is lexicon-based and English-only. Subjects outside the 20 categories come out UNKNOWN.
- When recovery is exhausted, the failure is still PERMANENT (retry classification deliberately unchanged).
- No retention policy for `topic_candidate_events` (a decision for you; none assumed).
- No historical recategorization was done; a read-only report is proposed in the 3.31B document.

## 7. Operational risks when enabling

| Risk | Mitigation |
|---|---|
| v2 `on` rejects UNKNOWN / out-of-scope subjects and shrinks the pool | Shadow first; enable after rotation and recovery |
| Recovery adds up to 180 s and 60 requests to an exhausted cycle | Capped by the recovery deadline minus `min_attempt_seconds`; only runs when the cycle would otherwise fail |
| GNews leads add ≤ 2 searches + ≤ 6 enrichments per run | Lead budget; GNews 6 h cache and daily budget unchanged |
| Ledger growth at 24 cycles a day | One transaction per run; retention decision pending |
| Long-lived daemon engine keeps per-run state (`_last_plan`, trace) | Reset at the start of every discovery run; recovery report reset per call |

## 8. Rollback

- **Before commit:** discard the worktree changes (`git checkout -- <files>`; delete the new files). Main and production are unaffected because nothing was committed or deployed.
- **After a future deploy:**
  - remove the `topic_intelligence` block, or set its flags to `off`, then restart the daemon; behaviour returns to the pre-3.31 path;
  - or `git revert` the commit.
  - The four new tables can stay unused; dropping them needs your approval. No existing table or record is affected.

## 9. Confirmation that production behaviour is unchanged

- **Nothing was committed, pushed, merged or deployed.** The running daemon (6680) and dashboard (9736) still run `089ba03cc6a1558f`.
- The schedule, recovery windows, concurrency, scheduler daemon, startup manager, renderer and Lemo-Opuscar adapter are untouched. So are Pop Info, OAuth, the publishing schedule and privacy policy, and `topic_history.json`.
- No database record was written by this work, and no production cycle, worker, render or upload was created.
- With every flag off (the default, and the live config), selection follows the existing approved path. This is verified by the unchanged existing tests and by `test_flag_off_writes_nothing`, `test_without_rotation_production_repeats_the_same_queries`, `test_gnews_flag_off_uses_legacy_path`, `test_in_the_news_off_ignores_section` and `test_orchestrator_hook_is_inert_when_flag_off`.

## 10. Intended commit (awaiting authorization)

Everything listed in §2 and nothing else: not `media/`, and not the main checkout's `topic_history.json`, `backend/backups/` or `backups/`.
