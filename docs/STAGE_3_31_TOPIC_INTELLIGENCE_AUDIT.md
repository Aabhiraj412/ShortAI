# Stage 3.31 Phase 1: Topic Intelligence Audit

**Date:** 2026-11-20 (IST)
**Mode:** Audit only. No production code, configuration, database record or running process was changed.
**Code revision audited:** `d41e912` on `main`; the live daemon (PID 6680) and dashboard (PID 9736) both run source revision `089ba03cc6a1558f`.

## Evidence labels

Every finding carries one of these labels.

- **[CODE]**: verified by reading the source; the file and function are named.
- **[DATA]**: verified with a read-only query of the live database (`backend/studio.db`, opened `mode=ro`) or a live data file.
- **[LOG]**: verified in `logs/scheduler_daemon.log`.
- **[REPLAY]**: verified by running an existing *pure* function offline on stored data. No network, no database writes; the script lives in the session scratchpad.
- **[UNVERIFIED]**: an external fact (limit, terms, cost) not confirmed from an authoritative source during this audit.
- **[RECOMMENDATION]**: an opinion, not a fact.

No live API calls were made. The audit used GNews data already cached by production at 01:28 IST.

---

## 0. Operating context discovered during the audit

**The live schedule is not FOUR_CYCLES_DAILY.** [DATA]
- `studio_settings['control_center.schedule.active']` is profile `CUSTOM`, revision `ccf0bd04f159`, with **24 cycles** at 00:00, 01:00, … 23:00 IST.
- Each cycle has a 45-minute recovery window.
- The profile was saved through the dashboard at 03:14 IST on 2026-11-20. Production was resumed by the operator at 03:15 IST.
- Earlier revisions of the same CUSTOM profile were saved at 23:13 and 01:26 IST. CUSTOM profiles have been active since at least 2026-11-18.

**This matters for topic intelligence.** At up to 24 productions a day, the system needs about 24 *new, fresh* topics a day. Section 11 shows that the current discovery pool cannot sustain that rate. This audit did not change the schedule.

---

## 1. Architecture: modules actually on the production path

The scheduler calls `master_orchestrator.execute_production_pipeline(job_id=job_id)` (`backend/app/scheduler/scheduler.py:534`). No `preferred_category` is passed, so category steering happens only inside discovery. [CODE]

| Stage | File | Class / function | Storage |
|---|---|---|---|
| Discovery | `backend/app/content/curiosity_topic_engine.py` | `CuriosityTopicEngine.discover_live_candidates` | none (in memory) |
| Research / enrichment | same | `enrich_wikipedia_candidate` (Wikipedia REST summary, media-list, MediaWiki extracts) | none |
| GNews supplement | `curiosity_topic_engine.py`, `backend/app/content/gnews_client.py` | `_gnews_candidates`, `GNewsClient.corroborated_events` | `backend/data/cache/gnews/*.json`, `usage.json` |
| Scoring | `curiosity_topic_engine.py` | `score_candidate` | weights in `backend/data/creative_policy_config.json` |
| Category policy | `backend/app/content/creative_production_policy.py` | `CreativeProductionPolicy.classify_topic`, `CategoryDistributionTracker` | `backend/data/category_distribution.json` |
| Duplicate / cooldown | `backend/app/content/topic_ledger.py`, `validation_topic_history.py` | `TopicLedger.check_content_collision`, `compute_semantic_similarity` | table `content_ledger`, `backend/data/validation_topic_history.json` |
| Ranking | `curiosity_topic_engine.py` | `discover_and_rank_live_topics` | `last_discovery_audit` (**memory only, never persisted**) |
| Identity gate | `backend/app/content/creative_identity_registry.py` | `CreativeIdentityRegistry.filter_fresh`, `reserve` | table `creative_identity_registry` |
| Selection and hook | `crazzydocs_master_orchestrator.py:486-590` | `ranked[0]`, `generate_five_hooks` | checkpoint in `persistent_jobs.checkpoint_data_json` |
| Research dossier | orchestrator `:628-647` | writes `research_<job>.json` | `media/productions/<job>/research/` |
| Script grounding | `backend/app/content/grounded_script_writer.py` | `write_grounded_script`, `validate_sections` | script in identity registry |
| Post-production record | orchestrator `:1532-1556` | `CategoryDistributionTracker.record_selection`, `TopicLedger.record_content` | `category_distribution.json`, `content_ledger` |

### Legacy modules not on the production path [CODE]

- `backend/app/content/topic_engine.py` (`TopicEngine`) and `duplicate_detector.py`: exported from `content/__init__.py`, but never instantiated by the orchestrator.
- `backend/app/content/fact_verifier.py` (`FactVerifier`): only `script_generator.py` uses it, and the orchestrator does not use `script_generator.py`.
  - Its claims are matched against a hard-coded table keyed on title words ("black hole", "moon", "solar", "baby"…).
  - The 27 `research_records` rows (10–11 Nov) come from this legacy path.
- `backend/app/agents/trend_researcher.py` and `topic_selector.py`: used only by the legacy `/api/pipeline/run` endpoint (`workers/pipeline.py`).
- Table `topics` has 0 rows; `video_performance` has 0 rows.

### Execution flow (as implemented)

```
scheduler.execute_production_pipeline(job_id)
 └─ CuriosityTopicEngine.discover_and_rank_live_topics(filter_duplicates=True)
     ├─ discover_live_candidates()
     │    1. Wikipedia full-text search: 3–4 category specs, 1 query each, srlimit=6
     │    2. NASA Images search: 1 static query, ≤3 items (for space/science/geo/env)
     │    3. Wikipedia featured feed for ONE day: tfa + mostread[:10] + onthisday[:3]
     │    → interleave → normalized-title dedupe → pre-dup ledger check → enrich_wikipedia_candidate (≥380 chars, ≥3 sentences)
     │    4. Phys.org RSS: 5 feeds × first 3 items (desc ≥160 chars, ≥3 sentences)
     │    5. GNews top-headlines: technology/science/business, ≥2-outlet clusters, ≤3 events
     │    → return (wiki + rss + gnews)[:18]
     ├─ score_candidate() for each (ledger collision + 8-dim score + penalties)
     └─ drop composite ≤ 0 and duplicates → sort by composite
 └─ CreativeIdentityRegistry.filter_fresh(ranked)   (exact normalized title, 30 days, any outcome)
     └─ empty → DuplicateCreativeIdentity (classified PERMANENT; the cycle fails)
 └─ selected = ranked[0] → generate_five_hooks → identity_registry.reserve → checkpoint
 └─ … script / media / render / QC / integrity gate …
 └─ CategoryDistributionTracker.record_selection + TopicLedger.record_content (PRODUCTION only)
```

---

## 2. Capability status

| # | Capability | Status | Evidence | Main limitation |
|---|---|---|---|---|
| 1 | Curiosity / surprise scoring | PARTIALLY_IMPLEMENTED | `enrich_wikipedia_candidate:676-692` sets curiosity 9.4/8.7 from a 17-word marker list and surprise 9.2/8.5 from "≥3 numbers". Phys.org is fixed at 8.9/8.8, GNews at 8.6/8.4. [CODE] | Two-level constants per source; "first", "built", "century" count as dramatic. Tests only check the dimension keys exist (`test_stage_3_21m…::test_eight_dimension_scoring…`). |
| 2 | Storytelling potential | PARTIALLY_IMPLEMENTED | 9.3 if ≥5 extract sentences, else 8.6 (`:689`); RSS fixed at 8.5. [CODE] | Measures article length, not narrative arc (conflict, turning point, outcome). |
| 3 | Visual potential | PARTIALLY_IMPLEMENTED | Count of image URLs or Commons filenames (`score_candidate:419-425`). [CODE] | Not checked against licence-verified acquisition, which only happens after selection. Nares Strait (cycle_2 01:00) failed during acquisition. [DATA] |
| 4 | Source credibility | PARTIALLY_IMPLEMENTED | Fixed per provider: Wikipedia 9.6, Phys.org 9.1, GNews 7.5; gate `factual_dim < 7.5 → 0`. [CODE] | No per-outlet or per-domain assessment on the live path. `FactVerifier.AUTHORITATIVE_DOMAINS` exists but is legacy. |
| 5 | Hook potential | PARTIALLY_IMPLEMENTED | `generate_five_hooks:1262-1306`: live topics get 5 templates with **constant scores**. [CODE] Recomputed: "Shocking Revelation" (first factual sentence) scores 9.54 and always wins; the next is 9.49. [REPLAY, arithmetic] | Hook choice ignores content. `hook_evaluator.py` rates scripts *after* writing, not candidate topics. The test checks only `len(hooks)==5`. |
| 6 | Audience relevance | PARTIALLY_IMPLEMENTED (effectively constant) | `emotional_connection`: Wikipedia 8.9, RSS 8.4, GNews 8.2, used as `audience_relevance`. [CODE] | No audience model, no performance data. |
| 7 | Semantic duplicate detection | PARTIALLY_IMPLEMENTED | `compute_semantic_similarity`: normalized/collapsed titles, stemmed token Jaccard, containment, `SequenceMatcher`, entity stems, hook Jaccard. Tested for headline swaps and shared-entity rewrites (`test_stage_3_21h…::test_requirement_b…`, `test_stage_3_21k…::…semantic_evasion`). [CODE] | Lexical only. Rewritten duplicates are missed and different events are merged; see §9. |
| 8 | Category diversity | PARTIALLY_IMPLEMENTED | 20 categories; soft recency penalty ≤0.45; under-represented categories preferred in queries (`discover_live_candidates:768-784`). [CODE] | The classifier mislabels: "History" is 13 of 42 ledger rows and includes supernova and dwarf-star stories. [DATA, REPLAY] See §11. |
| 9 | Historical repetition prevention | IMPLEMENTED_AND_TESTED | `content_ledger` collision (30-day cooldown plus a permanent similarity threshold) and `creative_identity_registry` (exact normalized title, 30 days, any outcome). Meaningful tests in `test_creative_identity_registry.py` (16 tests) and `test_stage_3_21h_content_freshness.py`. [CODE] | Works for exact and near-lexical repeats. "Science fiction" was selected 6 times on 12–13 Nov, before the registry existed (2026-11-13). [DATA] |
| 10 | Factual verification | PARTIALLY_IMPLEMENTED | `grounded_script_writer.validate_sections`: every number in the script must appear in the research corpus; no 9-word copied runs (tested in `test_grounded_script_writer.py`). [CODE] | Nothing verifies the *topic's* claims across independent sources. A single Wikipedia page or one Phys.org press release is the whole evidence base. |
| 11 | Topic exhaustion recovery | MISSING | When the identity gate removes every candidate, `DuplicateCreativeIdentity` is raised and classified PERMANENT (`test_creative_identity_registry::test_duplicate_identity_is_classified_permanent`). [CODE] | No query expansion, no second discovery pass, no reserve pool. Four cycles failed this way on 17 Nov. [DATA, LOG] |
| 12 | Multi-source aggregation | PARTIALLY_IMPLEMENTED | 5 source families combined (§4). [CODE] | GNews candidates are appended last and the list is cut at 18, so they are dropped whenever Wikipedia and RSS fill the pool (§5). RSS and GNews items are not deduplicated against Wikipedia items. |
| 13 | Discovery normalization | PARTIALLY_IMPLEMENTED | Every provider builds the same loose dict shape (id, title, category, summary, extract, bullets, sources, entities, images, timestamps, `research_fingerprint`). [CODE] | Untyped. Entities are regex capitals (`_extract_entities`), so "The", "In 1972" and similar can appear. No canonical subject ID (Wikipedia page ID / Wikidata QID). |
| 14 | Performance-based selection | MISSING | `video_performance` has 0 rows; the Analytics scope has not been granted (Stage 3.30). [DATA] | No feedback loop exists. |

---

## 3. Provider inventory (topic discovery and research)

| Provider | Implementation | Role | Method | Auth | Cost / limits | Live status (evidence) | Failure handling | Cross-provider dedupe | Persistence | Suitable for continuous production? |
|---|---|---|---|---|---|---|---|---|---|---|
| Wikipedia full-text search | `discover_live_candidates` §1 | Discovery | MediaWiki API `list=search` | none | Free. Wikimedia asks for a descriptive User-Agent and moderate request rates [UNVERIFIED: exact limits] | 0 "Wikipedia search discovery error" lines in the daemon log [LOG] | try/except per query; 10 s timeout; no retry | normalized-title key among Wikipedia proposals only | none | Yes, but the query rotation is static (§11) |
| Wikipedia featured feed (TFA, most-read, on-this-day) | same §3 | Discovery | REST `feed/featured/{date}` | none | Free | 0 failures logged [LOG]. Recent picks are dated 16 Nov (`wikipedia_tfa_1116`, `wikipedia_onthisday_1116`) [DATA] | try/except; 10 s timeout | as above | none | Yes. The feed's `news` ("In the news") section is fetched but **unused** [CODE] |
| Wikipedia REST summary + media-list + MediaWiki extracts | `enrich_wikipedia_candidate` | Research (dossier, facts, images) | REST + Action API | none | Free | Used by every Wikipedia candidate; all 4 jobs on 20 Nov have 2 Wikipedia sources [DATA] | silent `None` on any error | n/a | the dossier JSON per production | Yes |
| NASA Image and Video Library | §2 | Discovery (space) + media | REST search | none | Free [UNVERIFIED limits] | 0 failures logged [LOG] | try/except; 8 s timeout | n/a | none | Limited: the query index is static, so the same query runs every cycle (§11) |
| Phys.org RSS (5 feeds) | §4 | Discovery + the *only* research text for RSS topics | RSS | none | Free to read. Terms for reusing summaries [UNVERIFIED] | 1 timeout logged (17 Nov, biology feed) [LOG]. Supplied 21 of the 42 ledger topics on 16–18 Nov [DATA] | try/except; 8 s timeout | none | none | Usable for discovery; weak as sole evidence (a single press-release summary) |
| GNews | `gnews_client.py`, `_gnews_candidates` | Discovery (current events) | REST `top-headlines` | API key | Free plan 100 requests/day (code budget 90) [CODE]. Commercial-use terms of the free plan [UNVERIFIED] | Called 3 times on 2026-11-20 at 01:28 IST, **0 corroborated events** (§5) [DATA, REPLAY] | no-key / 401 / 403 / 429 / timeout categorized; 6 h cache; never raises | URL and title clustering within GNews only | cache + usage files | Not contributing at present (§5) |
| ScienceDaily RSS | `agents/trend_researcher.py` | Discovery | RSS | none | Free | **Legacy path only** [CODE] | — | — | — | Not in production |
| Internal curated pool (10 items) | `CURATED_CURIOSITY_CANDIDATES` | Discovery (test / non-live) | local list | none | — | Used only when `is_full_live` is false, never in production [CODE] | — | — | — | No; all 10 are already in the ledger or have been produced |
| `content_ledger`, `creative_identity_registry`, validation history | see §7 | History | SQLite / JSON | — | — | Active | — | — | yes | Yes |

**Visual acquisition providers are separate.** Wikimedia Commons, Openverse, NASA images, Internet Archive and Pixabay run after selection, in `backend/app/media/`. Their availability does **not** feed topic discovery. The exception is NASA search (above), and Wikipedia media-list filenames, which only *count toward* visual potential. [CODE]

---

## 4. Discovery yield (what can and cannot be measured)

- **Per-cycle candidate counts were never stored.** `last_discovery_audit` and `last_discovery_summary` exist only in memory, and nothing else in `backend/app` reads them. [CODE] So historical "retrieved → filtered → ranked → selected" funnels **cannot be reconstructed**. This audit does not invent them.
- **What the data does show:**
  - 49 "Selected topic" log lines. [LOG]
  - 46 "[CREATIVE IDENTITY] Skipping" lines; the most common was 'Science fiction' (22). [LOG]
  - The discovery source of each production, from its research dossier. [DATA]
- **Recent sources** (dossiers 20 Nov): `wikipedia_onthisday_1116`, `wikipedia_search:geography`, `wikipedia_tfa_1116`, `wikipedia_search:space_and_astronomy`. [DATA]
- **Ledger topics by family, 10–20 Nov:**
  - Phys.org RSS supplied about half of the 16–18 Nov topics, identifiable as headline-style titles. [DATA]
  - Wikipedia supplied the rest.
  - GNews supplied none.

---

## 5. GNews: detailed findings

| Question | Finding |
|---|---|
| Called in production? | Yes. `discover_live_candidates` step 5 calls `_gnews_candidates` on every live discovery (`curiosity_topic_engine.py:985`). [CODE] First production use: 2026-11-20 01:28 IST, cycle_2. [DATA: cache file mtimes] |
| Queries per cycle | 3 `top-headlines` requests (technology, science, business), `lang=en`, `max=10`. Static categories; no search queries. [CODE] With the 6 h cache, later cycles reuse those 3 responses (cycles at 02:00 and 03:00 made no new calls; `usage.json` = `{"2026-11-19": 3}`). [DATA] |
| Date, language and relevance filtering | Language via `lang=en`. Freshness: drops articles older than 3 days or dated more than 1 h in the future (`dedupe_and_cluster`). No relevance filter beyond the GNews category. [CODE] |
| Fields retained | title, description, content (cut at "["), url, source name, publishedAt, image. The original publication date is kept in `publication_timestamp` and in the sources list. [CODE] |
| Duplicate and syndication handling | Normalized-URL dedupe, then title-token Jaccard ≥0.5 clustering within GNews. Syndicated copies with the same title cluster together; rewritten headlines do not. [CODE] |
| Conversion to story candidates | ≥2 distinct outlets per cluster, ≥160 chars and ≥3 sentences of combined description, factual reliability 7.5, `verification="multi_source_corroborated_headline"`. [CODE] |
| Retrieved vs survived | **30 articles retrieved, 30 distinct clusters, 0 corroborated events, 0 candidates.** [REPLAY of `dedupe_and_cluster` on the 3 cached responses] |
| Selected | 0. [DATA] |
| Structural defect | Even a corroborated GNews candidate would usually be **cut off**: `return (enriched_candidates + rss_candidates + gnews_candidates)[: max_candidates + 6]` keeps 18. Wikipedia allows up to 12 and RSS up to 15, so GNews, appended last, is dropped whenever the first two fill 18. [CODE] |
| Failure, timeout, retry, rate limit | `fetch_json(retries=1, timeout=8.0)`; 401/403 → invalid_key; 429 → rate_limited; daily budget of 90 enforced locally. Failures are logged by category, never with the URL (which carries the key). A GNews problem never stops discovery (`test_gnews_failure_never_breaks_topic_discovery`). [CODE] 0 failures logged. [LOG] |
| Fallback | No explicit fallback is needed, because Wikipedia and Phys.org always run first. [CODE] |
| Quota tracking | Local counter only (`usage.json`, keyed by UTC date). GNews's own usage is not read. [CODE] |

**Verdict:** GNews is integrated and healthy, but it **does not contribute** to topic discovery. Two reasons:
1. Ten top headlines per category almost never contain the same story from two outlets.
2. Pool truncation would usually drop GNews candidates anyway.

Breaking news is also a poor match for a channel whose selected topics are curiosity and evergreen stories. [RECOMMENDATION]

---

## 6. Separation of discovery, research and media

- **Discovery:** Wikipedia search, Wikipedia featured feed, Phys.org RSS, NASA search, GNews.
- **Research / evidence:** Wikipedia (REST summary + extracts) for Wikipedia topics. For RSS and GNews topics, the **discovery text is reused as the evidence**: the Phys.org description, or the GNews descriptions. No independent evidence lookup happens. [CODE]
- **Visual acquisition:** Wikimedia Commons, Openverse, NASA, Internet Archive, Pixabay. Runs after selection (`acquire_isolated_topic_assets`).
- **Dual role:** Wikipedia (discovery + research + image hints) and NASA (discovery + media).
- **Defect:** discovery source and evidence source are the same for 2 of 5 families, so "factual reliability" for those topics is a property of the feed, not of the claims. [CODE]

---

## 7. Database and historical data (read-only)

| Measure | Value | Source |
|---|---|---|
| Discovered topics (all candidates ever seen) | **Not recorded.** | §4 |
| Selected topics | 62 rows in `creative_identity_registry` (2026-11-10 → 2026-11-20), including 11 historical rows with an empty category | [DATA] |
| Produced topics | 42 rows in `content_ledger`: 36 `produced`, 4 `archived`, 2 `approved` | [DATA] |
| Registry status mix | PRODUCED 32, SCRIPTED 7, SELECTED 2, HISTORICAL_* 21 | [DATA] |
| Uploaded | 33 `publishing_ledger` rows; 28 `YOUTUBE_UPLOAD_CONFIRMED` events | [DATA] |
| Rejected topics | Not recorded as rows. Identity rejections appear only as log lines (46). | [LOG] |
| Failed due to topics | 4 jobs (`cycle_15/16/17/20_20261117`) failed with `DuplicateCreativeIdentity` | [DATA] |
| Category distribution (ledger) | History 13, Unbelievable Real-Life 3, Everyday Science 3, Science 3, Technology 3, Space and Astronomy 3, Business 2, Nature 2, AI 2, 1 each for 8 others (including legacy names "Space & Astrophysics" and "untold_history") | [DATA] |
| Persisted scores | `composite_score` only in each research dossier JSON. The 8-dimension breakdown is not stored. | [DATA] |
| Provenance | Dossier `sources` (2 Wikipedia URLs or 1 Phys.org link), `discovery_source` | [DATA] |
| Timestamps | Selection = registry `created_at`; production = ledger `created_at`; candidate discovery time is not stored | [DATA] |
| Performance | `video_performance`: 0 rows | [DATA] |
| Validation history | 7 records, all VALIDATION, last on 2026-11-11 | [DATA] |
| Legacy | `research_records` 27 (10–11 Nov, legacy FactVerifier); `topics` 0 | [DATA] |

**Quality of selected topics** [DATA], a sample of what the system chose to make into curiosity Shorts:
- "Pakistan"
- "November 2015 Paris attacks"
- "Can't Let Go (Mariah Carey song)"
- "Rejection of evolution by religious groups"
- "Wei Jingsheng"
- "Mark Satin"
- "Researchers count kinetochore proteins in living cells using an improved…"

These passed every gate and scored about 8.9–9.3. The scores do not separate a story from an encyclopedia entry or a lab press release.

**Can published stories be rediscovered?**
- Exact same Wikipedia title: blocked by the registry for 30 days, and by the ledger permanently once a similarity threshold is met.
- The same story under a different title or a different Wikipedia article: **yes, it can be rediscovered** (§9).

---

## 8. Scoring and ranking

Current weights (`TopicScoringWeights`, overridable from `creative_policy_config.json`, which currently holds the defaults) [CODE, DATA]:

| Dimension | Current weight | How the value is produced | Proposed baseline (prompt) |
|---|---:|---|---:|
| curiosity | 0.18 | source constant ± keyword markers | Curiosity / surprise 20% |
| novelty (named "surprise" in candidates) | 0.14 | constant, reduced by historical similarity | (in curiosity / surprise) |
| storytelling | 0.15 | sentence count | Storytelling 20% |
| factual_reliability | 0.14 | provider constant | Factual reliability 15% |
| visual_availability | 0.14 | image-URL count | Visual potential 15% |
| audience_relevance | 0.09 | provider constant | Audience relevance 10% |
| short_form_suitability | 0.08 | extract length / bullet count | — |
| freshness | 0.08 | 10 × (1 − historical similarity) | — |
| hook potential | — | not scored at the topic level | Hook potential 15% |
| category diversity | — (subtractive penalty ≤0.45) | recency penalty | Category diversity 5% |

**Findings** [CODE unless marked]

1. **Little discrimination.** Most inputs are per-source constants, so composites cluster around 8.9–9.3. [DATA: dossier `composite_score` values 8.92–9.30 on 20 Nov]
2. **Double-counting history.** Similarity reduces `novelty`, `freshness` *and* the final score (`- sim_score*0.35`), on top of the duplicate gate.
3. **Arbitrary thresholds.** 380-char extract, ≥3 sentences, 160-char RSS description, factual gate < 7.5 (GNews sits exactly on it), +0.12 / −0.15 source-diversity adjustment, "≥9.15 can overcome recency" (mentioned in a comment but not implemented).
4. **Curated topics bypass every penalty** (`src_name == "curated"`); not relevant to production.
5. **Freshness is misnamed.** It measures distance from history, not recency of the event. `publication_timestamp` is stored but never scored.
6. **Bias toward Wikipedia.** It receives the highest constant reliability (9.6) and emotional connection (8.9), regardless of content.
7. **No topic-level hook potential.** No calibration data: no performance records.

---

## 9. Semantic duplicate detection

Mechanisms [CODE]:
- `normalize_topic_title` and `normalize_text` (lower-case, punctuation removal; "$1" becomes "1").
- A light suffix stemmer; adjacent-token bigram merge ("land mine" = "landmine").
- Order-invariant SHA-256 topic and research fingerprints.
- Signals: title word Jaccard, semantic-token Jaccard, containment (≥2 shared words and ≥65% of the shorter title), `SequenceMatcher` ≥0.78, entity-stem Jaccard, hook Jaccard.
- Thresholds:
  - 0.45 general;
  - 0.35 / entity Jaccard 0.30 during the 30-day cooldown;
  - entity Jaccard ≥0.40 at any age;
  - registry: exact normalized title.

Replay of `compute_semantic_similarity` (title vs title, no entities) [REPLAY]:

| Pair | Score | Correct outcome | Result |
|---|---:|---|---|
| "The Man Who Bought an Entire Bank for $1" vs "How a bank was sold for just one dollar" | 0.11 | duplicate | **missed** |
| same vs "The unbelievable story of a bank purchased for almost nothing" | 0.11 | duplicate | **missed** |
| "Tsutomu Yamaguchi: The Man Who Survived Both Atomic Bombs" vs "The engineer who lived through Hiroshima and Nagasaki" | 0.00 | duplicate | **missed** |
| "Apollo 12" vs "Apollo 13" | 0.89 | different events | **false duplicate** |
| "Rocky Mountains" vs "Rocky Mountain locust extinction" | 1.00 | different stories | **false duplicate** |
| "Apollo 12" vs "Lightning strikes Apollo 12 during launch" | 1.00 | same subject, different angle | duplicate (debatable) |
| "Cassini–Huygens" vs "Cassini's Grand Finale dive into Saturn" | 0.14 | same mission | not flagged |

The existing tests pass because their duplicate pairs share **identical entity lists**. Live candidates get regex-extracted entities that rarely match the ledger's. [CODE]

**Conclusion:**
- Exact and lexical rewrites are caught.
- Paraphrases with no shared words are not.
- Short titles with numbers or shared place names cause false rejections.
- There is no notion of an underlying event or subject identity, such as a Wikipedia page ID or Wikidata QID.

---

## 10. Factual verification and source reliability

| Check | Present? | Evidence |
|---|---|---|
| Independent verification of topic claims | No | Evidence = the discovery source itself (§6) [CODE] |
| Multiple sources required | Only for GNews (≥2 outlets), which currently yields nothing | [CODE] |
| Source credibility assessment | Per-provider constant only | [CODE] |
| Contradiction detection | No | — |
| Date / context validation | No | — |
| Unsupported claims rejected | At script level: numbers must appear in the research corpus; no invented statistics (`validate_sections`) | [CODE], tested |
| Uncertainty preserved | No field for disputed or uncertain claims | — |
| Sources passed to script generation | Yes: `sources` and `factual_bullets` go into the dossier and checkpoint | [CODE] |
| Sensational / misleading filter | A `blocked_terms` list checked against titles and descriptions (election, actor, singer…). It did not stop "Can't Let Go (Mariah Carey song)", "Mark Satin" or "Paris attacks" | [CODE, DATA] |

---

## 11. Topic diversity and exhaustion

**Category assignment defect.** `classify_topic` uses plain **substring** matching of category keywords (`if kw_l in body_l`), and ties go to the first registered category, which is History.
- "era" matches "several", "generally" and "operation"; "war" matches "award" and "toward".
- Replay on real dossiers [REPLAY]:
  - Mark Satin: History by 2 keyword hits ("war", "era").
  - Aucanquilcha (a volcano): History from a single "era" hit, tied with Nature and Architecture.
  - DESI Milky Way: **0 hits in every category**, yet it is recorded as "Everyday Science" through the neutral fallback.
- The ledger's 13 "History" topics include "This supernova is 50 million light-years away…" and "Astronomers track a flaring dwarf-star duo". [DATA]
- **Effect:** the recency penalty and category rotation work on wrong labels.

**Static query rotation.** Queries rotate with `history_offset = len(validation_history.load_records())`.
- In production, `record_selection` is skipped (orchestrator `:538`), so the offset stays at **7** forever. [CODE, DATA]
- As a result, every production cycle uses:
  - the same Wikipedia search query per category;
  - the same NASA query ("black hole accretion", 7 % 4 = 3);
  - the same featured-feed day offset (3 days ago).
- Diversity comes only from the category order and from the feeds' own daily changes.

**Exhaustion is observed, not theoretical** [DATA, LOG]:
- On 2026-11-17, cycles 15, 16, 17 and 20 failed. At 14:00 IST the ranked pool held **one** candidate ('Strait of Sicily'), already reserved by the failed cycle_12, so the gate rejected it and the job ended as PERMANENT.
- 'Science fiction' was rejected 22 times, 'Why aging stars suffer a sudden luminosity dip' 7 times, 'Drake Passage' 5 times. The pool regenerates the same few subjects.
- A failed or SCRIPTED production is not in `content_ledger`, so the early `pre_dup` check cannot skip it. It reaches the identity gate after ranking and consumes a pool slot.

**Missing:** no exhaustion detection, query expansion, deeper pagination, second discovery pass or reserve pool. Starvation risk scales with the 24-cycle schedule (§0).

---

## 12. Test coverage

| Area | Tests (meaningful assertions?) | Gap |
|---|---|---|
| Multi-source discovery | `test_stage_3_21k…::test_consecutive_live_discovery_selects_distinct_topics_and_fails_closed` (mocked HTTP) | No test of pool composition or truncation; the GNews-truncation defect is untested |
| Provider failure / GNews fallback | `test_pixabay_gnews_330.py` (5 GNews tests, real assertions); `test_stage_3_21m…::test_discovery_resilience_empty_pool…` | Adequate for error handling |
| Normalization | indirect | No schema test |
| Scoring | `test_eight_dimension_scoring…` checks the keys exist plus one ordering | No calibration or consistency test |
| Category diversity | `test_dynamic_category_registration_and_classification` (keyword in title) | No test with body-text substrings, so the classifier defect is untested |
| Semantic duplicates | `test_requirement_b…`, `test_stage_3_21k…semantic_evasion`, `test_content_intelligence::test_near_duplicate_detection` (legacy detector) | Only shared-entity paraphrases; no negative cases (Apollo 12/13) |
| Historical repetition | `test_creative_identity_registry.py` (strong) | — |
| Source reliability / factual verification | `test_grounded_script_writer.py` (script level); `test_content_intelligence` tests the **legacy** FactVerifier | Nothing at the topic level |
| Exhaustion / recovery | Only "fails closed" | No test of recovery |
| Production handoff | `test_creative_identity_registry::test_orchestrator_filters_and_reserves_before_any_render` | — |
| Scheduler integration | duplicate classified PERMANENT | — |
| Hooks | `len(hooks)==5` | No quality test |

---

## 13. Verified defects (ranked)

1. **Topic starvation** (§11): a static query rotation, a small pool (≤18) and exact-title identity rejections led to 4 PERMANENT cycle failures on 17 Nov. The risk grows with 24 cycles a day.
2. **Category classifier substring bug** (§11): mislabels about a third of topics and skews diversity control.
3. **Scores do not separate stories from encyclopedia entries** (§7, §8): constants dominate, so "Pakistan" and a pop song scored like strong stories.
4. **Semantic dedupe is lexical only** (§9): misses paraphrases and falsely rejects sequels or shared place names.
5. **GNews contributes nothing** (§5): no corroborated clusters, and the pool is truncated before GNews items.
6. **No topic-level fact verification**; discovery text doubles as evidence for RSS and GNews topics (§6, §10).
7. **Discovery funnel not persisted** (§4): rejected candidates, scores and provider yields are lost, so tuning cannot be evidence-based.
8. **Hook choice is fixed by template constants** (§2 #5).
9. **Static ingredients:** the featured feed uses a fixed 3-day-old date; the "In the news" section is ignored (§3).

## 14. Limitations of this audit

- No live API calls, so provider limits and terms marked [UNVERIFIED] need confirmation before Phase 2.
- Candidate-level metrics before 20 Nov cannot be reconstructed (§4).
- The production schedule's change history shows dashboard edits; this audit did not determine who made each one.
