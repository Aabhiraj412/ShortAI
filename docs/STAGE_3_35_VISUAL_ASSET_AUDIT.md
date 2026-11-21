# Stage 3.35: visual asset audit and real-world footage

## 1. Audit of what the final videos actually show

**Method:** the rendered Pop Info scene plan of each completed video (`pop_info_film/plan.json`) was taken as ground truth for what appears on screen. Assets that were downloaded but never rendered are not counted. **Sample:** the 30 most recent completed productions (2026-11-20 to 2026-11-21).

| On-screen content | Share of screen time | Scenes |
|---|---|---|
| Text-led scenes (title, kinetic type, callout, reveal) | 45.9 % | — |
| Motion graphics (procedural vector scenes) | 43.5 % | — |
| Licensed photographs | 10.6 % | 25 |
| **Real-world video footage** | **0 %** | 0 |

**Photographs by provider:** Wikimedia Commons 21, Openverse 4, Pixabay 0, Internet Archive 0, NASA 0.

**Top reasons a candidate photo was rejected:**

| Reason | Count |
|---|---|
| Not about this scene | 2589 |
| Licence not approved | 618 |
| No description or tag evidence | 238 |
| Not a still image | 15 |

**Conclusion:** the videos relied almost entirely on text and vector graphics. Pixabay was configured and answered every request, yet contributed nothing. Its image results carry no descriptions, so they fail the scene-evidence check (see `STAGE_3_35_PROVIDER_AUDIT.md` §2).

**Documentary storyboard assets** (acquired before rendering, about 100 s per cycle) are **not used** by Pop Info renders. The new manifest lists them as `acquired_not_rendered` and never counts them as used.

## 2. Pixabay video integration

- Videos API (`/api/videos/`, `video_type=film`, safe search on).
- The smallest rendition with a short side of at least 720 px is downloaded, which keeps traffic low and still gives a sharp 1080×1920 crop.
- **Licence:** Pixabay Content License (no attribution required). A credit is still shown on screen and recorded in the manifest.
- **Title:** newer Pixabay page URLs are `id-<number>`, so the title falls back to the clip's tags.

## 3. Footage provider evaluation

| Provider | Key | Licence handling | Portrait clips | Result |
|---|---|---|---|---|
| **Pixabay Videos** | existing key | single licence, commercial use | some | **Integrated (primary)** |
| **Pexels Videos** | `PEXELS_API_KEY` (not set) | Pexels License | many | Integrated, key-gated, optional |
| **Wikimedia Commons Video** | none | per-file `extmetadata` (CC0 / PD / CC BY / CC BY-SA allowed; NC and ND rejected) | few | Integrated; 720p WebM derivative only |
| **NASA Video** | none | public domain unless the caption names a copyright holder (then rejected) | few | Integrated, space subjects only |
| Internet Archive | none | per-item, often unclear | — | Evaluated, **not integrated** (licence cannot be verified automatically) |

No paid service, subscription or cloud rendering was added.

## 4. Footage selection design

**Which scenes take footage.** Not the title, not the payoff, and not data scenes (stat, compare, timeline, process, kinetic). Scenes that already hold a relevant photograph are also skipped. At most 60 % of scenes take footage.

**What it searches for:**
- **Subject:** the title's filmable words that recur in the narration ("Global analysis reveals how lakes amplify glacier ice loss" → `lake glacier`), not the photo anchor.
- **Scene queries:** the subject plus the scene's own filmable words that recur in the script. Abstract words such as "analysis", "relationship" or "accelerate" are never used.

**Relevance gate.** A clip must:
- cover every subject word, or half of them plus one of the scene's words, or three of the scene's words;
- **not show a differently qualified subject**: when the script consistently qualifies the subject ("gas turbine", twice or more), a clip tagged with another kind ("wind turbine") is rejected.

**Further gates:**
- licence allow-list;
- clip duration at least 70 % of the scene;
- download at most 80 MB;
- ffprobe validation, short side at least 640 px, a decode test and a black-frame check;
- one clip per film (deduplicated);
- a content-addressed cache shared across productions.

**Processing.** One FFmpeg pass per clip extracts the scene's frames, centre-cropped to 1080×1920 at 30 fps with audio dropped, and skips a 1 s fade-in when the clip is long enough. The Lemo-Opuscar engine draws the current frame full-bleed under the captions. This stays **one renderer**: no second render path and no overlapping workers.

**Fallback.** Any failure (a provider error, no relevant clip, a broken download) keeps the scene's photo or vector treatment, and the reason is recorded per scene in `footage_selection.json`, including download error details.

**Switch.** `CRAZZYDOCS_FOOTAGE=off` disables footage without a code change. It is on by default, as an approved render option recorded in render parity.

## 5. Controlled preview (scratch only, production untouched)

Re-rendered from existing productions into a scratch folder, while no job was active, outside the render-slot windows. Nothing was published.

| Video | Before (production) | After (with footage) | Quality gate | Render | Peak memory |
|---|---|---|---|---|---|
| `cycle_4_20261121_0300`: lakes amplify glacier ice loss | text 38.5 %, motion graphics 61.5 %, footage 0 % | **footage 51.9 %** (3 Pixabay clips: Iceland glacier lagoon, an alpine lake with glacier tags, a Canadian mountain lake), text 38.5 %, motion graphics 9.5 % | pass, 89.0 (visual quality 96.8 → 100, hook 75.9 → 76.8, motion 88.2 → 85.1, storytelling 96.4 → 93.9; pacing unchanged at 57.1) | 324 s in total, 189 s build and render | 2.15 GB |
| `cycle_16_20261121_1500`: turbine blade (gas and steam turbines) | text 59.1 %, motion graphics 40.9 % | **unchanged: no footage**. Every stock clip showed a *wind* turbine, which is not this subject; rejected as "a differently qualified subject" | pass, 88.8 | 244 s | 1.75 GB |

**Verification of the glacier preview:**
- 1080×1920 H.264, yuv420p, BT.709 limited range, 51.2 s.
- The footage plays rather than freezing: in the footage region, frames 2 s apart differ (PSNR 22 dB, against 38 dB at 0.1 s), which matches the source clip's own motion.
- Captions and the per-clip credit line render over the footage.

**What the iteration found and fixed:**
1. The first selector accepted wind-turbine clips for a gas-turbine script, on the shared word "turbine". Fixed with the qualifier-conflict rule.
2. Abstract title words ("Global analysis") produced unusable queries. Fixed with subject and query extraction from recurring filmable words.
3. Pixabay titles read "id". Fixed with a fallback to the clip's tags.

All three have regression tests.

**Composition target.** Footage 40–60 %, photographs 20–30 %, graphics and text 20–30 % are **flexible targets**: relevance comes first. A subject with no honest footage (the turbine blade) keeps its vector scenes. The 30-video baseline above is the "before" measurement; the Ops → *Visual composition* card tracks the share over the next 20 renders.

## 6. Asset manifest (per video)

`manifests/asset_manifest_<job>.json` is written after every render. Each rendered external asset records:
- asset ID, job ID and scene ID;
- asset type (photograph or video_footage), provider, title and tags;
- matched terms;
- source URL, download URL, original and rendered resolution, format, duration and frames rendered;
- scene seconds;
- licence, licence URL, creator, attribution and whether attribution is required;
- relevance, quality score, search query and selection reason;
- whether it was reused from the cache, validation status and SHA-256.

The manifest also records the film's composition and the storyboard assets that were acquired but not rendered.
