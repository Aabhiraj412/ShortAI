# Stage 3.32 (roadmap): Advanced Video Creative Intelligence

**Numbering note:** the original roadmap's Stage 3.32 is *Advanced Video Creative Intelligence*. The autonomy work delivered on 2026-11-20 reused the number as 3.32A–J. This document is the roadmap 3.32; its code lives in `backend/app/creative_intel/`.

## 1. Audit: what already existed (reused, not rebuilt)

| Capability | Existing component |
|---|---|
| Hook, curiosity loop, narrative progression, grounding, originality, spoken naturalness, pacing, payoff | `content/script_quality.evaluate_script` + `content/hook_evaluator` (attached to every script) |
| Controlled hook variation | `grounded_script_writer.choose_hook_type`: five hook styles, assigned per job by a hash, which is randomised |
| Scene/composition diversity at planning time | `rendering/creative_direction`, `visual_diversity_policy`, Pop Info `creative_director` / `scene_planner` |
| Frame-level visual quality gate | Pop Info `quality_report.json`; `rendering/quality/frame_validator`, `quality_gate` |
| Creative decision trail | render manifest (renderer, engine, script fingerprint, creative direction), Pop Info `plan.json`, storyboard |
| Video ↔ job link | `publishing_ledger` (video path contains the job id); `video_performance` (3.30) |

## 2. Gaps closed

1. **No evaluation of the actual rendered video against creative criteria.** QC checked compliance and validity, not craft.
2. **No single per-production record** joining the decisions and their evaluation to the YouTube video ID, which Stage 3.35 needs.
3. **No way to run a controlled creative experiment** beyond the fixed five-way hook rotation.

## 3. Evaluation (`creative_intel/evaluation.py`)

Fourteen dimensions, each 0–100 with evidence and its source. An unmeasurable dimension is `null` with the reason and is excluded from the averages.

| Source | Dimensions |
|---|---|
| Script (existing evaluator) | hook, narrative_progression, storytelling, ending_payoff, pacing |
| Scenes: the **rendered** Pop Info plan, falling back to the storyboard | visual_relevance, scene_diversity, animation, transitions |
| Subtitles (.ass) | text_readability (reading speed above 17 characters/s, line length above 36) |
| **Rendered MP4** (one FFmpeg pass at 270 px) | composition (exposure, tonal variety, frozen and black frames), narration_audio (integrated LUFS against −17 to −12 and true peak), av_sync (stream durations and start offsets: a stream-level check, not word-level alignment), visual_dynamics (visual change events per minute) |

The output separates `plan_score`, `render_score`, the 3 weakest dimensions, and the creative `decisions`:
- hook type, hook text and angle;
- script source and fingerprint;
- renderer, engine and creative direction;
- scene templates and design.

**Calibration finding:** Pop Info transitions are eased, not hard cuts, and their scene score peaks below 0.30. Change events therefore use a 0.08 threshold, with events closer than 0.3 s merged. This was checked on real output: 9 designed transitions were found.

**Runtime:** about 5 s per 54-second video.

## 4. Records for Stage 3.35 (`creative_intel/store.py`)

Table `creative_records`, primary key `job_id`. It stores:
- `youtube_video_id`, resolved from the publishing ledger by exact job-id match in the video path;
- the creative decisions;
- overall, plan and render scores;
- the full evaluation;
- the experiment and arm.

Audience metrics are **not** stored here: Stage 3.35 joins `video_performance` on `youtube_video_id` or `job_id`. Reads never create tables.

**Scheduling:** the existing daemon's `creative_evaluation_tick` runs hourly at :33, after a cycle's render window. It evaluates up to 3 finished productions from the last 14 days and links video IDs. It **skips entirely while any production job is active**. It writes only `creative_records`.

## 5. Controlled experiments

`creative_experiments` holds DRAFT, ACTIVE, PAUSED and COMPLETED experiments, with at most one ACTIVE per dimension (a unique index).
- **Supported dimension:** `hook_type`.
- **When ACTIVE:** the script writer's per-job hash assignment is limited to the experiment's arms (`choose_hook_type_for_job`). The arm is written into the script (`creative_experiment`) and the creative record.
- **Fail-safe:** with no active experiment, or any error reading one, the normal five-way rotation is used unchanged.
- **Endpoints:** `POST /api/creative/experiments` (create a draft) and `POST /api/creative/experiments/{id}/status`. Both need local, same-origin, CSRF and a validated operator. No experiment is active.

**No audience claims.** The dashboard and summaries report *craft* scores per hook type. Which hook style performs better with viewers needs the Stage 3.35 analytics join. There is no audience data yet (0 `video_performance` rows).

## 6. First results (dry run on a copy of the live database, 6 most recent productions)

- **Coverage:** all 6 evaluated and linked to their YouTube IDs; mean craft score 88.1.
- **Strong:** visual craft is strong (animation, transitions, audio, sync, diversity 97–100).
- **Weak:** the clear weakness is **the hook (mean 45/100)**, followed by storytelling (70) and ending payoff (73).

The live database starts filling after deployment, 3 productions per hour.

## 7. Dashboard

Ops tab → *Creative intelligence*. It shows:
- evaluated count and how many are linked;
- the weakest dimensions;
- craft score by hook type;
- the last 15 productions;
- experiments, with create, activate, pause and complete controls.

All values come from persisted records.

## 8. Tests

`backend/tests/test_creative_intel_332.py`: 16 cases, covering:
- the script, scene and subtitle dimensions;
- render analysis on synthetic video and on corrupt input;
- end-to-end evaluation;
- the store and video linking;
- experiment validation, the single-active rule and the effect on hook assignment;
- fallbacks;
- the runner's active-job skip;
- the API.
