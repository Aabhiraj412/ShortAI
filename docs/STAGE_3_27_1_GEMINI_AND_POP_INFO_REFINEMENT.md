# Stage 3.27.1: Real Gemini Validation, Pop Info Refinement and Real Photography (2026-11-13/14)

Each item is marked **implemented**, **tested**, **validated live** (real API or network calls), **rendered** (real MP4s exist), **deployed**, or **not done**.

## Status at a glance

| Item | Status |
|---|---|
| Gemini key loading | Verified. `.env.local` is git-ignored and the key is present; its value was never printed. |
| Real Gemini scripts | **Validated live: 5 of 5 topics.** All were written by `gemini-3.6-flash` through the new fallback chain, because the configured `gemini-3.8-flash` was unavailable (503, 504, 429). The Rhine topic needed a second run. |
| Spelled-out number grounding | Implemented and tested. The loophole found in real Gemini output is closed. |
| Hook evaluator and 8-criterion script evaluator | Implemented and tested. Scores are spread out, not saturated. |
| Video-gate hook dimension | Implemented and tested. Half measured opening, half hook-text evaluation. |
| Photo providers (TLS, timeouts, retries, health) | Implemented, tested, validated live. |
| Scene-level photo selection, cache, provenance | Implemented, tested, validated live. |
| Hybrid photo treatments in the engine | Implemented, rendered. |
| Reveal about 6 s, phrase endings, loudness −14 LUFS | Implemented, tested, rendered. |
| Production QC false failure (`Regulus (Turner)`) | Fixed and tested. Found while cycle_1 ran tonight. |
| New engine for scheduled production | **OFF** (unchanged). |

## Part 1: Preflight (22:48 IST, Nov 13)

- **Git and services:**
  - `main` = `origin/main` = `9b9d09f`;
  - daemon PID 7968 and dashboard PID 10352, both CURRENT;
  - no active job;
  - schedule `d3550fbd996b`.
- **When the key arrived:**
  - The key was added at 22:42, after the daemon started (22:23).
  - Settings load once at import, so **the running daemon did not have the key**, and tonight's cycle_1 used the labelled fallback.
  - The scheduler was not restarted just to load the key. The milestone deploy restarts it.
- **Worktree check:** the worktree resolves the project root to itself, so it cannot see the main checkout's `.env.local`.
- **Isolated validation:** `backend/scripts/validate_gemini_scripts.py` loads the key from an explicit file into its own process only, and refuses to write a report that contains the key.

## Part 2–3: Real Gemini validation

### What happened

**Run 1** (configured model `gemini-3.8-flash`, 3 tries per topic with backoff):
- 15 of 15 calls failed with HTTP 503 UNAVAILABLE or 504 DEADLINE_EXCEEDED.
- A one-word probe also returned 503. So the key authenticated and the model was overloaded.

**Model probe** (same key):

| Model | Result |
|---|---|
| 3.8-flash, 3.7-flash | 504 |
| 3.6-flash | answered, in 48.7 s |
| 3.5-flash | answered in 17.9 s, served by 3.6-flash |
| 2.5-flash | 404 |

**What changed in response:** the writer now has a **configurable fallback chain**, `GEMINI_FALLBACK_MODELS`, which defaults to `gemini-3.6-flash`. It:
- moves to the next model **only** after temporary failures (408/429/5xx/timeouts, bounded backoff of 4 s then 12 s, at most 180 s per model) or a 404;
- never moves on authentication, network or bad-request errors;
- **records the model that actually wrote the script** in `script_source`, so a fallback model is never hidden.

**Run 2:**
- 4 of 5 topics were accepted from `gemini-3.6-flash`. `gemini-3.8-flash` was by then returning 429 RESOURCE_EXHAUSTED.
- The Rhine topic was rejected twice by the copy check: the model kept reproducing "the late fourth and early third millennia B.C." from the research. It fell back, **labelled as fallback**.

**Rhine retry:** accepted on the second draft, after the validator returned the copied phrase as feedback.

### Per-topic results

Token counts are as reported by the API.

| Topic | Model | Calls (retries) | Latency of accepted call | Tokens in / out | Validation failures | Script score | Hook |
|---|---|---|---|---|---|---|---|
| Breakthrough Prize | gemini-3.6-flash | 4 (3) | 58.8 s | 908 / 279 | none on accepted draft | 88.5 | 7.3 |
| Royal Gloucestershire Hussars | gemini-3.6-flash | 4 (3) | 35.0 s | 982 / 303 | none | 80.0 | 5.4 |
| Space junk economics | gemini-3.6-flash | 4 (3) | 39.4 s | 750 / 279 | none | 80.6 | 6.4 |
| Climate anxiety | gemini-3.6-flash | 4 (3) | 23.1 s | 569 / 274 | none | 82.6 | 5.8 |
| Rhine double axes (retry run) | gemini-3.6-flash | 5 (4) | 21.6 s | 1301 / 538 (two drafts) | 1 copied phrase (fixed in draft 2) | 81.1 | 4.6 |
| *Rhine, run 2 (fallback, for comparison)* | extractive fallback | — | — | — | 2 copied phrases | 44.6 | 3.1 |

- **Retries** are almost all 429 or 503 on `gemini-3.8-flash` before the chain moved to 3.6.
- **Cost:** the API reports tokens but not prices, so no cost figure is claimed. Multiply the token counts by the current Google AI Studio price for the model used.

### What reading the scripts showed

**Breakthrough Prize:** the strongest script. It has a concrete hook ("pays more than double the cash of a Nobel Prize"), and every number checks against the research.

**Space junk:** the research is thin (about 1,000 characters), so the model padded:
- three sentences are about the source rather than the subject ("outlines this in an arXiv preprint", "is normally known for…");
- the reveal is vague ("comes down to control and economics").

The old evaluator scored this script **100**. The new one scores narrative progression 5.5 and names each padding phrase.

**Climate anxiety and Hussars:**
- clean, spoken style;
- generic payoffs that do not call back to the hook;
- the Hussars hook has no open loop (curiosity 1.1).

**A grounding loophole was found and closed.** Gemini spells numbers out for narration ("three million dollars"), and the validator only checked digits. `number_words.py` now parses spelled quantities. A quantity is enforced when:
- it is 13 or more;
- it has a magnitude word (hundred, thousand, million…);
- it is followed by a unit word;
- or it is a multiplier (twice, double).

All five accepted scripts still pass under the stricter check. Their numbers are supported by the research ("US$3 million", "US$300,000", "next five", "more than twice").

## Part 4D: Hook and script evaluation

**`hook_evaluator.py`** scores eight characteristics, each with evidence:
- curiosity, specificity, surprise, stakes;
- contradiction, clarity, relevance, continuation.

**Scoring:**
- **core:** clarity, relevance, continuation, plus curiosity;
- **pull:** the best two of specificity, surprise, stakes and contradiction. A strong hook uses two or three levers, not all four.

**Penalties:**

| Opening | Penalty |
|---|---|
| Encyclopedia or definition | −3.5 |
| Generic opener ("Did you know", "Have you ever wondered") | −2 |
| Throat-clearing | −1.5 |
| First sentence over 28 words | −1 |

**Calibration:**

| Hook kind | Score |
|---|---|
| Encyclopedia, generic or throat-clearing | 0.0–1.9 |
| Old fallback hook | 3.6 |
| Gemini hooks | 4.6–7.3 |
| Strong hand-written hooks | 5.6–7.2 |

**`script_quality.py`** now scores the 8 required criteria, each with a reason:
- hook, curiosity, narrative progression, grounding;
- originality, spoken naturalness, pacing, payoff.

Narrative progression penalises padding about the source and a vague reveal. Grounding checks digits and spelled-out numbers.

**Visual gate hook dimension:**
- **What was wrong:** it scored any animated opening about 100.
- **Now:**
  - the measured opening uses stricter scales and counts how soon the first change of picture arrives, worth 50%;
  - the hook-text evaluation of the plan's HOOK narration is worth the other 50%;
  - without a plan it is visual-only and says so.

## Addendum A1: Visual gap analysis

Sources: `docs/reference_styles/pop_info.html`, the Stage 3.27 contact sheets, and the published SoHo and Cycle 4 videos.

| Area | Reference | Stage 3.27 output | This milestone |
|---|---|---|---|
| Composition | Whole illustrated **environments**: a room, shelves full of products, a walking path, depth layers | One subject icon in a disc, two satellites, a headline. Clean but sparse, and the same few compositions recur | Photo treatments add real-world environments. **Vector environments are not built yet** (biggest remaining gap). |
| Asset relevance | Every object belongs to the story | Keyword icons are on-topic but generic (a trophy for any prize) | Scene-specific photos of the named person, place or object when one is licensed and relevant |
| Real-world grounding | None (fully illustrated) | None (photo acquisition returned 0) | Licensed photos on scenes that name a real person, place or organisation |
| Typography | Short punchy labels | Emphasis phrases cut from fallback text ("…Physics reshaped") | Weak-ending filter, verb-ending penalty, whole-clause bonus. Gemini scripts give better source phrases. |
| Pacing | Constant small motion, beat every 1–2 s | Scenes ≤ 6 s, but the reveal ran about 10 s | Reveal split to about 6 s with no narration dropped |
| Transitions | Iris and wipes | 5 transitions plus iris | Unchanged. Photo motions add 5 camera moves (push, pull, pan left/right, rise). |
| Emotion | A character carries the emotion | Not allowed (channel policy) | Objects, photographs of real people and motion |

**Tools evaluated:**
- **Lemo-Opuscar canvas (kept):**
  - deterministic `render(t)`;
  - already the approved production renderer;
  - photos draw with sub-pixel camera moves.
- **Motion Canvas (not adopted):** its own scene graph and renderer would be a second, competing engine, which the brief rules out.
- **Lottie (not adopted):** `lottie-web` can render frame-exactly to canvas, but there is no licence-clear animation library on hand. LottieFiles assets carry mixed licences and would need per-file review.
- **SVG:** already reachable through canvas `Path2D`; used for icons.
- **FFmpeg compositing (not used for photos):** cruder than canvas for Ken Burns moves and masks. It stays for muxing and audio.

## Addendum A2/A3: Photo providers (measured live from this machine, Nov 13–14)

| Provider | API / auth | Measured behaviour | Licensing for this channel | Cost | Decision |
|---|---|---|---|---|---|
| Wikimedia Commons | Public API, no key; wants an identifying User-Agent | Search OK in 1.2–1.9 s. A download with a generic User-Agent got **429**, so the project's existing identifying User-Agent is now used for downloads too. | Per file. The verifier approves CC0, PD and CC BY; it rejects NC, ND, SA and unknown. Attribution is kept in provenance. | Free | **Primary** |
| NASA Image Library | Public API, no key | OK in 0.7–2.2 s; up to 100 items | US government work, generally public domain. Third-party credits (e.g. ESA, ©) are **rejected**. `~orig` files can exceed 50 MB, so `~large` is used. | Free | Primary for space and earth topics |
| Openverse | Public API, no key (anonymous rate limit) | **Degraded:** 6–17 s per search, frequent **HTTP 504** from its gateway. Timeout raised to 20 s with 1 retry. The provider-side cause is outside our control. | Per item (CC). The `null` filetype bug was fixed in 3.27. | Free | Secondary |
| Internet Archive | Public API, no key | Failed TLS in 3.27. **Root cause:** the Windows root store lacked the GoDaddy G2 root. The leaf was a genuine `*.archive.org` GoDaddy certificate at IA's own IP, so it was **not** interception, which corrects the 3.27 report. Now verified against certifi's Mozilla bundle and working (HTTP 200). | Mostly unknown or mixed licences, rejected by the verifier. Its image search is sparse for modern topics (0 results here). | Free | Kept; low yield |
| Pexels | Free API key | Reachable; **no key configured** | Pexels licence: commercial use and modification allowed, no attribution required | Free | Recommended next (needs a free key from you) |
| Pixabay | Free API key | Reachable; **no key configured** | Pixabay Content Licence: commercial use allowed | Free | Recommended next (needs a free key) |
| Unsplash | Free API key | Reachable | API guidelines require hotlinking their URLs and triggering download events, which doesn't fit offline video rendering | Free | Not recommended |

These licensing terms are as the providers publish them. They are not legal advice, and the verifier's rules decide per file.

**Provider resilience, now shared by every provider through `backend/app/media/net.py`:**
- verified TLS against certifi (never disabled);
- per-provider timeouts;
- retries only for 408/425/429/5xx and timeouts, with exponential backoff;
- 20 MB response cap, 15 MB per image;
- JSON validation;
- failures logged at WARNING with a classified kind (tls, timeout, http, connection) and **never the URL**, because the Pixabay key travels in its query string;
- process-wide health with a circuit breaker after 3 consecutive failures, persisted to `media/asset_cache/provider_health.json` and shown on the dashboard.

A failed provider never stops acquisition; the others are still searched.

## Addendum A4–A8: Scene-level photo selection (`backend/app/rendering/pop_info/scene_photos.py`)

**Which scenes get a photo**
- Only scenes that name a real person, place or organisation, or whose subject is the topic itself.
- At most about 40% of scenes, never two adjacent.
- Never the title or the reveal.
- Data scenes take a photo only alongside their numbers.
- Abstract ideas stay vector, per A5.

**Queries**
- The scene's named entities first ("Yuri Milner", "NASA's Jet Propulsion Laboratory").
- Then the topic's short name, taken from the name the script actually uses ("Breakthrough Prize", "space junk").
- Month names and figure-of-speech words ("on Earth") are not entities.

**Ranking.** Relevance comes from the provider's **description, categories and tags**, never the filename alone. A candidate must pass all of these:
- it is about **this scene**: a scene entity in the caption, or at least a third of the scene's own words. A topic-name match alone does not count, after a live test chose an atomic-clock photo for a sentence about prize money;
- its caption names no person the narration doesn't mention, after a live test chose "Beth Jens at JPL" for a scene about Slava Turyshev;
- it is not junk: logos, maps, diagrams, flags, scans.

Then scored on caption coverage, entity match, narration overlap, resolution, aspect ratio, and reuse in other productions.

**Integrity checks on download**
- magic bytes must say JPEG, PNG or WebP;
- full decode;
- short side ≥ 640 px;
- aspect ratio ≤ 3.2;
- contrast ≥ 18 and sharpness ≥ 25;
- the image is re-encoded to a ≤ 2048 px JPEG;
- dHash duplicates are rejected within a video;
- a focus point is measured from gradient energy and used for every crop.

**Cache** (`media/asset_cache/`)
- content-addressed;
- a cache hit re-verifies the SHA-256, and an altered file is downloaded again;
- the licence is re-checked on every use, because candidates are always freshly verified;
- `used_by` reuse is tracked.

**Provenance** (in the film's `asset_provenance` and `photo_selection.json`) records for every photo:
- asset ID, provider, source URL, download URL, creator, title;
- licence and licence URL, attribution text, whether attribution is required;
- download time, original and derived checksums, format, resolution;
- search query, topic, scene, job;
- relevance breakdown, cache hit, treatment and camera move.

The credit is drawn as a small, unobtrusive pill and is also kept in the metadata.

**Treatments in the engine** (never the same twice in a row), each with one of five camera moves around the focus point:
- full-bleed with a teal fade and headline card;
- taped card;
- circle spotlight with a ring that draws itself;
- split, with the photo above and a counter below.

A photo that fails to load falls back to the vector spotlight.

## Part 4A–C: Phrase, reveal and loudness

**Emphasis text.** Windows are rejected or penalised when they:
- end on weak verbs or adverbs ("reshaped", "-ly");
- cut a verb off from its object ("fear produces | two outcomes", "worth three | million");
- start or end on vague adjectives ("exact same").

They get a bonus for ending where the clause ends. When no clean phrase exists, the planner now returns nothing, and the caller tries another source; it used to emit a raw word run ("or freeze you entirely").

**Other planner changes:**
- **Title cards** show the subject's short name, never a hook phrase. "Nobel Prize" used to head a Breakthrough Prize video.
- **Process diagrams** need three real labels, otherwise another layout is used (no "1/2/3", no "Takes/Exact/Same").
- **A year is never a counter.** The first render showed "2,012".

**Reveal.** The target is 6 s. Splits happen **only at sentence boundaries**, and the final section is never cut mid-sentence, after the first render produced the fragment "or freeze you entirely". A single-sentence payoff keeps its full length (7.3–8.5 s) rather than breaking its meaning. Final reveals:

| Video | Before | After |
|---|---|---|
| Breakthrough | 10.7 s | 7.6 s |
| Space junk | — | 8.5 s |
| Climate | — | 7.3 s |

**Loudness:**
- **What was wrong:** loudnorm's linear mode silently fell back to dynamic and undershot.
- **Now:**
  - measure, apply the exact gain, then a look-ahead limiter at −2 dBFS;
  - repeat up to 4 times, because the limiter takes back part of each gain step on sound-effect peaks.
- **Independent measurement of the final MP4s** (ffmpeg EBU R128 in the quality gate): **−14.2 LUFS, −4.1 dBTP**, against −15.6/−15.7 before.

## Part 5–6: Three new test videos

All three:
- use **real Gemini scripts** (`llm:gemini-3.6-flash`);
- are rendered through Lemo-Opuscar;
- are **not published, uploaded or scheduled**;
- are saved in `media/test_renders/pop_info_v1_1c/`, which is not in git.

| Video | Full gate (plan + layout) | Measured-only | With plan, no layout | Script score | Hook | MP4 loudness | Scenes / longest | Photos used | Max identity similarity (script / hook / visual) | Render time |
|---|---|---|---|---|---|---|---|---|---|---|
| Breakthrough Prize | **90.1** pass | 82.5 | 89.0 | 88.5 | 7.3 | −14.2 / −4.1 dBTP | 8 / 10.7 s | 1 (Yuri Milner, CC BY) | 0.00 / 0.00 / 0.00 | 173 s |
| Space junk | **87.6** pass | 80.8 | 86.2 | 80.6 | 6.4 | −14.2 / −4.1 | 8 / 9.9 s | 1 (JPL lab, public domain) | 0.00 / 0.00 / 0.07 | 199 s |
| Climate anxiety | **87.6** pass | 82.0 | 86.3 | 82.6 | 5.8 | −14.2 / −4.1 | 10 / 9.1 s | 0 (abstract subject; every candidate was off-scene) | 0.00 / 0.02 / 0.00 | 181 s |

All three had:
- no blank or frozen frames;
- no text outside the safe area, no overlaps, no undersized text;
- no near-duplicate scenes;
- no creative-identity blocks against the 18–20 registry rows.

### Comparison, all scored by the same revised gate

| Video | Measured-only | With plan | Loudness | Detected cuts |
|---|---|---|---|---|
| NEW Breakthrough | 82.5 | 89.0 | −14.2 | 7 |
| OLD Breakthrough (3.27) | 81.4 | 82.9 | −15.7 | 9 |
| NEW Space junk | 80.8 | 86.2 | −14.2 | 7 |
| OLD Space junk (3.27) | 80.3 | 81.3 | −15.7 | 8 |
| NEW Climate | 82.0 | 86.3 | −14.2 | 9 |
| OLD Climate (3.27) | 82.4 | 83.7 | −15.6 | 8 |
| SoHo, published #284 | 71.8 **fail** (blank frame 54.25 s) | — | −20.9 | 12 |
| Science fiction, Cycle 4 | 77.4 | — | −21.8 | 10 |

**Gate changes behind these numbers:**
- **Hook dimension:** it now judges the hook text when the plan is present, so it no longer saturates.
- **Cut detection:** it compares frames 0.5 s apart and keeps local maxima. The old rule found 2–4 of 7–10 planned cuts; the new one finds 7/7, 8/8 and 9/10. Applied to every video.

**What the numbers say, honestly:**
- **Measured from the MP4 alone, new and old are about the same** (+1.1, +0.5, −0.4). The automated visual measures (contrast, motion, cuts) barely moved, because most scenes are still the same vector templates.
- **The gains are:**
  - audio: −14.2 against −15.7 LUFS, audio dimension 98.8 against about 89.5;
  - the hook and storytelling text, which is where Gemini helps, and shows in the with-plan column (+2.6 to +6.1);
  - correct titles and labels.
- **Against the published baselines,** every Pop Info video is 3–11 points higher, mostly on audio and the absence of blank frames.

### Human review checklist (from the frames)

| Question | Answer |
|---|---|
| Does each scene communicate the narration? | **Mostly.** Weak spots: "completely paralyze" and "famous award" are thin emphasis phrases, and several scenes are a generic lightbulb or leaf. |
| Are photographs relevant and strong? | **Relevant, but there are few:** 2 of 26 scenes. Yuri Milner is exactly right. The JPL lab photo is related but generic (1960s staff in a lab). |
| Are illustrations consistent? | Yes. One coherent palette and icon style. |
| Do animations add meaning? | **Partly.** Counters, process links and timelines add meaning; spotlights are decoration. |
| Is text readable on a phone? | Yes. The layout audit is clean, sizes are ≥ 15 design px, and everything is inside the Shorts-safe area. |
| Are transitions natural? | Yes. Slides and wipes, 0.5 s. |
| Does it feel professionally edited? | **Not yet.** It feels like a clean template system. The same 3–4 compositions repeat, with no environments and no visual surprise. |

## Remaining problems

**Visual**
- The icon-in-a-disc spotlight and callout repeat 3–4 times per video (the climate video uses the leaf icon 4 times).
- There are no illustrated environments, which is the main gap against the reference.
- The photo split scene runs 10.7 s.

**Photos**
- Yield is low. Real licensed photos of specific events and people are scarce, and most candidates are rejected as off-scene or under NC/ND/SA licences, which is correct behaviour.
- Openverse was degraded (504s) throughout.
- Internet Archive returns no images for these topics.
- Pexels and Pixabay would add general-subject photos for free, but need API keys.
- **No video footage** was attempted; the licence and resolution checks for video are not built.

**Audio**
- On target now.
- The limiter works hard on sound-effect peaks. Sound effects could be mixed 1–2 dB lower to leave more headroom.

**Storytelling**
- When the research is thin (space junk, about 1,000 characters), Gemini pads with talk about its source. The evaluator now flags this, but the writer does not yet reject it.
- Payoffs often don't call back to their hook.

**Gemini availability**
- The configured `gemini-3.8-flash` was unusable all night (503/504, then 429). Every accepted script came from `gemini-3.6-flash` through the recorded fallback chain.
- Production should either set `GEMINI_MODEL=gemini-3.6-flash` or keep the chain.

## Production QC false failure found tonight

**What happened**
- cycle_1_20261114_0000 (topic "Regulus (Turner)") failed `CHECK_A_TOPIC_NARRATION` on every attempt (3/3, then a 4/4 recovery).
- **Cause:** the title was split on spaces, so the required keyword was `(turner)`, with parentheses, which no narration can contain. The narration did say "Turner".

**The fix (in this milestone's commit)**
- Words are tokenised without punctuation.
- The check still needs two topic words in the narration.
- Three regression tests cover it, including one confirming that a narration which never names the topic still fails.

**What was not done**
- cycle_1 was not rescued: the restart endpoint correctly refuses while a job is running or scheduled for retry, and nothing was forced.

## Safety

- `CRAZZYDOCS_VISUAL_ENGINE` is **not set**, so production still uses the legacy film builder.
- The schedule is unchanged (`d3550fbd996b`).
- No uploads; no YouTube changes; no self-learning.
- The live database is never written by tests or the harness: identity checks ran on copies.
- The Gemini key was never printed. The validation runner refuses to write a report containing it, and the dashboard reports presence only.

## Recommended next step

**Not yet ready to switch the engine on.** Ready for **one controlled end-to-end production validation** in VALIDATION context through the orchestrator. That exercises the orchestrator path, which only the harness has run so far. Run it after:

1. **More visual variety:** vector environments (a lab bench, an orbit field, a city street), built from the existing icon kit, and a cap of two uses per composition per video.
2. **Better photos:** free Pexels/Pixabay keys for general-subject photos, and moving the writer's padding detection from "flagged" to "rejected".
3. **Settling the Gemini model choice:** pin `gemini-3.6-flash` or keep the fallback chain.
