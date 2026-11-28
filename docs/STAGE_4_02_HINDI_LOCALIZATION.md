# Stage 4.02: Hindi localization and production pipeline

**Status:** **accepted** on 2026-11-27 and re-accepted under the Hinglish language policy (§0, §7.3). A real English Short (cycle_12) went through real translation, voice, rendering in the shared slot and Hindi QC, producing a valid Hindi MP4 that also passed visual inspection (§7.2). Deployment and blockers: §9.
**Publishing:** disabled. A Hindi video is never published automatically. It becomes *eligible* only when the Hindi channel is ACTIVE, its identity is verified (4.01), Hindi publishing is explicitly authorized, and the video passed Hindi QC. Even then, no code path publishes it: Hindi publishing is a separate milestone.

## 0. Language policy (follow-up, 2026-11-27 evening): natural Hinglish, English-first visual text

The target audience is Indian YouTube viewers. This supersedes the formal-Hindi/Devanagari design of the first
acceptance (§7.1–§7.2 are kept as history).

| Element | Policy | Implementation |
|---|---|---|
| Narration (spoken) | Natural Indian Hinglish: Hindi and English mixed the way a creator talks. English technical terms, names and familiar words stay English. No formal, Sanskritized or textbook Hindi, and no sentence-by-sentence translation. Same facts, numbers, hook, curiosity gaps and payoff. | `translator.build_prompt`, using the policy's own examples |
| Subtitles | Roman-script Hinglish that matches the spoken words | `narration` (Roman), written to ASS with Segoe UI (Latin) |
| Voice input | The same Hinglish sentence, with Hindi words in Devanagari and English words in Latin. A hi-IN neural voice mispronounces Roman-script Hindi ("mein" sounds like "mine") but reads this code-mixed form naturally. It is internal and **never shown**. | `narration_tts`, used only by `voice.synthesize` |
| All visible video text | English or Roman-script Hinglish only; English is the default for short labels. No Devanagari anywhere. | The Hindi film uses the **unchanged English engine**: the same typography and fonts, and the English stage pills ("THE SETUP", "THE TWIST", "THE REVEAL"). Headline, highlight, labels, units, category and title come from the adaptation in English or Roman Hinglish. |

**QC under this policy:**
- *Script QC* checks:
  - Devanagari in any visible text (subtitles, headline, highlight, labels, units, title, category) is a problem.
  - The narration must read as Hinglish: at least 12 % unambiguous everyday Hindi words, with English homographs such as "to", "is" and "the" excluded. English vocabulary is never a problem.
  - Formal words (mahatvapurn, nirantar, sanchalit, …) are flagged.
  - The voice form must carry its Hindi words in Devanagari and say the same numbers, at a similar length, as the subtitles.
  - The facts, names, highlight and length rules are unchanged.
- *Video QC* rejects Devanagari in any engine-drawn text (title, category, headline, highlight, stage label, labels, number units) and in the subtitle file, as a creative rejection.

## 1. Architecture

There is a new package, `backend/app/localization/`. It reuses the 4.01 channel registry (`content_localizations`), the 3.37 production coordinator (queue, admission and the one render slot), the Pop Info film engine, Lemo-Opuscar, the FFmpeg audio mix and colour delivery, the layout probe and the visual quality gate. It adds no scheduler, worker, renderer, queue or publisher.

```
completed English Short (media/productions/<job>/: script + pop_info_film/plan.json + licensed assets)
  │  operator: dashboard "Localize" (POST /api/localizations, CSRF + confirm)
  ▼
content_localizations row (UNIQUE source job × target channel) ──► coordinator LOCALIZATION queue
  translate  (LIGHT, 3 min)  Gemini free tier → Hindi adaptation → script QC (2 attempts per model, with feedback)
  voice      (LIGHT, 3 min)  edge-tts hi-IN Swara → Madhur → SAPI hi-IN; per scene; pronunciation map
  render     (HEAVY, 12 min) Hinglish plan → Pop Info film (unchanged English engine) → Lemo-Opuscar → mix → MP4; Roman-script ASS subtitles
  qc         (LIGHT, 4 min)  container/codec/A-V/volume/decode + no Devanagari in visible text + layout probe + script rules
  ▼
QC_PASSED (eligible for publishing: no, unless every 4.01 condition holds) | QC_REJECTED | WAITING | FAILED
```

| File | Role |
|---|---|
| `localization/translator.py` | Request (per scene: seconds, target words, narration, headline, highlight, labels, units), prompt, model chain, retries with feedback, error classes. |
| `localization/script_qc.py` | Hinglish script rules (§0): scene ids, no Devanagari in visible text, natural Hinglish (everyday-Hindi share, no formal words), voice form consistent with the subtitles, headline ≤ 6 words, highlight inside the headline, label count, numbers (missing or invented), proper-name coverage, estimated duration. |
| `localization/voice.py` | Per-scene synthesis with fallback, rate escalation (+8 %, +18 %, +25 %), clip and volume validation, timings. |
| `localization/render.py` | Hinglish plan (text replaced, scenes retimed, SFX moved), film packaging with the unchanged English engine, Lemo render, mux, Roman-script ASS subtitles plus a libass render check. |
| `localization/qc.py` | Hindi QC with CREATIVE_REJECTION versus TECHNICAL_FAILURE. |
| `localization/pipeline.py` | `HindiLocalizer` (state machine, retries, archive), coordinator executor, dashboard overview, enqueue. |
| `channels/registry.py` | Localization columns (step, attempts, error kind, artifacts, QC, owner, version), and `publishing_eligibility`. |
| `scheduler/production_coordinator.py` | LOCALIZATION queue (`localization_enabled`, own pause), built-in executor registration, active-schedule-profile restore, fail-closed admission. |
| `main.py` | `GET/POST /api/localizations`, `POST /api/localizations/{id}/retry`, `GET /api/localizations/{id}/video` (local only; writes need CSRF). |
| `templates/dashboard.html` | Queue tab → *Hindi localization* card: status, step, QC, MP4 link, publishing reasons, Retry / Retranslate. |
| `services/operator_attention.py` | Lists only genuine Hindi blockers: FAILED, QC_REJECTED, and one aggregated "passed QC, waiting for publishing authorization". |

Output files go in `media/localizations/hi/<english job>/`. They are separate from the English production, and the English files are only read. A retry from `translate` archives the previous attempt to `attempt_<n>_<ts>/` and never deletes it.

## 2. Component choices (zero cost)

| Component | Choice | Why | Limitation |
|---|---|---|---|
| Translation and adaptation | Gemini free tier via the existing key. Models: `gemini-3.7-flash`, `gemini-3.5-flash`, `gemma-4-31b-it`, `gemma-4-26b-a4b-it`. They are never the English chain's models, so Hindi never takes English production quota. Override: `CRAZZYDOCS_HINDI_MODELS`. | Already configured; no new service or cost. The prompt asks for natural Indian Hinglish (§0), not a translation; facts, numbers and names are preserved, and there is a spoken-form list. | Free-tier quota (429 runs from about 01:00 to 13:30 IST on busy days). When every model is unavailable, the localization **waits** (an infrastructure condition, retried later). No paid fallback. **No offline translation is installed**: an offline model (for example IndicTrans2) would need a large download, which is an operator decision. |
| Voice | `edge-tts` `hi-IN-SwaraNeural`, then `hi-IN-MadhurNeural`, then any installed Windows SAPI `hi-IN` voice. | Free, no key. It is the same service the English pipeline already uses, so it adds no new dependency. Natural neural Hindi. | Needs the network. **No offline Hindi voice is installed** (SAPI has English voices only; no Piper or Coqui). Adding the Windows Hindi speech pack or a Piper Hindi voice is an operator decision. Without a voice, the localization waits. |
| Visuals | The English film's licensed assets, animation and **unchanged engine** reused (typography, fonts, English stage pills); headline, labels and category in English or Roman Hinglish. | Identical visual quality to the English Shorts; nothing Devanagari-specific is needed on screen. | — |
| Subtitles | ASS file in Roman-script Hinglish (Segoe UI, 1080×1920, ≤ 2 lines, ≤ 32 characters per line, word-safe wrap), checked by rendering a frame through FFmpeg libass. | A burnable or uploadable subtitle track that matches the spoken words; its rendering is measured, not assumed. | — |
| Rendering | Lemo-Opuscar through `lemo_adapter().render_video`, inside the coordinator's HEAVY render slot. | The existing renderer, so no second renderer is added. | One render at a time (3.37 heavy concurrency 1). |

## 3. States and failure classes

| Status | Meaning | Who acts |
|---|---|---|
| PLANNED, IN_PROGRESS | Queued or running in the LOCALIZATION queue. | Automatic |
| WAITING | Infrastructure (translation quota, voice provider, render). Retried by the coordinator (bounded attempts); completed steps are reused. | Automatic |
| QC_REJECTED | Creative: script rules (including formal Hindi or Devanagari in visible text), duration, clipped text. Final for these inputs. | Operator: *Retranslate* (archives the attempt) or leave it |
| FAILED | Technical failure after its attempts, or the source is missing. | Operator: *Retry* once the cause is fixed |
| QC_PASSED | Valid Hindi MP4; eligibility reasons shown. | Publishing remains a separate milestone |

## 4. Coordination with English Shorts

- **Queue:** LOCALIZATION is its own coordinator queue, with its own switch (`localization_enabled`, default on; it runs only operator-enqueued work) and its own pause.
- **Admission:** the render step is HEAVY (estimate 12 min). It is admitted only when it fits before the next English Shorts start, and it holds the one render slot. English Shorts keep priority and preempt any work-queue holder.
- **Profile restore:** the coordinator now loads the active schedule profile in every process, so a standalone process cannot plan against the default profile. Admission **fails closed** when the next Shorts start is unknown. This was found during the 4.02 dry run; see §7.
- **English unchanged:** the English scheduler, orchestrator, Pop Info pipeline, OAuth token, publishing ledger and 24-cycle schedule are untouched. A test checks that none of them imports the localization package.

## 5. Channel isolation and publishing safeguards

- The localization package contains no publishing, upload, OAuth manager or token code (a test checks the source).
- A localization never reads the English token: the registry is built with an OAuth factory that fails if called.
- `publishing_eligibility("hi", row)` returns `eligible: false` with reasons unless the Hindi channel is ACTIVE, verified, `publishing_authorized`, and the row is QC_PASSED. `automatic_publishing` is always `false`.
- A localization can be planned while Hindi is PENDING_VERIFICATION (local production and testing are allowed); it is refused only when the Hindi channel is BLOCKED.

## 6. Tests

`backend/tests/test_stage_402_hindi_localization.py` (39 tests) covers:
- translation (success, feedback retry, rejection, provider unavailable, malformed JSON, model separation);
- each script-QC rule;
- voice (pronunciation, voice fallback, unavailable, rate escalation, silent clip);
- the Hindi plan and font patch (English engine unchanged);
- the real libass render check of Roman-script subtitles and word-safe wrap;
- QC classification of each failure;
- mapping uniqueness, a full run without publishing, retry reuse, creative rejection and archiving, a missing source, and no publishing or credential path;
- coordinator queue admission in Shorts gaps, the switch and pause, and lazy executor registration;
- the API, dashboard and attention feed;
- English backward compatibility.

The existing 3.37, 4.01 and remediation tests were updated for the new queue and attention source.

## 7. Production test results

### 7.1 Technical dry run (2026-11-27, 11:13–11:17 IST)

**Source:** `cycle_9_20261127_0800`, "Hibiscus Rising", a completed English Short.

**Translation:** a hand-written Hindi adaptation, `backend/tests/fixtures/hindi_fixture_cycle_9_20261127_0800.json` (marked TEST FIXTURE). Gemini was quota-exhausted, so this fixture exercised voice, render and QC. It passes script QC.

**Everything else is real:**
- **Voice:** edge-tts Swara at +18 %, 53.7 s.
- **Admission:** the production coordinator's HEAVY admission, read from the production database read-only: "renderer free and the step ends before the next Shorts cycle".
- **Render:** inside the production render slot, then released. It took **184 s** (package, Chromium render and mux); QC took 15 s.
- **Copies:** on scratch copies only (scratch database, a copy of the English job's files).

**Result: QC_PASSED.** h264, 1080×1920, 30 fps; AAC 48 kHz; 54.37 s; A/V drift, volume and decode all OK; every headline in Devanagari; the libass subtitle check rendered and was not clipped; no major typography issue. Frames checked visually:
- The category chip ("कला और इतिहास"), headlines, highlights and number callouts are all in Hindi.
- Conjuncts and the nukta shape correctly (हिबिस्कस राइज़िंग, लीड्स).
- The licensed photo credits are kept.

**Finding and fix (the dry run did its job):**
1. The first attempt at 10:53 IST was admitted **6 minutes before the 11:00 Shorts cycle**. The standalone process was planning against the DEFAULT schedule profile, not the operator's 24-cycle profile. The run was aborted at 10:55, and its slot was released by its exact token (audited as `SLOT_RELEASED`, actor `operator-dryrun-abort`). The 11:00 cycle leased the slot normally at 11:00:00, so production was unaffected.
2. A second attempt (11:12) found that the coordinator read Shorts demand from its own database but the schedule from the process database. Admission correctly **failed closed** ("schedule unreadable").
3. **Fix:** the coordinator now reads the active profile from its own database, read-only, and admission fails closed whenever the next Shorts start is unknown. Regression tests cover both.

### 7.2 Real acceptance run (2026-11-27, 13:32–16:40 IST): accepted

**Source:** `cycle_12_20261127_1100`, "Vasoactive intestinal peptide". A completed English Short that was not the fixture: a dense science text with 119 English words in 56.4 s.

**Path:** the real Gemini translator, then the real edge-tts voice, then a render inside the production render slot (admitted by the production coordinator against the live 24-cycle schedule, read-only), then real Hindi QC. Scratch database and media only; the English files were only read.

| Attempt (IST) | Outcome | What it found and the fix |
|---|---|---|
| 13:32 | Script QC rejection (128 words, estimated 61 s) | The estimate assumed the slowest voice rate, although +18 % speaks 128 words in about 56.6 s. **Fix (`2afb167`):** the upper bound uses the fastest natural rate; the voice still measures the real audio. |
| 13:34 | Rejection: "invented number 2"; 137 words | The English said "class II"; the Hindi correctly wrote "क्लास 2". **Fix:** Roman numerals II–XII are allowed as digits. Each scene now has a `max_words` ceiling, and the feedback names the scenes over it. |
| 13:40, 14:26, 14:30, 15:18 | WAITING: every Hindi model at 429 (free-tier rate limit) | Correct infrastructure handling: waited and retried; nothing failed or was lost. |
| 14:18 | Rendered, then **Hindi QC rejected the MP4 at 59.27 s** | The voice was 58.55 s even at +25 % (over its own 57 s limit) and was still passed on to the renderer, wasting a render. Word counts underestimate technical Hindi (long transliterated terms speak at about 2.0 words/s even at +25 %). **Fix (`8544aa6`):** the voice never returns over-length audio (`ScriptTooLong`). The voice step re-translates with the **measured** duration as feedback, at most 2 times, archiving each attempt; after that it is a creative rejection. |
| 15:36 | The automatic shortening ran; its re-translation was rejected: "name not preserved: II" | A Roman numeral was treated as a proper name. **Fix (`75c68c4`)**. |
| **16:27** | **QC_PASSED** | Gemini 3.7-flash returned 429, so 3.5-flash translated: 116 words. Swara at +25 %: 56.55 s. Render 180 s; QC 16 s. |
| — | Visual inspection of the 16:27 frames | **English stage pills ("THE SETUP", "THE TWIST")**: the engine derives them from its own English map and ignored the plan, so QC could not see them. **Fix (`75c68c4`):** the Hindi film's engine copy takes the plan's Hindi `stage_label` (packaging fails loudly if that engine line changes), and QC checks stage labels and labels for Devanagari. |
| **16:36** | **Re-render QC_PASSED** (the accepted output) | Explicit `reopen(from_step="render")`. The 16:27 output is archived in `attempt_5_20261127T110653/`, not deleted. The accepted translation and voice were reused (0 s). The coordinator admitted it ("ends before the next Shorts cycle"): render 191 s, finished about 16:40, before the 17:00 cycle. |

**Final MP4** `hindi_cycle_12_20261127_1100.mp4` (14.8 MB), checked independently with ffprobe and ffmpeg:
- Video: h264 High, yuv420p, 1080×1920, 30/1 fps, 1718 frames, 57.267 s.
- Audio: AAC LC, 48 kHz stereo, 57.259 s, so **A/V drift is 0.008 s**.
- No decode errors. Mean −17.0 dB, peak −4.7 dB, integrated −14.2 LUFS.
- Hindi QC: PASSED with no creative or technical findings.

**Visual inspection** (16 frames across every scene):
- The category chip (रोजमर्रा का विज्ञान), every headline and highlight (वैसोएक्टिव इंटेस्टाइनल पेप्टाइड, 28 अमीनो एसिड, क्लास 2 जी प्रोटीन, दिमाग का हाइपोथैलेमस, …) and the diagram labels (उत्तेजित करता है, संकुचनशीलता, वैसोडिलेशन) are in Devanagari.
- The stage pills are शुरुआत and ट्विस्ट; the diagram, card and ending templates draw no pill.
- The number callout counts up to 28.
- Conjuncts shape correctly. **No English text appears anywhere.**

**A network event the same day:** at 17:08:14–17:08:24 IST, DNS resolution failed for about 10 s (`getaddrinfo failed`, Errno 11001, reported by Node clients as ENOTFOUND). The English 17:00 cycle's first narration attempt failed on edge-tts. The scheduler's existing retry policy recovered it (COMPLETED 17:18, uploaded privately as usual). DNS resolves normally afterwards. This was a transient local network fault, not an application or provider-configuration fault, and nothing was changed.

### 7.3 Hinglish reprocess under the language policy (2026-11-27, 18:26–18:40 IST): accepted

The accepted formal-Hindi output was archived (`attempt_5_20261127T125625/`, never deleted) and the same English Short
was reprocessed under §0. A new translation and voice were **necessary**: the stored ones were formal Devanagari Hindi,
which the policy replaces. 18:26: every Hindi model was at 429 (it WAITED). 18:36: Gemini 3.7-flash returned 503, so
3.5-flash adapted it; the voice took 64 s; the coordinator admitted the render ("ends before the next Shorts cycle"):
render 162 s, finished about 18:40, before the 19:00 cycle; QC 16 s.

**Script (excerpt):**
- *Subtitles / narration:* "Vasoactive intestinal peptide, jise vasoactive intestinal polypeptide ya VIP kehte hain," · "gut samet vertebrates ke kaafi tissues VIP banate hain," · "Aisi aur real-world stories ke liye CrazzyDocs ko subscribe karein."
- *Voice form:* "gut समेत vertebrates के काफी tissues VIP बनाते हैं,"
- 113 voice words; Swara at +25 %, 56.72 s. Technical terms stay English.

**Visible text** (one frame per scene, inspected): English only, in the English Shorts' own typography:
- the "EVERYDAY SCIENCE" chip and every headline and highlight ("Vasoactive intestinal peptide", "28 amino acid", "class II G protein", "hypothalamus in the brain", "causes vasodilation", "VIP gene");
- the stage pills "THE SETUP" and "THE TWIST";
- the diagram labels (Stimulates, Contractility, Vasodilation) and the 28 callout.

**No Devanagari anywhere on screen.** Subtitles: 18 events in Roman-script Hinglish (Segoe UI, two lines at most), timed per scene from the voice, with no Devanagari; the libass check frame is clean.

**Final MP4** (ffprobe and ffmpeg):
- Video: h264, 1080×1920, 30 fps, 1723 frames, 57.433 s.
- Audio: AAC 48 kHz stereo, 57.429 s, so **A/V drift is 0.004 s**.
- No decode errors; mean −17.0 dB, integrated −14.2 LUFS.
- **Hindi QC: PASSED**, with no creative or technical findings.

## 8. Resource use and cost

**Measured during the dry-run render** (`typeperf`, 10 s samples, 11:14–11:17):

| Phase | CPU (whole machine) | RAM available (about 14 GB total) |
|---|---|---|
| Idle after the render | mean 11 %, max 19 % | about 4.0 GB |
| Render (Chromium, FFmpeg mux) | mean 63 %, max 99 % | min 2.38 GB, so the render used about 1.7 GB |
| QC (decode, volumedetect, layout probe) | mean 80 %, max 86 % | about 3.7 GB |

The coordinator's `heavy_min_ram_available_gb` (3.5 GB) is checked **at admission**. During the render, available RAM fell to 2.38 GB because the render itself uses about 1.7 GB. That is expected and safe while only one heavy job runs at a time (3.37 heavy concurrency 1). It is also why heavy concurrency must not be raised without the concurrent-render benchmark. The 12-minute HEAVY estimate is conservative: the render took about 3 minutes.

**Acceptance-run timings:**
- Translation: 28–138 s per call chain (Gemini latency varies; a 429 moves on to the next model).
- Voice: 53–62 s, or about 240 s with one automatic shortening.
- Render: 180–191 s inside the slot. Hindi QC: 16–17 s.

The CPU and RAM profile of a render is the dry-run measurement above: render CPU mean 63 % / max 99 %, render RAM about 1.7 GB. The acceptance renders use the same engine and film size; they were not sampled again.

**Cost: zero.**
- Gemini free tier, with the Hindi models separate from the English chain.
- edge-tts (free, no key).
- Local Chromium and FFmpeg; Windows system fonts.
- No paid API, cloud rendering, subscription or download.

## 9. Remaining blockers

| Blocker | Exact reason | Who |
|---|---|---|
| Hindi publishing | The Hindi channel is PENDING_VERIFICATION. No Hindi OAuth exists (`backend/channel_tokens/hi/token.json` absent), and `publishing_authorized` is not set. Publishing itself is a separate milestone; this stage adds no publishing path. | Operator: connect the Hindi channel's own account (Channels card), then authorize the milestone |
| Translation availability | Gemini free tier only. On days when the English pipeline exhausts its quota, the Hindi models (3.7-flash, 3.5-flash) can also be at 429 and Gemma at 503. The localization then waits and retries (bounded); it never falls back to a paid service. | Automatic; an offline translator (for example IndicTrans2) would need a large download, which is an operator decision |
| Offline voice | None installed: SAPI has English voices only, and there is no Piper or Coqui. edge-tts needs the network. | Operator decision: Windows Hindi speech pack or a Piper Hindi voice |
| Dense technical sources | Long transliterated terms speak slowly, so a word estimate cannot guarantee the length. The voice step measures it and shortens automatically (at most 2 times); a source that still does not fit is a creative rejection, shown once in Needs your attention. | Automatic; rare cases are a human edit |
| Automatic Hindi enqueue | Localizations are enqueued by the operator, one English job at a time. No automatic "localize every completed Short" rule was added. | Future decision |

**Full suite** (Hinglish policy): **2007 passed, 4 failed (the same known baseline), 174 skipped**; 39 focused 4.02 tests. Earlier, at `75c68c4` with the operator-dependency reduction: **2005 passed, 4 failed (the known baseline: `test_dashboard_html_page_served`, `test_end_to_end_orchestration_run`, two `test_youtube_branding`), 174 skipped.** The baseline before 4.02 was 1948 / 4 / 174. Earlier run at `1a11ab7`: 1981 / 4 / 174.

**Commits:** `1a11ab7` (stage), `33b392c` (queue display), `2afb167`, `8544aa6` and `75c68c4` (acceptance-run fixes). Deployment status is in the handover §3.

## 10. Follow-up (2026-11-27 evening): quota resilience, Hindi publishing queue, Shorts thumbnails, channels

**Gemini quota.** Live evidence: the automatic localizations of `cycle_22` and `cycle_21` failed after 3 attempts. Gemini 3.7/3.5 Flash answered 429 (free-tier quota). The Gemma fallbacks in the same free chain answered 503/504 (overloaded). Each failed run still consumed one attempt.
- Fix (`localization/translator.py`, `pipeline.py`; coordinator `StepDeferred`):
  - A model that answers 429 is not called again until the provider's own reset: midnight Pacific (13:35 IST) for a per-day quota, otherwise its `retryDelay`.
  - An overloaded model cools down for 10 min.
  - The localization waits as `WAITING`/`PROVIDER_WAIT`. The coordinator job stays QUEUED with `not_before_utc` and keeps its attempts. Waiting is bounded to 26 h per deferral and 48 h overall.
- Gemma answers are accepted only when they pass Hindi script QC. No paid or offline translator exists.

**Hindi publishing queue** (`localization/publishing.py`, table `hindi_publications`, history `hindi_publication_events`, dashboard card "Hindi publishing queue", API `/api/hindi-publishing`):
- Holds only QC_PASSED Hindi localizations whose MP4 is under `media/localizations/hi/`, one row per localization.
- Metadata is derived from the English metadata and is editable.
- Slots come from the Hindi schedule: daily limit, one video per slot, at least 20 min ahead.
- Uploads use only the Hindi token, after a `channels.list?mine=true` identity check against the verified Hindi id. Videos are uploaded private with `publishAt`.
- An interrupted upload becomes NEEDS_CHECK. Its retry adopts the existing video instead of uploading again.

**Hindi schedule.** Stored in `channel_registry.publishing_config_json` (Hindi row, key `schedule`): `enabled` (default **false**), `times` (Asia/Kolkata), `daily_limit`, `max_attempts`.
- It runs every 5 min from the daemon's recovery loop (`daemon_runtime.hindi_publishing_tick`), in a worker thread that is never awaited. The coordinator and the English scheduler never call it.
- It uploads only when the Hindi channel is ACTIVE and verified, `publishing_authorized` is true (operator authorization, never set by code) and the schedule is enabled.
- The English 24-cycle schedule is untouched.

**Shorts thumbnails.** Measured on a live Short (`eJKaDDIEAoc`):
- `thumbnails.set` with a 9:16 image is accepted, but only the 16:9 sizes change (pillarboxed). The Shorts-feed `oar2` frame does not change, so a raw portrait upload only degrades the 16:9 surfaces.
- When a thumbnail is set after processing SUCCEEDED, YouTube adds a `shortsAspectRatio` thumbnail (`sardefault.jpg`, 405×720): the centre 9:16 crop of the uploaded image. Thumbnails set right after `videos.insert` (the previous flow) never got it.
- Of 161 recorded Shorts thumbnails, 155 were marked "verification failed" only because the check ran while the video was still private (CDN 404). They verify once public.
- Fix (`services/thumbnail_pipeline.py`, `youtube_publishing_engine.py`):
  - The Shorts artifact is 1280×720 with the full portrait design in the centre 405×720 column.
  - It is set only after processing SUCCEEDED (`awaiting_processing` until then).
  - Verification requires YouTube's `shortsAspectRatio` to match the portrait.
  - A private or scheduled video is never counted as failed.
- Long-form keeps the 16:9 composition.
- Backfill: at most 2 per 5-min reconciliation and 60 per Pacific day, always keeping 3,000 general units free. Portrait-safe thumbnails are never replaced.

**Channels.** Activation and deactivation are one confirmed action. The operator and reason fields were removed from the dashboard, the API (`ChannelChangeRequest` accepts only `confirm`) and the registry. Older records are kept in the database but never displayed. CSRF, origin, local-control and channel-state validation are unchanged.

## 11. Hindi Target Publish Time per production cycle (2026-11-28)

**UI.** The Production Schedule table has a **Hindi Target Publish Time** column beside the English Target Publish Time.
- Each cycle has a **Same as English** toggle (on by default) and a time picker, enabled when the toggle is off.
- **Save Hindi Times** saves only this column, immediately. It is separate from Save Draft / Activate, so the English schedule's draft, revision and target times are never changed.
- The Hindi publishing card now shows the strategy and keeps the enabled switch, daily limit (1–24) and max attempts. The single daily-time field is gone.

**Data model.** The Hindi channel's `publishing_config_json.schedule`:
```
{"strategy": "MANUAL", "enabled": false, "timezone": "Asia/Kolkata", "daily_limit": 24, "max_attempts": 3,
 "cycles": {"cycle_20": {"same_as_english": false, "time": "22:15"}, ...}}
```
- A cycle missing from `cycles` follows English, so cycles added later default to Same as English.
- The legacy `times` key is ignored.
- API: `GET/POST /api/hindi-publishing/cycle-times`. `POST /api/hindi-publishing/schedule` merges, so it never drops the cycle times.

**Scheduling.**
- When a Hindi localization passes QC it enters the queue and is planned at once by the channel's strategy. `MANUAL` uses its cycle's Hindi time, or the English target when Same as English is on.
- The time is the first occurrence at or after the English production start (a 23:00 cycle with a 00:00 target means the next day) that is also at least 20 min from now. A late localization therefore gets the next day's occurrence.
- Each video stores one individual `scheduled_at_utc`.
- A scheduled time that passes without an upload (publishing not authorized, schedule off, late) is re-planned to the next occurrence. Nothing is ever published immediately, and an upload never uses a publishAt less than 15 min ahead.
- Uploads still need Hindi QC, the Hindi schedule enabled and `publishing_authorized`.

**Future analytical scheduling.**
- `PLANNERS` maps a strategy name to a function returning an individual timestamp per video, and the queue already stores per-video timestamps.
- `schedule_item(pid, at_utc)` accepts any individual time (at least 20 min ahead), which is what an analytical planner would supply.
- Each channel has its own `strategy`. English keeps its existing per-cycle targets, and its own strategy field can be added the same way when the analytical engine arrives.
- `ANALYTICAL` is reserved and rejected for now.

**Limits.**
- Videos already planned keep their time when English times change later; only new or missed ones use the new times.
- Up to `daily_limit` Hindi videos per IST day; beyond that, the next day.

## 12. Status check and unified publishing queue (2026-11-28, about 04:00 IST)

### 12.1 Actual status (production database, read-only)

| Item | State |
|---|---|
| §7.2 / §7.3 acceptance runs | Passed Hindi QC, but in a **scratch database and scratch media**. They prove the pipeline end to end; they are not a production record. |
| Production localizations (`content_localizations`) | #1–#4 (`cycle_22`, `cycle_21`, `cycle_20`, `cycle_23` of 2026-11-27): **FAILED**, "infrastructure failures exhausted the coordinator attempts". They ran before the quota-resilience fix (§10) and were kept unchanged. #5 (`cycle_24_20261127_2300`): **WAITING** (`PROVIDER_WAIT`), 0 attempts used. It waits for the Gemini free-tier reset (13:35 IST), when the coordinator resumes it automatically. |
| Hindi videos that passed QC in production | **None yet**, so `hindi_publications` is empty. |
| **Stage 4.02** | **Complete (production acceptance 2026-11-28 14:17 IST, §12.3).** At the time of this check it was not complete; the condition was a production localization reaching QC_PASSED plus a visual check of its frames. Nothing was forced. |
| Stage 4.03 | **Not defined** anywhere in the repository (docs, code or git history). Nothing was started or labelled 4.03. The unified queue below is a 4.02 publishing follow-up. |

### 12.3 Production acceptance (2026-11-28): complete

**Record.** Production localization #5 (`cycle_24_20261127_2300`, "Water on Mars") waited in PROVIDER_WAIT (every Hindi model on free-tier quota) from the morning until the Gemini reset, then ran voice → render → QC and reached **QC_PASSED at 14:17 IST** with 0 coordinator attempts used. #6 (`cycle_10_20261128_0900`) also reached QC_PASSED. #1–#4 stay FAILED and were not retried. The re-run followed the fixes `a5b9710` (translator feedback / proper-name QC) and `c8bda38` (re-run id).

**Hindi QC (`qc_json`):** passed, classification PASSED, no creative or technical problems; 49.96 s.

**Independent verification (read-only, 14:20 IST):**
- ffprobe: h264 1080×1920, 30/1 fps, 1498 frames, 49.93 s; AAC 48 kHz stereo, 49.96 s; 18.1 MB. Audio mean −16.9 dB, max −4.6 dB; no silent gap ≥ 1.5 s.
- Voice: edge-tts `hi-IN-SwaraNeural`, 9 scene clips, 49.24 s; voice form in code-mixed Devanagari + Latin (internal, never shown), e.g. "Mars के surface पर थोड़ा liquid water कभी मिलता है,".
- Subtitles: 14 ASS events in Roman-script Hinglish ("Mars ke surface par thoda liquid water kabhi milta hai,"), last event ends 49.34 s; **no Devanagari**.
- Frames at 1, 8, 16, 26, 36 and 47 s, inspected: "Water on Mars" with the SPACE AND ASTRONOMY chip, "Traces of Moisture", "Underground Water" (THE SETUP), "Deep Subsurface Ice" (THE TWIST), "Earth-based Telescope" with the 1963 marker, and the "Subscribe to CrazzyDocs" outro. English on-screen text in the English engine's typography, as §0 requires; no Devanagari, no clipped or overlapping text, no blank frame.

**Publishing.** The Hindi queue planned both videos (#5 publication 8 at 2026-11-29 00:00 IST, #6 publication 1 at 2026-11-29 10:00 IST) as SCHEDULED, but the gate reports `can_publish: false` ("Hindi publishing is not authorized"), so nothing uploads. Hindi publishing still needs separate operator authorization.

### 12.2 One YouTube Publishing Queue

The Publishing Queue tab now holds a single card:
- **Filters:** All, English and Hindi; the default is All.
- **Rows:** one per video, ordered by scheduled publication time: upcoming first (soonest first), then past publications (most recent first), then unscheduled.
- **Columns:** Video, Channel (ENGLISH or HINDI badge), QC Status, Thumbnail, Scheduled Time, YouTube Status, Actions.

**Data source.** `GET /api/publishing/unified?channel=all|en|hi` (`services/unified_publishing_queue.py`) only merges the two existing queues; it uploads, schedules and authorizes nothing.

**Actions on English rows** use the existing English endpoints (`/api/publishing/queue/{id}/…`, English token):
- Edit and Schedule, or Reschedule. This is the existing modal, with title, description, tags, hashtags, an IST date and time, and a custom thumbnail upload.
- Unschedule.
- Publish now, with the existing confirmation.
- Retry for a Failed row, which reopens the schedule modal.
- View on YouTube.

**Actions on Hindi rows** use `/api/hindi-publishing/{id}/…` (Hindi token, identity check, Hindi gates):
- Edit metadata.
- Schedule or Reschedule, either at a chosen IST time or at the cycle's Hindi target time.
- Unschedule.
- Retry.
- View on YouTube.
- **Publish now** schedules the video 21 minutes ahead. That is the Hindi minimum lead of 20 minutes; the video is never public immediately. It uploads only while the Hindi channel is verified and active, publishing is authorized and the schedule is enabled.
- The thumbnail cannot be edited: it is the English Short's thumbnail, set after YouTube processing.

**Hindi rows without a pass.** A Hindi row whose localization has not passed Hindi QC has no action. The backend also refuses: `sync()` adds only QC_PASSED localizations whose MP4 is under `media/localizations/hi`, and `schedule_item()` re-checks QC_PASSED.

**New Channels & Localization tab.** It now holds:
- Channels (verification and authorization, with history);
- Hindi localization;
- Hindi publishing settings (gates, the schedule switch, the daily limit, max attempts and the Hindi publication history);
- Shorts thumbnail diagnostics.

Nothing was removed.

**Not changed:**
- The English and Hindi OAuth tokens, upload clients, identity checks and publishing settings.
- The duplicate-upload prevention (UNIQUE localization and video ids; claim-by-state).
- Retry and recovery.
- The 24-cycle schedule and every existing scheduled video.
