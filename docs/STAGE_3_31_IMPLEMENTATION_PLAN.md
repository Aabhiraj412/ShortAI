# Stage 3.31: Topic Intelligence Implementation Plan

**Status:** PLAN ONLY. Implementation needs review and approval of the audit first.
**Inputs:** `STAGE_3_31_TOPIC_INTELLIGENCE_AUDIT.md` (findings) and `STAGE_3_31_PROPOSED_ARCHITECTURE.md` (design). Section numbers are given as "audit §N" or "architecture §N".

## 1. Principles

- Fix verified defects first: those that already cause failures, then those that cause poor topics.
- Each sub-stage:
  - is independently testable;
  - ships behind a flag where it changes selection;
  - leaves the schedule, concurrency, renderer, Pop Info and publishing untouched.
- Tests never touch the live database (existing pytest guards in `TopicLedger`, `CategoryDistributionTracker` and the identity registry).
- Additive schema only; no migration rewrites existing rows.

## 2. Sub-stages (priority order)

| # | Sub-stage | Fixes | Depends on | Independent? |
|---|---|---|---|---|
| **3.31a** | Discovery observability: persist `discovery_runs` and per-candidate decisions (`topic_candidates` rows with status and reason) from today's inline engine, without changing selection | audit §4 / defect 7 | — | Yes |
| **3.31b** | Category classifier fix: whole-word matching, confidence, no History tie-break, use the discovery hint | defect 2 | — | Yes |
| **3.31c** | Pool and rotation fixes in the existing engine: persisted rotation state replaces the frozen `history_offset`; GNews candidates no longer truncated (per-family quota inside the 18 cap); featured-feed date rotation; use the "In the news" section | defects 1, 5, 9 | 3.31a (to measure) | Mostly |
| **3.31d** | Exhaustion recovery in the current flow: when `filter_fresh` empties the list, run one bounded expansion pass (next page or query) before raising `DuplicateCreativeIdentity` | defect 1 | 3.31c | Yes |
| **3.31e** | Subject identity: resolve Wikipedia page ID / Wikidata QID; event signature; add identity checks to `check_content_collision`; validation dataset v1 | defect 4 | — | Yes |
| **3.31f** | Story gates and scoring v2: non-story blocklist (countries, songs, politics biographies, lists), signal-based dimensions, topic-level hook potential via `hook_evaluator`, score versioning, optional batched Gemini ratings | defects 3, 8 | 3.31a, 3.31b | Yes, behind flag `scoring.v2` |
| **3.31g** | Topic-level verification: claim extraction, second-source check, `VerificationReport` in the dossier; RSS and GNews leads must resolve to independent evidence | defect 6 | 3.31e | Yes |
| **3.31h** | Topic reservoir and refill job (architecture §2, §11); selection reads from the reservoir with the inline fallback | defects 1, 7 | 3.31a, c, e, f, g | Flag `topic_reservoir.enabled` |
| **3.31i** | Embedding dedupe layer (optional, Gemini embeddings, cached) | residual of defect 4 | 3.31e dataset | Yes |
| **3.31j** | Performance priors (needs Analytics consent and at least 30 videos with metrics) | capability 14 | 3.30 Analytics authorized | Deferred |

**Recommended order:** a → b → c → d (these stop the failures and fix labels quickly) → e → f → g → h → i → j.

**Can be built in parallel:**
- a, b and e;
- f and g, once their dependencies are in.

## 3. Code modules

| Module | Change | Sub-stage |
|---|---|---|
| `backend/app/content/topic_reservoir.py` | NEW: schema init, candidate upsert, status transitions, `select(job_id)` | a, h |
| `backend/app/content/curiosity_topic_engine.py` | CHANGE: write audit rows; rotation state; per-family pool quota; feed date rotation; "In the news"; expansion pass | a, c, d |
| `backend/app/content/creative_production_policy.py` | CHANGE: `classify_topic` whole-word matching plus confidence | b |
| `backend/app/content/subject_identity.py` | NEW: page ID / QID resolution (cached), event signature | e |
| `backend/app/content/topic_ledger.py` | CHANGE: optional subject-key check in `check_content_collision` (additive argument) | e |
| `backend/app/content/topic_scoring.py` | NEW: gates, scoring v2, model-rating batch (uses the existing Gemini client) | f |
| `backend/app/content/topic_verifier.py` | NEW: claim extraction plus second-source verification | g |
| `backend/app/orchestration/crazzydocs_master_orchestrator.py` | CHANGE (small): selection block calls `topic_service.select()` when the flag is on | h |
| `backend/app/scheduler/scheduler.py` | CHANGE: add `topic_reservoir_refill_job` (non-production, skipped during renders) | h |
| `backend/app/main.py`, `templates/dashboard.html` | CHANGE: Topic pool card (eligible count, provider yield, rejections) | a, h |
| `backend/app/content/fact_verifier.py`, `topic_engine.py`, `duplicate_detector.py`, `agents/trend_researcher.py`, `agents/topic_selector.py` | Leave in place. Mark as legacy in docstrings. Removing them needs separate approval. | — |

## 4. Database changes

All additive and created with `CREATE TABLE IF NOT EXISTS` (architecture §12):
- `topic_candidates`
- `discovery_runs`
- `provider_rotation_state`

Rules:
- No changes to `content_ledger`, `creative_identity_registry`, `persistent_jobs` or publishing tables.
- Back up `studio.db` with the online-backup procedure before the first deploy that creates the tables.
- An optional one-time **read-only** import seeds `topic_candidates` with USED rows from the existing ledger and registry. It writes only to the new table.

## 5. Validation dataset (designed now, built in 3.31e)

**File:** `backend/tests/data/topic_validation_set.json`. At least 120 hand-labelled candidates.

**Fixtures:**
- Built from stored dossiers and synthetic text.
- No production jobs, renders or uploads.
- No network: provider responses are recorded fixtures.

| Group | Count | Examples / purpose |
|---|---:|---|
| Exact duplicates | 10 | Same title with punctuation or case changes |
| Rewritten duplicates | 15 | "Bank bought for $1" × 3 phrasings; Yamaguchi vs "Hiroshima and Nagasaki survivor" |
| Semantically similar, same subject different angle | 10 | Apollo 12 launch vs Apollo 12 lightning strike |
| Unrelated, shared entity | 10 | Rocky Mountains vs Rocky Mountain locust; Apollo 12 vs Apollo 13 |
| Same person, different events | 8 | Two distinct episodes in Tesla's life |
| Strong story candidates | 15 | Mary Celeste, Kola borehole, Molasses Flood |
| Weak / non-story candidates | 15 | "Pakistan", a pop song, a lab-method press release, a politician biography |
| Visually rich vs poor | 10 + 10 | Space missions vs abstract theorems |
| Reliable vs single-source / contradicted | 8 + 8 | Wikipedia + primary source vs a lone press blurb; a conflicting-date pair |
| Category spread | ≥3 per category | Covers all 20 categories, including bodies containing "award" or "several" |
| Recently published / historical subjects | 10 | From the real ledger titles |

**Each item records:**
- the expected labels: `is_story`, `category_key`, `duplicate_of`, `relation` (one of exact, rewrite, related, same_person_diff_event, unrelated), `verifiable`;
- a rough expected score band.

**Metrics and acceptance thresholds:**
- Duplicate detection: precision ≥ 0.95 and recall ≥ 0.90 on exact + rewrite; false-duplicate rate ≤ 5% on unrelated / shared-entity pairs.
- Category accuracy ≥ 85%; no category receives more than 2× its true share.
- Story gate: ≥ 90% of weak candidates rejected, ≤ 10% of strong candidates rejected.
- Score consistency: Spearman ≥ 0.6 against a manual ranking of the strong / weak set; identical inputs give identical scores (determinism test).
- Verification: 100% of contradicted fixtures rejected; 0 single-source leads eligible.

## 6. Regression strategy

- The existing topic tests must all pass unchanged:
  - `test_creative_identity_registry.py`
  - `test_stage_3_21h_content_freshness.py`
  - `test_stage_3_21k_…`
  - `test_stage_3_21m_…`
  - `test_stage_3_21i_…`
  - `test_pixabay_gnews_330.py`
  - `test_grounded_script_writer.py`
- Run the full suite each sub-stage (baseline: 865 passed, 4 known failures).
- **New tests per sub-stage:**
  - a: audit rows written, with no change in ranking (golden comparison on recorded fixtures).
  - b: substring traps ("award", "several", "operation"); ties; low confidence.
  - c: rotation advances across simulated production cycles; GNews survives the pool cap; feed date rotates.
  - d: an emptied pool triggers exactly one bounded expansion and no duplicate selection.
  - e to g: dataset metrics above, as parameterized tests.
  - h: reservoir concurrency (two jobs never reserve the same subject); with the flag off, behaviour is byte-identical; with an empty reservoir, the inline fallback runs.
- **Shadow mode before enabling each flag:**
  - New scoring and the reservoir run alongside production for at least 48 h.
  - Results are logged only.
  - Compare selected subjects and rejection reasons on the dashboard before switching.

## 7. Acceptance criteria (Stage 3.31 complete)

1. 0 cycle failures caused by topic exhaustion over 7 days at the active schedule, while eligible subjects exist.
2. Every production has a persisted discovery run, candidate decisions, score breakdown with `score_version`, and a verification report.
3. Dataset metrics (§5) met in CI.
4. No selected topic matches the non-story gate classes. Spot-check: 20 consecutive selections reviewed by the user.
5. Category labels on new productions are ≥ 85% correct in the user's spot-check, and no category exceeds 25% of a rolling 24.
6. No change to the schedule, concurrency, recovery windows, renderer, Pop Info or publishing (verified by diff review and existing tests).
7. External API usage within budgets: GNews ≤ 90/day; Gemini calls per day reported on the dashboard.

## 8. Risks

| Risk | Mitigation |
|---|---|
| Stricter gates shrink the pool and *increase* starvation | Ship 3.31c/d (supply) before 3.31f (gates); watch eligible count in shadow mode |
| Wikidata or Wikipedia rate limiting | Cache resolutions; serial requests; descriptive User-Agent; resolve in refill jobs, not at cycle time |
| Gemini quota or cost | Batched, cached ratings; heuristic fallback; daily cap |
| Reservoir picks stale topics | Re-check freshness at reservation time; expire unused candidates after N days (a value to be decided by the user; none is invented here) |
| Schema additions touch the live DB | Additive tables only, created at startup; backup first; tests isolated from the live DB |
| A selection-path regression stops production | Feature flags default off; inline fallback; byte-identical behaviour with flags off (tested) |
| Phys.org or GNews terms restrict commercial reuse [UNVERIFIED] | Use them as leads only; evidence and script text come from independently verified sources; confirm terms before relying on them |

## 9. Rollback

- **Flags:** set `topic_reservoir.enabled=false` and `scoring.v2=false` in `creative_policy_config.json`. The next cycle uses today's inline path; no restart is needed if config is read per run (verify in 3.31h; otherwise use the standard daemon restart).
- **Code:** `git revert` the sub-stage commit on `main`, then redeploy with the standard procedure.
- **Data:** the new tables can be left in place (unused) or dropped only with the user's approval. Existing tables are never modified, so no data restore is needed. The pre-deploy `studio.db` backup remains available.

## 10. Decisions needed from the user before Phase 2

1. Approve this audit and the sub-stage order.
2. Should non-story subjects (countries, songs, politics biographies) be excluded outright, or only down-ranked?
3. Reservoir candidate expiry period (no default is assumed).
4. Whether Gemini may be used for batched topic ratings and embeddings (cost and quota).
5. Whether to keep GNews as a discovery source once 3.31c measures its yield, or retire it.
6. Confirm the intended production schedule: the active profile is CUSTOM with 24 hourly cycles (audit §0). It determines the reservoir size.
