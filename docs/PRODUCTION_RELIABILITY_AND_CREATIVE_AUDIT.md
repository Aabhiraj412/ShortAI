# Production Reliability & Creative Quality Audit (2026-11-13)

Status words used below:

| Word | Meaning |
|---|---|
| **implemented** | The code exists. |
| **tested** | Covered by automated tests on temporary databases. |
| **deployed** | Running in the live daemon and dashboard. |
| **real-world verified** | Observed working in a live production. |

## Phase 0 — Preflight (19:38 IST)

**Git:**
- `main` = `origin/main` = `bb8e01c`, which was pushed earlier today.
- The working branch `claude/scheduler-redesign-render-fix` is at the same commit.
- Untracked files only in the main checkout (`backend/backups/`, `backups/`, `media/`); none are touched.

**Processes and jobs:**

| Process | PID |
|---|---|
| Monitor | 7728 |
| Dashboard | 8532 |
| Daemon | 7696 |

- Code revision `81b1078682e7dd84`; deployment status OK.
- No active job.
- Schedule revision `d3550fbd996b`.
- Next cycle is `cycle_1` at 00:00 IST.

**Recent jobs:**
- `cycle_1`, `cycle_2`, `cycle_3` and `cycle_4` of 2026-11-13 are FAILED or EXPIRED.
- The last completed production is `cycle_3_20261112_1200` ("SoHo Weekly News"), published as #284.

**Backups:** the latest is `D:\Agents\CrazzyDocs_backups\pre_youtube_connect_20261113_192017`. A new one is taken before this deploy.

## Phase 1 — Reliability findings

| Stage | Finding | State |
|---|---|---|
| Topic selection | A failed production never reached the TopicLedger, so the same topic was re-selected every cycle (Phase 2). | **Fixed**: implemented and tested; deploy described below. |
| Render (Lemo) | Cycles 2 and 3 failed with `window.READY never became true`. Their film pages predate `c8d1c68` and contain no READY signal at all. | Already fixed by `c8d1c68`. Real-world verified: cycle 4's page (new builder) rendered a valid 54.38 s video. |
| Post-render | `prod_ws.save_manifest` was missing and crashed cycle 4 (see [CYCLE_4_FAILURE_ANALYSIS.md](CYCLE_4_FAILURE_ANALYSIS.md)). | Fixed in `bb8e01c`, plus a permanent AST guard over the orchestrator. Deployed; awaiting the first real run past the render. |
| QC → upload → thumbnail → schedule → remote verify | Never reached in production since 2026-11-12, because every cycle failed earlier. | **Not real-world verified.** The mocked tests from [STAGE_3_22_READINESS_AUDIT.md](STAGE_3_22_READINESS_AUDIT.md) pass. |
| Notifications | The audit gaps (missing alert types, email publishing-section bug) still stand. | Open. |
| Reconciler | Touches live RUNNING jobs on its "within window" branch. The worker registry makes this harmless. | Open, low risk. |

The orchestrator still runs as one 1,700-line function that records checkpoints. A single authoritative lifecycle model (Phase 1.2) remains a design task; nothing was changed there in this pass.

## Phase 2 — Root cause of repeated identical videos

### Evidence (read-only)

Six of seven production workspaces contain the **same script, byte for byte**: md5 `90f019b2…`, topic "Science fiction".

| Job | Outcome |
|---|---|
| `cycle_4_20261112_1730` | Expired |
| `cycle_4_20261112_1800` | Failed |
| `cycle_1_20261113_0000` | Expired |
| `cycle_2_20261113_0600` | Failed (READY) |
| `cycle_3_20261113_1200` | Failed (READY) |
| `cycle_4_20261113_1800` | Failed (save_manifest) |

- The procedural animations were also identical, with the same md5s across workspaces.
- The daemon log shows "Selected topic: 'Science fiction' (Composite Score: 9.24)" six times, with the same score each time.
- The "script" is the Wikipedia lead paragraph, nearly verbatim. It was discovered through `wikipedia_search:nature_and_wildlife`.

### Mechanism

1. Live discovery and scoring are deterministic. The same feeds give the same top candidate.
2. Production writes to `content_ledger` only after a **successful** render (`record_content`, after Stage 12). A failed production leaves no trace, so the duplicate gate never sees the topic.
3. `compose_structured_script` is deterministic for a given topic, so the same topic gives the same script.
4. A second repeat exists in the ledger: "The Flight Attendant Who Fell 33,000 Feet…" was produced twice on 2026-11-10. One copy was deleted from YouTube, according to `deleted_video_audit`.

### "Grab Attention"

No database, backup database, log, manifest, script or code path contains "Grab Attention". It may be a title you saw on YouTube Studio for one of the videos above. If you can give a video ID, it can be matched against the publishing ledger and `deleted_video_audit`. The mechanism above explains the frame-identical repeats either way.

### Fix (implemented and tested)

**New module:** `backend/app/content/creative_identity_registry.py`, a `creative_identity_registry` table in `studio.db`.

**What it records:** normalized topic, category, angle (hook approach), hook, script, storyboard, asset fingerprints, visual-sequence fingerprint, renderer config, final video SHA-256 and status. It is keyed by job, so it survives deleted files, restarts and new cycles.

**Orchestrator wiring, PRODUCTION only:**
1. Ranked topics are filtered against **every earlier attempt**, failed ones included, for 30 days.
2. The chosen topic is reserved **before** any later stage can fail.
3. An identical script from any other job within 365 days raises `DuplicateCreativeIdentity`. Its message carries "integrity gate", so the error is classified PERMANENT and is not retried.
4. The final video SHA is recorded.

**Backfill:** on first use, the registry imports `persistent_jobs` (all attempts) and `content_ledger`, including script fingerprints from workspace files. It is idempotent and never overwrites a row.

**Similarity levels kept separate:**
- A repeated subject or identical script blocks the production.
- Storyboard, asset and video fingerprints are recorded for reports only. A reused stock asset is not treated as a duplicate.

**Probe run on a copy of the live database (not the live database):**
- The backfill imported 17 historical productions.
- The current discovery pool has 7 candidates; none conflicts.
- The next pick would be "Breakthrough Prize in Fundamental Physics".

### Not done yet (Phase 2.4 and 2.5)

- Topic-angle diversification.
- An LLM-written script instead of the Wikipedia lead. The next topic's script will still read like an encyclopedia lead. **This is the biggest remaining creative-quality problem.**
- Controlled variation of hook structure, composition and motion.

## Phase 3 — Pop Info audit and History/Archive

### Pop Info reference (`docs/reference_styles/pop_info.html`)

- **Rendering:** a single canvas with bespoke illustrated scenes drawn per story (room, shelf, shopper character with blinks and wobble, speech bubbles, wipe transitions).
- **Typography:** Anton display, Inter body, Instrument Serif accents.
- **Palette:** warm cream and ink (`#FBF8EE`, `#FFD86B`, `#E85D4A`, `#E07A5F`, `#F2CC8F`, `#0b0b0d`).
- **Sound:** procedural sound design with music ducking under the voice.

### Current production film

- **Media:** a stock photo or clip layer with a Segoe UI caption box.
- **On-screen text:** callouts are chunks of narration (for example "OR ABBREVIATED SF IS THE"), not designed information.
- **Gap:** large. Matching Pop Info means generating illustrated scenes from a structured storyboard, not restyling captions. That is a dedicated milestone.

### Fixed in this pass (implemented and tested)

- The film page no longer draws internal scaffolding on the viewer's frame:
  - the "LEMO-OPUSCAR STUDIO" footer;
  - the "BEAT nn / nn" counter;
  - "DOCUMENTARY" / "DOCUMENTARY BRIEF" fallbacks.
- The style selector gives Pop Info a primary-style bonus (`PRIMARY_STYLE_BONUS = 0.35`).

### History/Archive deactivation (implemented and tested)

There are no History or Archive reference files in `docs/reference_styles/`; the four files present are Pop Info, Data Story, Chalk Talk and Creator Motion Graphics. These looks are mapped to:

| Look | Mapped to |
|---|---|
| History | `ApprovedStyle.DOCUMENTARY` (ledger label `historical_documentary`, layouts `full_bleed_evidence` and `archival_caption`). This is what "SoHo Weekly News" and the hook beat of every Science fiction storyboard were rendered in. |
| Archive | `CreativeDirection.ARCHIVE_EDITORIAL_MONTAGE`. |

**Changes:**
- `AdaptiveVisualStyleSelector` never returns a deactivated style, and they are left out of its reported scores.
- The storyboard builder refuses a deactivated style or direction outright, so neither can reach a render silently.
- Enum values are kept so existing storyboards and manifests still parse.
- No production history, video or artifact was deleted.

**Left alone on purpose:** "History" as a **topic category** (for example "Strange but True History") is unchanged. If you also want history *topics* retired, that is a separate decision.

## Phases 4–6

Not started, as instructed:
- Topic-discovery scoring and source verification beyond the current engine.
- The quality score.
- The analytics schema.

Autonomous learning remains off.

## Tests

- `backend/tests/test_creative_identity_registry.py` (35 tests):
  - failed attempts block the subject;
  - resume of the same job;
  - cooldown expiry;
  - script collision across titles;
  - survival of deleted files and restarts;
  - stock-asset reuse is not a duplicate;
  - backfill and idempotency;
  - PERMANENT classification;
  - the registry is off in tests and validation and never touches the live database under pytest;
  - gate ordering before the render;
  - the History/Archive style is never selected (20 combinations);
  - Pop Info wins neutral beats;
  - builder refusal.
- `backend/tests/test_film_page_viewer_chrome.py` (3 tests).
