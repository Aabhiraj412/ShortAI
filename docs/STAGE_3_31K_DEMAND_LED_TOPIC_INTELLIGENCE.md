# Stage 3.31K: demand-led topic intelligence

**Roadmap stage:** 3.31, Advanced Topic Intelligence. It builds on 3.31A–D (candidate ledger, category classifier v2, query rotation, GNews and Wikipedia "In the news", exhaustion recovery); none of those was rebuilt.

## 1. What was missing

Before this stage, discovery was **supply-led**: every candidate came from category search queries, featured feeds, phys.org RSS, NASA and GNews. Nothing measured what people are actually looking for. 3.31f (signal-based scoring) was planned but never built. Several broad interests the channel should cover (film and TV, games, internet culture, sport) were classified out of scope.

## 2. What was added (all behind flags; default off)

| Flag | Modes | Module | Effect |
|---|---|---|---|
| `demand_signals` | off / shadow / on | `content/demand_signals.py` | Collects public-interest signals with provenance and resolves the strongest to verified Wikipedia articles. **Shadow:** collected and recorded only. **On:** up to 6 demand candidates, with 4 reserved pool slots |
| `scoring_v2` | off / shadow / on | `content/topic_scoring_v2.py` | Eight-dimension score. **Shadow:** recorded beside the v1 score. **On:** `0.5 × v1 + 0.5 × v2` (a v1 rejection stays a rejection) |
| `expanded_categories` | off / on | `content/expanded_categories.py` | Adds *Cinema & Entertainment*, *Media & Internet Culture* and *Sports & Major Events*. Classifier v2 then maps film/TV/music/game and sport subjects to them. **Politics always stays out of scope** |

Flags live under `topic_intelligence` in `backend/data/creative_policy_config.json` and are read at daemon start.

### Demand sources

Free, keyless, public; no scraping of pages that forbid it.

| Source | Signal | Covers |
|---|---|---|
| Wikimedia Pageviews API, most-read English articles (previous UTC day) | daily views; **trend velocity** = mean views of the last 2 days ÷ the 7 days before (12 lookups per run) | public interest, popular questions, emerging and seasonal interest |
| Google Trends daily trending searches RSS (US, IN, GB) | approximate search traffic plus the news items behind each trend | trending searches, current affairs, breaking developments |

**Safety, applied twice:**
- **To the term:** non-article pages, list/meta pages ("Deaths in 2026", "2026 in film"), adult content, death and crime, rumour/leak/scandal, politics, betting, share prices, live fixtures, and non-English terms are dropped.
- **To the resolved article's opening:** person biographies (privacy and gossip risk) and crime or death subjects are skipped.

A search term never becomes a topic by itself. It must resolve to a Wikipedia article, which then goes through the normal enrichment, factual gates, duplicate and cooldown checks, and the category classifier.

**Provenance:** every signal is persisted in `topic_demand_signals` (source, region, metric, value, rank, velocity, URL and news items, fetch time, run id). Each demand candidate carries `demand` (index, velocity, sources, term, news) into the candidate ledger and the score breakdown.

**Bounds:**
- 1 top-list request, at most 12 velocity requests, 3 RSS requests, up to 12 title resolutions and 6 enrichments per run;
- 0.5 s gap between enrichments (bursts were rate-limited by Wikipedia in testing);
- one failing provider never stops the others.

### Scoring v2

| Dimension | Weight | Measured from | When unmeasured |
|---|---|---|---|
| demand | 0.20 | demand index (log-scaled views/searches, multi-source bonus, velocity boost) | 0.25 ("no signal") |
| trend_velocity | 0.12 | pageview velocity | 0.4 |
| novelty | 0.15 | 1 − highest similarity to produced and validated topics | v1 novelty |
| freshness | 0.10 | news/feed publication date (half-life 7 days) | 0.5 (evergreen). A Wikipedia timestamp is an *edit*, not news, so it is never used |
| curiosity | 0.15 | v1 curiosity + short-form suitability | — |
| source_credibility | 0.12 | v1 factual reliability + independent source count | — |
| production_feasibility | 0.11 | v1 visual availability, image count, research depth | — |
| historical_performance | **0.05** | category views relative to the channel median (3.30 `video_performance`, at least 3 videos) | **0.5 neutral** |

Historical performance is **one bounded signal**. It can move a topic by at most 0.5 of 10, and it is neutral today because `video_performance` has **0 rows** (no analytics collected yet).

## 3. Live validation (2026-11-21, read-only, no database writes)

- **Signals:** 75–81 signals from both sources, no provider errors.
- **Candidates:** the run produced 4 verified demand candidates: *Grand Theft Auto VI* (velocity 7.1×), *The Hunger Games: Sunrise on the Reaping* (classified *Cinema & Entertainment* with expanded categories), *Rockstar Games* and *Anthropic*.
- **Skipped:** a living-person biography and one sensitive subject. Several person and crime pages were dropped by the term filters.
- **Scores:** v2 8.7–8.9 against v1 9.2–9.4 (blended 8.3–9.1); demand and velocity are measured, historical performance is neutral.

## 4. Activation (each step needs the operator's approval)

1. `demand_signals: shadow` and `scoring_v2: shadow` for at least 48 h: signals recorded, v2 recorded, selection unchanged. **Enabled at this deployment (see the handover for the time).**
2. Review the dashboard (Ops → *Topic intelligence*) and the ledger.
3. `expanded_categories: on`: widens scope. It changes which subjects are eligible, so it is not enabled automatically.
4. `demand_signals: on`, then `scoring_v2: on`.

## 5. Tests

`backend/tests/test_topic_demand_331k.py`: 30 cases (filters, collector, provenance store, ranking, v2 dimensions and bounds, analytics provider, expanded scope, flags, engine integration in off, shadow and on).
