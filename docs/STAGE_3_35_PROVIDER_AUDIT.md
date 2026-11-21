# Stage 3.35: content provider audit

**Sources of evidence:**
1. Request telemetry from the existing provider-health ledger (`media/asset_cache/provider_health.json`, written by every scene photo selection: per-provider OK/failed counts, latency, last error kind). This is "configured / reachable".
2. **Rendered usage:** the rendered scene plans of the last 20 to 30 completed videos. This is "used in production". A configured key or a successful request is not counted as usage.

## 1. Provider table (measured 2026-11-21)

| Provider | Configured | Reachable (requests OK/failed) | Integrated | Used in production (rendered, last 30 videos) | Success rate | Stage 3.35 state |
|---|---|---|---|---|---|---|
| Pixabay (images) | yes (key) | 73 / 0 | yes (scene photo search) | **0 images** | 100 % | **NOT_USED**: requests succeed, but no image has ever passed scene relevance into a final video |
| Pixabay Videos | yes (same key) | new in 3.35 | yes (scene footage) | starts with 3.35 | — | CONFIGURED |
| Pexels (images, videos) | **no key** | — | yes (key-gated) | 0 | — | **KEY_MISSING** (optional, never required) |
| GNews | yes (key) | 6 / 2 (HTTP 429) | yes (topic discovery, optional) | discovery only | 75 % | **RATE_LIMITED** |
| Openverse | no key needed | 74 / 5 (timeouts) | yes | 4 photos | 94 % | **DEGRADED** (intermittent timeouts) |
| Wikimedia Commons | no key needed | 93 / 0 | yes | **21 photos** | 100 % | HEALTHY |
| Wikimedia Commons Video | no key needed | new in 3.35 | yes (scene footage) | starts with 3.35 | — | CONFIGURED |
| Internet Archive | no key needed | 75 / 0 | yes | 0 | 100 % | NOT_USED |
| NASA Image Library | no key needed | 13 / 0 | yes (space subjects only) | 0 | 100 % | NOT_USED (relevant to few topics) |
| NASA Video | no key needed | new in 3.35 | yes (space subjects only) | starts with 3.35 | — | CONFIGURED |
| Microsoft Edge neural TTS | no key needed | 691 segment requests, 5 retries, 1 final failure | yes (only voice) | every video | 99.86 % after retries | HEALTHY |
| Wikipedia REST | no key needed | discovery | yes | every topic | — | HEALTHY |
| Phys.org RSS | no key needed | discovery | yes | some topics | — | HEALTHY |

Usage figures are confirmed from rendered plans. Request counts are cumulative counters from the provider-health ledger (they describe reachability, not usage).

## 2. Why Pixabay contributed nothing

Scene photo selection (3.28+) requires caption or description evidence that the image shows *this scene* (`evidence_beyond_filename`). Pixabay results carry short tag lists and no description, so they almost always fail that check. Across the last 30 videos the top rejection reasons were "not about this scene" 2589, "licence not approved" 618 and "no description/tag evidence" 238. The strictness is deliberate (no irrelevant photos) and was **not** relaxed. Pixabay's value is its **video** library, now used by the footage selector, which judges stock clips on tags against the subject and the scene's own words.

## 3. Changes in 3.35

- **Stage 3.35 provider states:** `CONFIGURED`, `HEALTHY`, `DEGRADED`, `RATE_LIMITED`, `AUTHENTICATION_REQUIRED`, `KEY_MISSING`, `DISABLED`, `NOT_USED`, `UNKNOWN`. Computed beside the existing statuses; `NOT_USED` = reachable but no asset in the last 20 rendered videos.
- **Rendered usage per provider, split into photographs and footage:** `rendered_usage_last_20` in `/api/providers/health`, and the Ops dashboard card.
- **Footage providers:** Pixabay Videos, Wikimedia Commons Video and NASA Video, plus Pexels Videos when a key is added. All go through `media/net.fetch`, so their requests, failures and latencies land in the same provider-health telemetry under their own names.
- **Fallback:** providers are searched in parallel. A provider failing 3 times in a row is skipped for the run (existing circuit). Any footage failure keeps the photo or vector treatment, so no provider is a dependency.
- **Discovery failures and download failures** are reported separately in the selection reports (`searches[].note` and per-scene `rejected`).

## 4. Pexels

Pexels offers free images and videos (Pexels License: free commercial use, attribution not required). With a key it adds a second large stock-footage library (portrait clips are common, which suits Shorts). It is optional; add `PEXELS_API_KEY` to `.env.local` to enable it. Nothing depends on it.

## 5. Remaining provider issues

- Openverse timeouts (5 of 79): handled by retry and fallback; no action.
- GNews rate limiting: optional discovery source; the other sources continue.
- Internet Archive footage: evaluated, not integrated. Licences vary per item and metadata quality is uneven, so automated licence verification is unreliable. Left as a candidate.
