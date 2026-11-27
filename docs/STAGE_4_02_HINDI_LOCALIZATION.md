# Stage 4.02: Hindi localization and production pipeline

**Status:** implemented and tested. Results, deployment and blockers: §7–§9.
**Publishing:** disabled. A Hindi video is never published automatically. It becomes *eligible* only when the Hindi channel is ACTIVE, its identity is verified (4.01), Hindi publishing is explicitly authorized, and the video passed Hindi QC. Even then, no code path publishes it: Hindi publishing is a separate milestone.

## 1. Architecture

There is a new package, `backend/app/localization/`. It reuses the 4.01 channel registry (`content_localizations`), the 3.37 production coordinator (queue, admission and the one render slot), the Pop Info film engine, Lemo-Opuscar, the FFmpeg audio mix and colour delivery, the layout probe and the visual quality gate. It adds no scheduler, worker, renderer, queue or publisher.

```
completed English Short (media/productions/<job>/: script + pop_info_film/plan.json + licensed assets)
  │  operator: dashboard "Localize" (POST /api/localizations, CSRF + confirm)
  ▼
content_localizations row (UNIQUE source job × target channel) ──► coordinator LOCALIZATION queue
  translate  (LIGHT, 3 min)  Gemini free tier → Hindi adaptation → script QC (2 attempts per model, with feedback)
  voice      (LIGHT, 3 min)  edge-tts hi-IN Swara → Madhur → SAPI hi-IN; per scene; pronunciation map
  render     (HEAVY, 12 min) Hindi plan → Pop Info film (Nirmala UI) → Lemo-Opuscar → mix → MP4; ASS subtitles
  qc         (LIGHT, 4 min)  container/codec/A-V/volume/decode + Devanagari + layout probe + script rules
  ▼
QC_PASSED (eligible for publishing: no, unless every 4.01 condition holds) | QC_REJECTED | WAITING | FAILED
```

| File | Role |
|---|---|
| `localization/translator.py` | Request (per scene: seconds, target words, narration, headline, highlight, labels, units), prompt, model chain, retries with feedback, error classes. |
| `localization/script_qc.py` | Hindi script rules: scene ids, Devanagari share ≥ 85 %, headline ≤ 6 words, highlight inside the headline, label count, numbers (missing or invented, Devanagari digits normalised), proper-name coverage, estimated duration 27–61 s. |
| `localization/voice.py` | Per-scene synthesis with fallback, rate escalation (+8 %, +18 %, +25 %), clip and volume validation, timings. |
| `localization/render.py` | Hindi plan (text replaced, scenes retimed, SFX moved), film packaging with a Devanagari font, Lemo render, mux, ASS subtitles plus a libass render check. |
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
| Translation and adaptation | Gemini free tier via the existing key. Models: `gemini-3.7-flash`, `gemini-3.5-flash`, `gemma-4-31b-it`, `gemma-4-26b-a4b-it`. They are never the English chain's models, so Hindi never takes English production quota. Override: `CRAZZYDOCS_HINDI_MODELS`. | Already configured; no new service or cost. The prompt asks for a natural, conversational Hindi adaptation, not a word-for-word translation; facts, numbers and names are preserved, and there is a spoken-form list. | Free-tier quota (429 runs from about 01:00 to 13:30 IST on busy days). When every model is unavailable, the localization **waits** (an infrastructure condition, retried later). No paid fallback. **No offline translation is installed**: an offline model (for example IndicTrans2) would need a large download, which is an operator decision. |
| Voice | `edge-tts` `hi-IN-SwaraNeural`, then `hi-IN-MadhurNeural`, then any installed Windows SAPI `hi-IN` voice. | Free, no key. It is the same service the English pipeline already uses, so it adds no new dependency. Natural neural Hindi. | Needs the network. **No offline Hindi voice is installed** (SAPI has English voices only; no Piper or Coqui). Adding the Windows Hindi speech pack or a Piper Hindi voice is an operator decision. Without a voice, the localization waits. |
| Visuals | The English film's licensed assets and animation reused; every on-screen text replaced; the film's `engine.js` copy patched to `"Nirmala UI"` first. | Chromium shapes Devanagari with HarfBuzz; Nirmala UI is a Windows system font (no download). The English engine file is not modified. | — |
| Subtitles | ASS file (Nirmala UI, 1080×1920, ≤ 2 lines, ≤ 26 characters per line, word-safe wrap), checked by rendering a frame through FFmpeg libass (HarfBuzz, FriBiDi). | A burnable or uploadable subtitle track. Its rendering is measured, not assumed. PIL has no raqm, so it cannot shape Devanagari and is not used for Hindi text. | — |
| Rendering | Lemo-Opuscar through `lemo_adapter().render_video`, inside the coordinator's HEAVY render slot. | The existing renderer, so no second renderer is added. | One render at a time (3.37 heavy concurrency 1). |

## 3. States and failure classes

| Status | Meaning | Who acts |
|---|---|---|
| PLANNED, IN_PROGRESS | Queued or running in the LOCALIZATION queue. | Automatic |
| WAITING | Infrastructure (translation quota, voice provider, render). Retried by the coordinator (bounded attempts); completed steps are reused. | Automatic |
| QC_REJECTED | Creative: script rules, duration, non-Devanagari or clipped text. Final for these inputs. | Operator: *Retranslate* (archives the attempt) or leave it |
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

`backend/tests/test_stage_402_hindi_localization.py` (30 tests) covers:
- translation (success, feedback retry, rejection, provider unavailable, malformed JSON, model separation);
- each script-QC rule;
- voice (pronunciation, voice fallback, unavailable, rate escalation, silent clip);
- the Hindi plan and font patch (English engine unchanged);
- the real libass Devanagari render check and word-safe wrap;
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

### 7.2 Real acceptance run

TO FILL (Gemini free-tier quota resets around 13:30 IST).

## 8. Resource use and cost

**Measured during the dry-run render** (`typeperf`, 10 s samples, 11:14–11:17):

| Phase | CPU (whole machine) | RAM available (about 14 GB total) |
|---|---|---|
| Idle after the render | mean 11 %, max 19 % | about 4.0 GB |
| Render (Chromium, FFmpeg mux) | mean 63 %, max 99 % | min 2.38 GB, so the render used about 1.7 GB |
| QC (decode, volumedetect, layout probe) | mean 80 %, max 86 % | about 3.7 GB |

The coordinator's `heavy_min_ram_available_gb` (3.5 GB) is checked **at admission**. During the render, available RAM fell to 2.38 GB because the render itself uses about 1.7 GB. That is expected and safe while only one heavy job runs at a time (3.37 heavy concurrency 1). It is also why heavy concurrency must not be raised without the concurrent-render benchmark. The 12-minute HEAVY estimate is conservative: the render took about 3 minutes.

**Cost: zero.**
- Gemini free tier, with the Hindi models separate from the English chain.
- edge-tts (free, no key).
- Local Chromium and FFmpeg; the Nirmala UI system font.
- No paid API, cloud rendering, subscription or download.

## 9. Remaining blockers

| Blocker | Exact reason | Who |
|---|---|---|
| Hindi publishing | The Hindi channel is PENDING_VERIFICATION. No Hindi OAuth exists (`backend/channel_tokens/hi/token.json` absent), and `publishing_authorized` is not set. Publishing itself is a separate milestone; this stage adds no publishing path. | Operator: connect the Hindi channel's own account (Channels card), then authorize the milestone |
| Translation availability | Gemini free tier only. On days when the English pipeline exhausts its quota, the Hindi models (3.7-flash, 3.5-flash) can also be at 429 and Gemma at 503. The localization then waits and retries (bounded); it never falls back to a paid service. | Automatic; an offline translator (for example IndicTrans2) would need a large download, which is an operator decision |
| Offline voice | None installed: SAPI has English voices only, and there is no Piper or Coqui. edge-tts needs the network. | Operator decision: Windows Hindi speech pack or a Piper Hindi voice |
| Automatic Hindi enqueue | Localizations are enqueued by the operator, one English job at a time. No automatic "localize every completed Short" rule was added. | Future decision |

**Full suite at 4.02:** 1981 passed, 4 failed (the known baseline: `test_dashboard_html_page_served`, `test_end_to_end_orchestration_run`, two `test_youtube_branding`), 174 skipped. Baseline before 4.02: 1948 / 4 / 174.
