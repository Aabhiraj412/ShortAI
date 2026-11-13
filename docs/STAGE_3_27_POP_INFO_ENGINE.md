# Stage 3.27: Pop Info Creative Engine, Gemini Validation & Visual QC (2026-11-13)

Each item below is marked **implemented**, **tested**, **rendered** (real MP4s exist), **deployed**, or **BLOCKED**.

## Status at a glance

| Item | Status |
|---|---|
| Pop Info engine (`pop_info_v1`) | Implemented, tested, rendered (3 test videos). Deployed **behind a feature flag that is OFF**. |
| Rendered-video quality gate | Implemented, tested on real MP4s, run on 3 new videos and 2 baseline videos. |
| Creative identity extension | Implemented and tested. |
| Script-quality evaluation | Implemented and tested; attached to every script. |
| Dashboard "Creative Engine" card | Implemented and tested. |
| Real Gemini validation | **BLOCKED**: `GEMINI_API_KEY` is absent from `.env.local`. No live call was made. |
| Real photographs in the test videos | **Not achieved.** Provider failures in this environment (below). |

## Part 1: Preflight (21:24 IST)

- **Git:** `main` = `origin/main` = `24b5df3`.
- **Services:** dashboard PID 3312, daemon PID 19116, revision `0ed65050`, status OK. No active job.
- **Schedule:** next cycle is `cycle_1` at 00:00 IST.
- **Gemini key:** absent, so live Gemini validation is BLOCKED.
- **Known test baseline:** 4 failures.
  - `test_production_dashboard_browser::test_dashboard_html_page_served`
  - `test_stage_3_8_integration::test_end_to_end_orchestration_run`
  - `test_youtube_branding` (2 tests)

## Part 2: Script validation

- **Live validation:** BLOCKED; no key.
- **New evaluator:** `backend/app/content/script_quality.py` scores each script on 8 criteria, each 0–10 with a written reason:
  - hook in the opening words;
  - spoken style;
  - five-stage structure;
  - grounding of numbers;
  - originality (no 9-word copy);
  - duration fit;
  - payoff;
  - narratability.
- **What it does not judge:** how interesting a script is beyond those signals. The report says that needs a human listen.
- **Where scores go:** `write_grounded_script` attaches the evaluation to every script (`script["quality"]`) and records `script_source`. Production manifests now also record `script_source` and `visual_engine`.

Applied to the three test scripts (all `extractive_fallback`, because there is no key):

| Video | Overall | Hook | Originality | Payoff |
|---|---|---|---|---|
| Breakthrough Prize | 67.1 | 2 | 0.0 | 5 |
| Space junk | 66.5 | 3 | 0.8 | 5 |
| Climate anxiety | 75.4 | 6 | 3.5 | 5 |

The fallback scripts are the weakest part of every video. **They are not AI-generated** and are labelled as fallback everywhere.

## Part 3: The engine (`backend/app/rendering/pop_info/`)

### Medium and identity

Taken from `docs/reference_styles/pop_info.html` (studied, not copied):
- flat vector art drawn in canvas, seeded and deterministic (no `Math.random`);
- the reference palette: sage, sand, cream, teal `#2F6F73`, coral `#E85D4A`, yellow `#FFD86B`;
- Fredoka and Lilita One fonts (SIL OFL, bundled with Lemo-Opuscar and copied with their licence, so there are no network fonts);
- springy pop-ins and decaying wobble;
- a teal iris wipe with a yellow ring at the open and close;
- a counter bubble with a pill tag, and a dotted path with pins;
- a slow camera push and drifting dust.

The layout is 1080×1920 (a 540×960 design space). Text stays inside a Shorts-safe area that avoids the right-hand action buttons and the bottom title band.

**Deliberate difference from the reference:** the reference uses a cartoon character as its emotional anchor. This channel's own policy (`PROHIBITED_CHARACTER_TOKENS`) bans cartoon characters and mascots, so emotion is carried by objects, motion and typography instead.

### `scene_planner.py`: script and timings to a visual brief per scene

Each brief records:
- narration;
- template;
- main subject and supporting objects;
- background and composition;
- headline and highlight;
- numbers;
- animation direction;
- transition in;
- asset requirements and the fallback;
- sound cues.

How it plans:
- **Scene length:** one idea per scene, split at sentence and clause boundaries, aiming for 6 s or less.
- **Templates:** chosen from content.
  - years → `timeline`; one quantity → `stat`; two quantities → `compare`;
  - a question → `question`; a mechanism cue → `process`;
  - a licensed photo → `photo`;
  - otherwise a rotation of `spotlight` and `callout`;
  - the film always opens on `title` and ends on `reveal`.
- **No repetition:** the same template or transition never appears twice in a row, and an icon used twice is deprioritised.
- **Text:** emphasis text is a scored 2–5-word window from the narration. It is never invented and never a call to action. Numbers are taken only from the narration.

### `engine.js`: the visual library

- **Templates (10):** title, stat, compare, timeline, process, question, spotlight, callout, photo, reveal.
- **Icons:** 30 flat-vector subject icons.
- **Transitions:** slide, circle reveal, diagonal wipe, pop zoom and split, plus the iris at the open and close.
- **Layout audit:** `window.layoutAudit(t)` measures every drawn text box against the safe area.

### `film_builder.py` and `pipeline.py`

- The film package is self-contained.
- **Photos:** only assets that are licence-verified, have relevance of at least 0.5, and are still images are used, each with its provenance recorded. A successful download alone never qualifies an asset.
- **Pipeline:** Lemo-Opuscar render (the approved renderer) → sound mix → mux → layout probe → quality gate.

### `sound_design.py`

- All sound is synthesized in code, so there is nothing to license: pops, taps, counter ticks, whooshes and a soft pad.
- The pad is side-chain ducked under the narration.
- Loudness uses two-pass EBU R128 normalisation with a −14 LUFS target.
- **Measured result:** −15.7 LUFS and −4.2 dBTP. Linear mode stops short of −14 to protect true peak. This is a remaining tuning item.

### Feature flag

- `CRAZZYDOCS_VISUAL_ENGINE=pop_info_v1`, set in the environment or `.env.local`, selects the engine.
- **Unset or any other value → the legacy film builder (the default and the rollback path).**
- The legacy code path is unchanged; it is only indented under the flag check.

## Part 4: Creative identity (extends the existing registry)

**New columns** (added by migration; existing rows keep their defaults):
- `hook_type`, `narrative_structure`;
- `script_text`, `hook_text`, `ending_text`;
- `scene_sequence_json`, `visual_concepts_json` and its fingerprint;
- `typography`, `animation_json`, `transition_sequence_json`;
- `audio_treatment`, `thumbnail_concept`;
- `visual_engine`, `script_source`.

**`near_duplicates()`** — blocking:
- script 3-gram Jaccard ≥ 0.5;
- hook word Jaccard ≥ 0.6;
- scene-composition concept Jaccard ≥ 0.85 (checked **before** rendering).

**`near_duplicates()`** — advisory:
- ending similarity ≥ 0.7 when the script is the labelled extractive fallback. Its fixed closing template would otherwise block every production until a key exists. For model-written scripts the same check blocks.

Calls to action and the topic's own words are removed before endings are compared. Shared channel branding is never compared.

A creative-identity rejection is PERMANENT, and the render-retry loop now re-raises it instead of retrying.

## Part 5: Visual quality gate (`backend/app/services/visual_quality_gate.py`)

The gate inspects the **MP4 itself**:
- **Frames** at 4 fps: blank, frozen, contrast, motion, scene-cut cadence, near-duplicate scenes (dHash), opening and ending activity, and detail in the Shorts overlay bands.
- **Audio** via ffmpeg: EBU R128 loudness, true peak, silences.
- **Container** via ffprobe: resolution, aspect, duration, streams.
- **Typography:** from the page layout probe when available; otherwise reported as `not_measured` and **excluded** from the total.

Weights and thresholds are configurable. The report lists:
- the overall score and each dimension's score;
- the basis and evidence for every score;
- timestamped issues with severity, evidence and a suggested fix;
- the pass/fail decision.

## Part 6: Results on real MP4s

Test videos are in `media/test_renders/pop_info_v1_final/`. They are **not published or uploaded**.

| Video | Full gate | Measured-only (same mode as the baseline) | Audio LUFS / TP | Blank frames | Issues |
|---|---|---|---|---|---|
| Breakthrough Prize (52.5 s) | 89.8 pass | 82.4 | −15.7 / −4.2 | 0 | none |
| Space junk economics (51.9 s) | 89.6 pass | 83.1 | −15.7 / −4.4 | 0 | none |
| Climate anxiety (50.8 s) | 85.1 pass | 78.6 | −15.6 / −4.2 | 0 | 1 minor (small text) |
| Baseline: SoHo, published #284 | n/a | 74.7 **fail** | −20.9 / −6.1 | critical blank at 54.25 s | 1 critical |
| Baseline: Science fiction, Cycle 4 | n/a | 78.2 | −21.8 / −2.9 | 0 | static stretches of about 21 s |

**How to read these numbers:**
- **The fair comparison is the measured-only column**, which is +4 to +8 over the baseline.
- **Main measured gains:** audio level and ducking (89–90 against 51–57), no blank frames, and more motion.
- **Pacing is mixed:** the SoHo baseline scores higher on cut cadence.
- **Storytelling** in measured-only mode is visual distinctness only, which is 60 for every video. The gate cannot see relevance without the plan.
- **The hook dimension saturates:** almost every video scores 100. It is a weak discriminator and should be tightened.

**From human review of the frames** (the contact sheets are next to the videos):
- **Better:** one consistent illustrated identity, topic-relevant subjects, readable emphasis text instead of narration chunks, counters, year paths, labelled process steps, callout cards and a payoff reveal.
- **Still weak:**
  - several emphasis phrases end awkwardly ("Fundamental Physics reshaped"), because they are cut from the fallback script's template closing;
  - the reveal scene is long (about 10 s);
  - there are no real photographs.

**Photographs:**
- Internet Archive requests fail with `CERTIFICATE_VERIFY_FAILED: self-signed certificate in certificate chain`. TLS to that host is being intercepted on this network, and certificate checks were **not** disabled to work around it.
- Openverse timed out, and had a real bug: a `null` `filetype` threw away the whole result batch. That is **fixed**, with a regression test.

## Part 7: Cost and architecture

- **Paid services:** none added, and no paid API is called without a Gemini key.
- **Per-video cost today:** $0.
- **With a key:** one or two Gemini Flash calls per script. Measure them in Google AI Studio; no pricing is assumed here.
- **Render time:** 3.3–4.1 min per video on this machine, including TTS, render, mix and the gate.

## Part 8: Dashboard

The Overview tab has a new **Creative Engine** card showing:
- engine version and the flag's state;
- visual style;
- script source;
- Gemini status (presence only, never the value);
- asset sources;
- last quality score and the QC pass/fail pill;
- estimated cost;
- a quality-report selector with the dimension table and timestamped issues (rendered as text only).

Endpoints, both localhost-only:
- `GET /api/creative-engine/status`
- `GET /api/creative-engine/reports/{id}`

## Part 9: Safety

- The schedule, publishing policy and YouTube videos are unchanged.
- The flag is OFF, so tonight's cycle uses the legacy builder.
- Nothing was uploaded.
- Self-learning is not enabled.

## Remaining blockers and next steps

1. **Gemini key (blocker):** add `GEMINI_API_KEY` to `.env.local`. Then run the real 5-topic script validation and re-render the three tests with model-written scripts.
2. **Photo providers:** resolve the TLS interception to `archive.org`, which is a network or antivirus setting, and re-test Openverse now that its bug is fixed.
3. **Engine tuning before enabling the flag:**
   - verb-ending phrase penalty;
   - cap the reveal at about 6 s;
   - loudness to exactly −14 LUFS (a limiter before loudnorm);
   - tighten the gate's hook dimension.
4. **Before the flag goes on:** run one flagged production in VALIDATION context end to end through the orchestrator. Only the test harness has exercised the engine so far, not a scheduled cycle.
