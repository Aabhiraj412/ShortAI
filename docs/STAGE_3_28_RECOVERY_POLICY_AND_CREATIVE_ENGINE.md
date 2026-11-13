# Stage 3.28 — Unlimited recovery policy, Cycle 1 rescue, Creative Engine 3.28

Date: 2026-11-14 (IST). Legacy renderer remains the production default; the Pop Info engine flag stays OFF.
The production schedule (4 cycles, revision `d3550fbd996b`) and the publishing policy
(`automatic_private_scheduled`) are unchanged.

## 1. Cycle 1 rescue (`cycle_1_20261114_0000`, Regulus (Turner))

| Item | Value |
|---|---|
| State before | FAILED, 4 of 4 production attempts, last failure `BLOCKED_QC [CHECK_A_TOPIC_NARRATION]` ("(turner)" keyword), operator retry already used |
| Recovery window | 00:00 – 04:00 IST; target publication 06:00 IST |
| Upload before rescue | none (no video ID, no ledger row) |
| How it was recovered | deployed the new retry policy; on the first dispatch pass the daemon re-armed the job (`RETRY_POLICY_REARMED`, "deterministic failure; code changed since the failure") |
| Attempt | started 02:00:46 IST, voice-over and render re-run (stale MP4 ignored), canonical thumbnail reused, QC passed |
| Upload | one resumable upload at 02:05:08 IST, private; video ID `pNma2aMg2yo`; ledger row 285 `publication_scheduled`, scheduled 2026-11-14 06:00 IST |
| Duplicates | none: one ledger row for the topic; no earlier video ID existed |
| Final job state | COMPLETED at 02:05:50 IST |

## 2. Retry policy `unlimited_until_recovery_deadline`

Configuration: `backend/app/scheduler/recovery_settings.py` (defaults) → `config/recovery_policy.json`
(validated save via `POST /api/production/recovery-policy`, confirm required, local only) →
`CRAZZYDOCS_RETRY_POLICY` (environment override). `bounded` restores the previous fixed-count behaviour.

| Setting | Default |
|---|---|
| retry_policy | `unlimited_until_recovery_deadline` |
| backoff_schedule_seconds | 60, 120, 240, 480, 900 (last value repeats) |
| jitter_fraction | ±10 %, deterministic per (job, attempt) so a restart recomputes the same delay |
| min_attempt_seconds | 1200: no attempt starts with less recovery time left |
| deterministic_cooldown_seconds | 900 |
| operator_retry_allowance | 3 manual retries per job; never limits automatic retries |
| publish_max_attempts | 5 (separate counter; never re-renders, never re-uploads) |

Behaviour:
* Every failed attempt is appended to `cycle_state.attempt_history` (attempt, kind, time, classification,
  message, code revision, outcome, next retry) in the job checkpoint, so retry state survives restarts.
* Transient and render failures retry with backoff until the deadline. Deterministic failures (QC blocks,
  integrity gate, invalid research, script validation) and permanent failures (code/config errors,
  credentials, duplicate creative identity) wait for a meaningful change: a new code revision or an
  operator retry, which records the acknowledgement. They never tight-loop (900 s cooldown minimum).
* A FAILED cycle still inside its window is re-armed automatically when it has no video, no queue entry
  and no upload, and (for deterministic failures) the code changed since it failed.
* At the deadline no new attempt starts; the job becomes `RECOVERY_WINDOW_EXPIRED`, artifacts are kept,
  the dashboard shows it and the next cycle proceeds. The deadline is never extended.
* Publishing retries (`HANDOFF_PENDING`) have their own counter and reuse the rendered video; a video ID
  already recorded is never uploaded again. Post-render failures are never re-rendered.

Production Monitor (dashboard): policy (shown as "Unlimited until recovery deadline", not a number),
current attempt and total, next retry time and current backoff, recovery deadline and remaining time,
last failure with classification, automatic-retry status, whether operator action is needed, and the
manual retry control ("Retry now", confirmation required).

## 3. Creative Engine 3.28 — visual gap analysis against `docs/reference_styles/pop_info.html`

| Reference trait | 3.27.1 engine | Gap | 3.28 response |
|---|---|---|---|
| Story happens in a drawn place: a perspective room with vanishing point, receding ceiling lights, tiled floor, glowing cooler, swinging sign | flat colour field with drifting blobs behind every scene | **large**: no sense of place | 13 layered illustrated environments (`environments.js`) with a shared perspective-room system, depth-placed props and ambient motion |
| Props live in the space (shelves, products, cooler) at the right depth | icons float on discs | large | props drawn in perspective on walls and floor (flasks, desks, monitors, gears, vault, columns, screens, beds, desks, lamps) |
| Constant small motion in the world (light pulse, sign sway, blinking) | dust only | medium | per-place ambient loops: bubbling flasks, turning gears, blinking panels, ECG trace, ticker, traffic light, drifting clouds, packets on a network |
| Camera moves through the place | 3.5 % push on every scene | medium | environment scenes dolly 7 % toward the vanishing point; diagrams keep the gentle push |
| Typography carries emphasis | words pop in on cards | medium | `kinetic` template: the line fills the frame word by word on a colour field, key word on a yellow block, swoosh |
| Hybrid scenes (explainer graphics over the world) | diagrams on blank fields | medium | setup diagrams (stat, timeline, compare, process, question, spotlight) sit on a washed view of the story's place |
| Characters (shopper) | none | intentional | **not adopted**: no mascots or characters by requirement |
| One coherent world per film | per-scene backgrounds rotate | medium | video-level creative direction picks a primary place (and a secondary for the turn of the story) from title, category and script |

Kept from 3.27.1 without change: scene-level licensed photo pipeline, photo treatments and motion,
reveal split, phrase scoring, sound design and −14 LUFS mix, layout audit and quality gate.

## 4. Creative Engine 3.28 — what was built

* `backend/app/rendering/pop_info/environments.js` — lab, city street, office, bank, space, factory,
  research facility, nature, historical, newsroom, hospital, classroom, digital network. Flat vector in the
  Pop Info palette, deterministic (no `Math.random`), offline, no characters.
* `backend/app/rendering/pop_info/creative_director.py`
  * `video_direction`: primary/secondary environment (title keywords weigh 3×, category hint), palette
    mood, pacing; recorded as `plan.direction`.
  * `cast_scene`: content layouts (numbers, years, mechanisms, assigned photos, title, reveal) are never
    changed; only general-purpose layouts are re-cast — a setting becomes an `environment` scene, a short
    emphatic line with a strong headline becomes `kinetic` type.
  * `environment_for`: the scene's own place wins when its narration names one; the turn of the story
    (escalation/revelation) moves to the secondary place.
  * `enforce_diversity`: every film of 5+ scenes shows its place at least once; 6+ scenes get one
    typography beat when a strong line exists; no visual mode three scenes running when a general-purpose
    scene can change. Every change is logged in `plan.direction.diversity_changes`.
  * `backdrop_for`: title = establishing shot in the place; setup diagrams sit in the place; payoff
    scenes stay clean.
* Engine: `environment` and `kinetic` templates, environment painting with dolly, washed backdrops.
* Fingerprints add `visual_modes` and `environments`; asset provenance records the environment per scene.
* Photos: the typography beat keeps its type unless a strong name is spoken (−1.5 photo score).
* Spoken numbers: real Gemini scripts spell numbers for speech ("three million dollars"), so the
  digit-only reader planned no data scenes in the first 3.28 renders. `extract_numbers` now also reads
  spelled quantitative claims via `number_words` ("3 MILLION DOLLARS", "5,000 YEARS"); "one physics
  prize" stays words.
* Budgets: kinetic type ≤ 1 per film (2 from 9 scenes); illustrated places ≤ 30 % of scenes (min 2).
* Title card and photo anchor never use a time phrase ("years ago" had produced the queries
  "Rhine years ago" and a 1930s portrait painting for the Rhine scene).
* Kinetic fit: every line, including a single long word plus its highlight block, is measured.

## 5. Test videos (unpublished; never uploaded)

Folder `media/test_renders/pop_info_v1_28b` (and `_28c` for the pottery re-render after the anchor fix).
Real Gemini scripts (`gemini-3.6-flash`, reused from the 3.27.1 validation), real TTS, scene-level
licensed photos, rendered through Lemo-Opuscar. Contact sheets `contact_popinfo_test_*.png` were
inspected frame by frame.

| Video | Quality gate | Scenes / layouts | Visual modes | Places |
|---|---|---|---|---|
| Breakthrough Prize | 91.0 pass | 8 / 7 | photo (Yuri Milner, split), environment, 2 stat diagrams, kinetic, illustration | bank (washed behind title and stats), research facility |
| Space junk | 88.1 pass | 8 / 8 | photo (JPL, circle), environment, process diagram, kinetic, callout | space, lab |
| Climate anxiety | 90.1 pass | 10 / 6 | 2 environments, 2 kinetic, 2 callout | nature, newsroom |
| Double axes and pottery | 87.9 pass | 8 / 7 | photo (Central Europe map, split with 5,000 counter), 2 environments, stat, kinetic | historical gallery, nature |

Requirement "photo + illustrated environment + animated diagram + typography-led emphasis in one video":
met by Breakthrough Prize and by Double axes and pottery.

Comparison with 3.27.1 (same topics; `comparison_3_27_1_vs_3_28.json`). Note the 3.27.1 set used
extractive scripts and 3.28 used the Gemini scripts, so the text differs.

| Topic | Measured score 3.27.1 → 3.28 | Distinct layouts | Data scenes | Loudness |
|---|---|---|---|---|
| Breakthrough Prize | 82.5 → 84.1 | 6 → 7 | 1 → 2 | −14.2 → −14.2 LUFS |
| Space junk | 80.8 → 80.8 | 6 → 8 | 1 → 1 | −14.2 → −14.3 |
| Climate anxiety | 82.0 → 84.6 | 5 → 6 | 2 → 0 (script has no numbers) | −14.2 → −14.3 |

Observed in frames: environments read as places (bank hall with vault and coin stacks, detector ring,
gallery with gold frames, river valley, newsroom video wall); washed backdrops keep diagrams legible;
kinetic beats are legible on both colour fields. Known weak spots: some short headlines from the
existing phrase scorer ("down to control and economics", "five years"); an atom icon in a nature scene
(icon matching is keyword based).
