# Stage 3.32E: Architecture

**Purpose:** generate one YouTube Community post **draft** per day (text and one image) from CrazzyDocs' own research, validate it, and hold it for operator review.

**Nothing in this stage publishes.** "Approved" records the operator's decision on the draft. Posting is done by hand in YouTube Studio until Stage 3.32F.

## 1. Components

```
existing scheduler daemon (APScheduler)          dashboard (FastAPI)
  job community_draft_tick, every 5 min            /api/community/*  (loopback + CSRF writes)
            │                                                │
            └──────────────► CommunityService ◄──────────────┘
                              (backend/app/community/service.py)
               ┌──────────────┬───────────┴────────┬────────────────────┐
         content.py       images.py          validation.py          store.py
         sources, topic,  licensed-asset     text + image checks    community_* tables
         format, text     card / title card  → VALID … REJECTED     (studio.db)
               │                │
   read-only:  persistent_jobs, publishing_ledger, topic_candidate_ledger,
               content_ledger, creative_identity_registry,
               media/productions/<job>/research/*.json, metadata/attribution_manifest_*.json,
               assets/acquired/*
```

| Module | Responsibility |
|---|---|
| `community/store.py` | Six `community_*` tables, the lifecycle transition table, atomic compare-and-set transitions, retention. Reads never create tables. |
| `community/content.py` | Read-only sources; channel-relevance filter; topic, format, opening, question and call-to-action rotation; 8 template formats; optional Gemini wording. |
| `community/images.py` | Licence-verified reuse of production assets (SHA-256 matched to the attribution manifest); 1080 × 1080 cover crop; local typographic title card; difference hash. |
| `community/validation.py` | Independent text and image checks, combined into VALID / VALID_WITH_WARNINGS / NEEDS_OPERATOR_REVIEW / REJECTED. |
| `community/service.py` | Generation pipeline, lifecycle actions, schedule, idempotency, limits, kill switches, crash recovery, notifications, retention. |
| `scheduler/scheduler.py` | One added job, `community_draft_tick`, which calls `service.scheduler_tick()`. |
| `community/schedule.py` | IANA timezone list and presets, DST-aware window resolution, and the scheduling decision (`decide`) used by both the tick and the status API. |
| `main.py` | 18 `/api/community` routes (see `STAGE_3_32E_API.md`). |
| `templates/dashboard.html` | The Community Posts tab. |

## 2. Reused components (no duplicates)

| Need | Reused | Not built |
|---|---|---|
| Topics | Stage 3.31 `topic_candidate_ledger` (ELIGIBLE), completed productions, research dossiers | A topic discovery engine |
| Facts and grounding | Research dossiers; `number_words.unsupported_claims`; `hook_evaluator` editorial patterns | A fact sourcer |
| Channel identity | Categories in `content_ledger` and `creative_identity_registry` | New category lists |
| Images | Licensed assets already acquired per production, their attribution manifests, Pillow | Downloads, paid APIs, GPU models, the video renderer |
| Text model (optional) | The existing Gemini SDK and key (`GEMINI_API_KEY` / `GEMINI_MODEL`) | A new LLM provider |
| Scheduling | The daemon's existing APScheduler instance (same pattern as the daily summary, analytics and junk-cleaner jobs) | A second scheduler |
| Notifications | The 3.32B operator inbox through `ControlPlane.submit("operator.notify")`, gated like 3.32D | A notification system |
| Operator identity | `control_plane.validate_operator` (reserved machine identities refused) | New auth |
| API security | `require_local_control`, `require_autonomy_write` (same-origin, JSON, CSRF) | New guards |
| Database | `studio.db`, the same SQLite conventions as 3.32D (`BEGIN IMMEDIATE`, busy timeout, read-only probes) | A new database |

## 3. Why the draft pipeline does not run through the Control Plane

- **Independence is required.** Stage 3.32E must work, and stay safe, while 3.32B and 3.32D are fully disabled (today the startup kill switch is active and all levels are OFF).
- **Its own safety controls are equivalent for a local, non-publishing action:**
  - an enable flag that is OFF by default;
  - a pause;
  - its own kill switch (dashboard switch and sentinel file);
  - the global `AUTONOMY_DISABLED` file;
  - limits, a deadline and audit history.
- **The existing registry entry stays.** `community.draft.prepare` (3.32B, explicit approval each time) remains for the autonomy engine.
- **Publishing stays prohibited at the registry level:** `youtube.community.publish` is a PROHIBITED action.
- **Notifications** do use the Control Plane, and only when its `notifications` level allows.

## 4. Independence from video production

- **Writes go only to:**
  - the `community_*` tables;
  - `backend/data/community/images/<draft id>/`.
- **Production data is read only**, through `mode=ro` connections and bounded file reads.
- **Not touched:** jobs, cycles, recovery windows, retry policy, the production schedule (its revision is computed from cycles only), publishing records, OAuth tokens or media.
- **No coupling to production timing.**
  - Video failures do not block drafts, apart from leaving fewer topics.
  - Draft failures cannot block, delay or retry video work.
  - The tick never raises into the scheduler.
- **No publish path:** a static test proves that the package imports no YouTube, Google API, OAuth or upload module.

## 5. Image strategy (zero cost, local)

1. **Licensed production asset.** A file in `media/productions/<job>/assets/acquired/` qualifies only when its SHA-256 equals a `verified` manifest entry:
   - the licence is accepted (CC0, Public Domain or CC BY);
   - modification is allowed;
   - commercial use is not excluded.

   It is cover-cropped to **1080 × 1080** JPEG with **no text added**. Assets used by another draft in the last 180 days are skipped. CC BY adds a credit line to the post text.
2. **Typographic title card**, rendered locally with Pillow. It shows the topic title and category from the dossier on a gradient: own work, no third-party imagery.
3. **If neither can be produced**, the draft becomes `IMAGE_FAILED` and a notification is sent. An unrelated image is never attached.

Measured on this laptop: about 110–220 ms per image warm, no GPU. Pillow's global decompression limit is not changed, because the renderer shares the daemon process.

## 6. Data flow of one generation

```
claim (run row, UNIQUE key)  →  insert draft GENERATING (UNIQUE active per date)
→ select source + format, compose text            → IMAGE_PENDING
→ licensed card or title card (+ credit line)     → VALIDATING
→ text + image validation                         → READY_FOR_REVIEW | VALIDATION_FAILED
→ finish run (SUCCEEDED / FAILED, metrics)        → notify (ready / failed)
```

The deadline (120 s) is checked between stages. Any exception moves the draft to the failed state that matches the stage it was in.
