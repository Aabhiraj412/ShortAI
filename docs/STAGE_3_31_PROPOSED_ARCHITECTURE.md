# Stage 3.31: Proposed Topic Intelligence Architecture

**Status:** PROPOSAL. Nothing in this document has been implemented. It is based on the findings in `STAGE_3_31_TOPIC_INTELLIGENCE_AUDIT.md`; references such as "audit §11" point there.

**Labels**
- **EXISTING** marks components that are already present.
- **NEW** marks proposed components.
- **CHANGE** marks proposed modifications to existing components.

## 1. Design goals

1. Never fail a production cycle because the pool ran dry while fresh, eligible subjects exist (audit §11).
2. Rank *stories*, not encyclopedia entries or press releases (audit §7, §8).
3. Identify the same subject or event across titles, providers and time (audit §9).
4. Treat discovery text as a lead, never as proof (audit §6, §10).
5. Persist every decision so tuning is based on evidence (audit §4).

### Hard boundaries

- Discovery runs *before* and *independently of* rendering and publishing.
- It is called from the same place in the orchestrator, keeps the same `ScoredTopic` contract and adds no worker, cycle or process.
- Concurrency, the schedule, recovery windows, the renderer and the Pop Info engine are untouched.

## 2. Target pipeline

```
                 ┌──────────── Topic Reservoir (NEW, SQLite) ────────────┐
 Discovery       │ candidates with status: NEW → ENRICHED → SCORED →      │
 adapters  ───►  │ ELIGIBLE / REJECTED(reason) → RESERVED → USED          │
 (scheduled      └───────────────────────────────────────────────────────┘
  refill job,                │                          ▲
  off the                    ▼                          │ outcome (produced,
  production path)   Production cycle:                  │ failed, published,
                     select_topic(job_id) ──► top ELIGIBLE, re-checked
                                              against history → RESERVED
```

- **NEW: Reservoir.** Discovery stops running inline inside each cycle. A light refill job keeps a pool of pre-scored eligible topics.
  - Target: at least 72 eligible topics, about 3 days at 24 cycles a day.
  - The job tops the pool up when it drops below a low-water mark.
  - It runs on the existing APScheduler, as a non-production job like the 3.30 junk-cleaner dry-run.
  - It never runs while a render is active, and makes no more than a handful of HTTP calls per run.
- **Selection at cycle time** is a local database read plus a final freshness re-check against history, with no network calls.
  - This removes discovery latency and provider outages from the production path.
- **Fallback.** If the reservoir is empty, the cycle falls back to today's inline `discover_and_rank_live_topics` (EXISTING), so behaviour can never get worse than today.

## 3. Discovery, research and verification separation

| Layer | Purpose | Providers | Output |
|---|---|---|---|
| Discovery (CHANGE) | Find candidate *subjects* | Wikipedia search (with paging and rotating queries), featured feed (TFA, most-read, on-this-day **plus the unused "In the news" section**), Phys.org RSS, NASA search, GNews (optional) | `RawCandidate` |
| Resolution (NEW) | Map each candidate to a canonical subject | Wikipedia page lookup with redirects, the article's Wikidata QID (free, no key) | `subject_key` = QID or page ID |
| Research (EXISTING, extended) | Build the dossier | Wikipedia extract (EXISTING). For RSS and GNews leads, look up the matching Wikipedia or primary article (NEW) | `Dossier` |
| Verification (NEW) | Check the *claims the hook relies on* | Second, independent source (a Wikipedia article plus the original publisher or a press release). Optional Wikidata for dates, places and quantities | `VerificationReport` |
| Media (EXISTING, untouched) | Visual acquisition after selection | Commons, Openverse, NASA, Internet Archive, Pixabay | — |
| Visual pre-check (NEW, cheap) | Estimate usable visuals *before* selection | Commons category and file count for the subject (metadata only, no downloads) | `visual_estimate` |

**Rule:** a candidate whose only evidence is its own discovery text, for example a lone Phys.org blurb, cannot reach `ELIGIBLE`.

## 4. Provider orchestration

- Each provider becomes a small adapter with the same interface.
  - Method: `fetch(budget, cursor) -> List[RawCandidate]`.
  - Shared plumbing (EXISTING `media/net.py`): `cached_fetch_json` / `fetch_json` with timeouts, one retry and error categories.
- **NEW: rotation state persisted per adapter.** It records the query index, page offset and feed date, and replaces the frozen `history_offset` (audit §11).
- **Budgets:**
  - per adapter per refill;
  - GNews keeps its 90/day local budget and 6 h cache;
  - Wikipedia stays polite: descriptive User-Agent, serial requests, small page sizes.
- **Health:** each refill writes per-adapter yield and errors to the reservoir. The existing provider-health card (3.30) can read it.
- **Expansion ladder (NEW)**, used when the eligible count falls below the low-water mark:
  1. next page of the same queries;
  2. next query in each category;
  3. additional featured-feed dates (1–14 days back);
  4. related-article links of recently *successful* subjects;
  5. widen to all 20 categories.
- Each step is bounded, and the ladder stops when the target is reached.

## 5. Candidate normalization (NEW typed schema)

`TopicCandidate` (a dataclass; it serializes to a reservoir row):

```
candidate_id, provider, provider_item_id, discovered_at,
title, subject_key (QID | wiki:pageid | url-hash), subject_label,
event_year, event_date (optional), publication_ts (for news),
summary, extract, factual_bullets[], sources[{url, publisher, type: discovery|evidence, retrieved_at}],
entities[{label, qid?}], category_key, category_confidence,
image_hints[], visual_estimate, research_fingerprint,
scores{...}, score_version, status, rejection_reason
```

Fields are filled the same way regardless of provider. `ScoredTopic` (EXISTING) is built from it at selection time, so orchestrator code stays unchanged.

## 6. Scoring model (CHANGE)

Two-step model: **hard gates**, then a **weighted score**.

**Gates.** Fail any one and the candidate is `REJECTED` with a reason:
- the subject is not a story: lists, countries, people with no notable event, songs or albums, election or politics biographies (a stronger blocklist plus Wikidata "instance of" when available);
- verification is not `VERIFIED`;
- the subject is in cooldown (§7);
- the visual estimate is below the minimum for a 45–55 s Short.

**Weighted score.** The prompt's proposed baseline is used as the starting point:

| Dimension | Weight | Signal (replaces constants) |
|---|---:|---|
| Curiosity / surprise | 20% | Counter-intuitive markers in the *hook sentence*: superlatives, numbers with units, contrast words ("but", "despite", "only", "never"), plus a model rating (below) |
| Storytelling potential | 20% | Presence of actor + event + turning point + outcome in the extract (sentence-role heuristics plus a model rating) |
| Visual potential | 15% | `visual_estimate` (Commons file count, Wikipedia media-list), not raw URL count |
| Factual reliability | 15% | Verification result: independent sources, contradictions, date check |
| Hook potential | 15% | Best of the candidate hooks scored by the EXISTING `hook_evaluator` rules, applied *before* selection |
| Audience relevance | 10% | Category baseline; later, performance priors from Analytics (§9) |
| Category diversity | 5% | Under-representation of the (fixed) category over the last N productions |

**Model rating (optional, cost-bounded).**
- One Gemini call per *refill batch*, not per cycle, through the EXISTING integration.
- It returns JSON ratings for curiosity, storytelling and hook potential, with a one-line reason per candidate.
- Its output is cached on the candidate.
- If the model is unavailable, the heuristic scores are used and the row is marked `score_source=heuristic`.

**Score versioning.** Every row stores `score_version` and the dimension breakdown, so weights can be recalibrated offline against outcomes.

**Category fix (CHANGE to `classify_topic`):**
- match whole words only, with title weighting;
- record a confidence value and do not use History as the tie-break default;
- leave the category unassigned rather than guess when confidence is low;
- prefer the discovery hint (for example `wikipedia_search:<category>`).

## 7. Semantic deduplication architecture (CHANGE)

Four layers, cheapest first:

1. **Subject identity (NEW):** same `subject_key` (QID or page ID, resolved through redirects) means the same subject. This catches "Mary Celeste" versus "The ghost ship found with no one aboard" when both resolve to the same article.
2. **Event signature (NEW):** (subject_key or main-entity QIDs, event year, key quantity). This separates Apollo 12 from Apollo 13 (different QIDs) and two different events about the same person (same person, different year or event).
3. **Lexical (EXISTING):** `compute_semantic_similarity`, kept as is. It works well for headline rewrites with shared words.
4. **Embedding similarity (NEW, optional):**
   - Gemini text embeddings of title and summary, computed once per candidate at refill and cached in the reservoir.
   - Cosine thresholds are set from the validation dataset.
   - With no key or quota, this layer is skipped.
   - No local model is needed, which respects the RAM limits.

**Decision table:**

| Relation | Rule | Action |
|---|---|---|
| Exact duplicate | same normalized title or same `subject_key` within cooldown | reject |
| Rewritten duplicate | same event signature, or embedding ≥ high threshold | reject |
| Related story (same subject, different event) | same subject, different event signature | allow after 90 days; penalize within |
| Same person, different event | shared person QID, different event year | allow; small penalty |
| Unrelated, shared entity (for example "Rocky Mountains" vs "Rocky Mountain locust") | different `subject_key`, low embedding similarity | allow |

History compared: `content_ledger`, `creative_identity_registry` (all outcomes) and reservoir rows marked USED or RESERVED.

## 8. Factual verification (NEW)

- Extract the 3–5 claims the hook and script will rely on: dates, numbers, superlatives and causal statements.
- Verify each claim against a second independent source:
  - for Wikipedia topics, the cited primary source or a second article;
  - for news or RSS leads, the Wikipedia article or the publisher's original page.
- Optional Wikidata checks: dates, coordinates, quantities.
- Mark each claim `supported`, `contradicted` or `unverified`. Any contradicted claim, or any unsupported hook claim, makes the topic ineligible.
- Keep uncertainty: claims flagged "disputed" or "estimated" are passed to the script writer with that qualifier.
- `grounded_script_writer.validate_sections` (EXISTING) stays as the final script-level check.
- Store `VerificationReport` (claims, sources, statuses) on the reservoir row and in the research dossier.

## 9. Historical intelligence (NEW)

- Persist every candidate and rejection reason (fixes audit §4).
- Link each selection to its production outcome (produced, failed, uploaded, published) and, once Analytics is authorized, to its metrics (`video_performance`, EXISTING table and collector from 3.30).
- **Performance priors.** After enough published videos (at least 30 with 7-day metrics), estimate per-category and per-hook-type priors and feed them into "audience relevance".
  - Missing metrics are never treated as zero (consistent with 3.30).
  - Priors are shown on the dashboard before they influence ranking.

## 10. Topic diversity

- A category is assigned from a fixed classifier with a confidence value. The diversity score uses the last 24 *productions* (one day at the current schedule).
- Soft quotas: no category above 25% of a rolling 24-production window unless the eligible pool has no alternative.
- Source diversity: at most 2 consecutive topics from the same provider family.
- Subject-type diversity (person, event, place, phenomenon, object), taken from Wikidata "instance of" when available.

## 11. Exhaustion recovery

- **Detection:** `eligible_count` < low-water mark (24) triggers the refill expansion ladder (§4). At a critical level (< 4), the dashboard shows an alert.
- **At selection time:** if every reserved candidate is rejected, the system fetches the next eligible one without any network call, instead of failing PERMANENT.
  - DuplicateCreativeIdentity stays PERMANENT only when the reservoir is truly empty and the inline fallback also returns nothing.
- **Duplicate safety:** freshness is re-checked at selection against all histories in the same transaction that sets `RESERVED`. A unique index on `(subject_key, status=RESERVED|USED)` prevents two jobs from taking the same subject.

## 12. Data model (NEW tables in `backend/studio.db`; additive only)

```sql
CREATE TABLE topic_candidates (
  candidate_id TEXT PRIMARY KEY, provider TEXT, provider_item_id TEXT,
  subject_key TEXT, title TEXT, normalized_title TEXT, category_key TEXT,
  category_confidence REAL, event_year INTEGER, publication_ts TEXT,
  dossier_json TEXT, sources_json TEXT, entities_json TEXT,
  visual_estimate REAL, scores_json TEXT, score_version TEXT,
  embedding BLOB, verification_json TEXT,
  status TEXT, rejection_reason TEXT,
  discovered_at TEXT, updated_at TEXT, reserved_by_job TEXT, used_at TEXT);
CREATE INDEX ix_tc_status ON topic_candidates(status);
CREATE INDEX ix_tc_subject ON topic_candidates(subject_key);
CREATE TABLE discovery_runs (
  run_id TEXT PRIMARY KEY, started_at TEXT, finished_at TEXT, trigger TEXT,
  provider_stats_json TEXT, expansion_steps_json TEXT, eligible_after INTEGER);
CREATE TABLE provider_rotation_state (provider TEXT PRIMARY KEY, state_json TEXT, updated_at TEXT);
```

- No existing table is altered or deleted.
- `content_ledger`, `creative_identity_registry` and `validation_topic_history.json` remain the authorities for history.
- The new tables reference them only.

## 13. Performance and cost

- **CPU / RAM:** heuristics, SQLite and small JSON. No local ML model. Embeddings, if enabled, are remote and cached.
- **Network per refill (typical):**
  - Wikipedia: about 10–30 requests;
  - RSS: 5;
  - NASA: 1;
  - GNews: 0–3 (cached 6 h);
  - Gemini: 1–2 calls per batch.
- At 24 cycles a day this is *lower* than today's inline discovery, which makes about 30–60 Wikipedia requests per cycle.
- **Cost:** no new paid service. Gemini and GNews usage stays within existing free or current quotas. Refills are spread through the day and skipped during renders.

## 14. Production integration boundaries

| Boundary | Rule |
|---|---|
| Orchestrator | Only the topic-selection block (`crazzydocs_master_orchestrator.py:486-552`) changes, from `discover_and_rank_live_topics()` to `topic_service.select(job_id)` with the inline fallback. The rest of the pipeline receives the same `ScoredTopic`. |
| Scheduler | One new background job, `topic_reservoir_refill_job`, outside the production cycle list. No change to cycles, concurrency, recovery windows or the active schedule profile. |
| Identity registry | Unchanged contract. It is still reserved after the hook is chosen. |
| Rendering / Pop Info / publishing | Not touched. |
| Rollout | Behind a feature flag in `creative_policy_config.json` (`topic_reservoir.enabled`, default `false`). With the flag off, behaviour is identical to today. |
