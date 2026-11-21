# Stage 3.35b: topic discovery failures caused by Wikimedia rate limiting

## 1. Symptom

On 2026-11-21, seven cycles failed at topic discovery with `DuplicateCreativeIdentity`: 12:00, 13:00, 16:00, 17:00 (first attempt), 18:00, 19:00 and 22:00 IST. The message read "every discovered topic was already selected by an earlier production (1–2 rejected)". Discovery had about 53 candidates, but the eligible pool was down to 0 or 1, and the few survivors had already been produced.

## 2. Root cause (confirmed)

1. **Request volume.** Each discovery run sends Wikimedia about 3 requests per candidate (summary, media list, extract), plus search and feed requests. Since 3.34, `demand_signals: shadow` adds about 75 pageview requests. All of it was sent unpaced, roughly 200 requests in about a minute.
2. **Rate limit.** Wikimedia answers HTTP **429** with Retry-After of about 35 s once a client without an account sends roughly 18 requests in a burst. Measured on 2026-11-21: 20 quick summary requests gave 18 OK and 2 at 429.
3. **Misclassification.** `_http_get_json` raised on the 429. Enrichment's catch-all `except` returned `None`, and the caller recorded the candidate as `INSUFFICIENT_EVIDENCE` ("no usable encyclopedic extract"). Articles such as *Indian Ocean* and *Quaternary glaciation* were rejected this way.
4. **Pool drain.** About 30–38 of roughly 50 candidates per run were lost this way, every run since at least 2026-11-20. Query rotation is off, so each run saw the same Wikipedia search pages. The eligible pool shrank from 3–9 to 0–1, and the integrity gate then refused the already-produced survivors. That refusal was correct.

**Replay proof:** re-enriching cycle 19's "insufficient evidence" titles produced 18 × HTTP 429 out of 35 requests, and only 4 of 19 titles enriched.

## 3. Fix (code only; no flags changed)

| Change | Where |
|---|---|
| `WikimediaGate`: every request to Wikimedia hosts is paced at one per second (process-wide, thread-safe). HTTP 429/503 waits for Retry-After (default 10 s, capped at 40 s) and retries at most 2 times. Retry waiting is capped at 180 s per discovery run, reset at `begin_run`. | `curiosity_topic_engine.py` |
| A throttled candidate is recorded as `PROVIDER_FAILURE` ("Wikimedia rate limit … not judged"), never as `INSUFFICIENT_EVIDENCE`. A throttled media list is not scored on partial data. | `enrich_wikipedia_candidate`, `_reject_unenriched` |
| The media-list request now runs after the extract-length checks, so stub articles cost one request fewer. Scoring is unchanged. | `enrich_wikipedia_candidate` |
| Every discovery run records its Wikimedia request statistics (ok, 429, retried, gave up, seconds waited) in the run trace and the discovery summary, and logs a warning when throttled. | `_note_wikimedia_requests` |

Non-Wikimedia hosts (phys.org, NASA, GNews) are unaffected.

## 4. Verification

**Live dry runs.** Real Wikimedia traffic, temporary copy of the database, production flags:

| Pacing | Discovery time | HTTP 429 | Gave up | Eligible topics |
|---|---|---|---|---|
| none (production before the fix) | about 70 s | many (replay: 18 of 35) | — | 0–1 |
| 0.4 s | 267 s | 16 | 9 (all shadow demand lookups) | 12 |
| **1.0 s (deployed)** | **154 s** | **3, all retried successfully** | **0** | **12** |

Genuine evidence rejections in enrichment fell from 30–38 to about 6 (NASA image titles, feed stubs). Discovery still finishes far inside the 45-minute recovery window.

**Tests:** `test_wikimedia_rate_limit_335b.py`, 19 tests covering pacing, Retry-After, the cap and default, bounded retries, the wait budget, no retry for other HTTP errors, host matching, gate routing, the not-judged path, no media request for stubs, a throttled media list, the rejection reason, and the budget reset.

## 5. Decisions left to the operator (not changed)

- `demand_signals: shadow` still adds about 75 Wikimedia requests per run. Turning it off, or on, is a topic-flag decision.
- `production_query_rotation: off` means each run searches the same Wikipedia pages. Turning it on would widen the pool.
- **User-Agent.** Wikimedia's robot policy asks that the User-Agent include contact information (a URL or email). Adding one may earn more lenient treatment, but it is the operator's information to publish.
