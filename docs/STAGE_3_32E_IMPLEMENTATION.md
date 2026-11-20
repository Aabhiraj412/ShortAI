# Stage 3.32E: YouTube Community Post Draft Automation (Implementation)

**Status:** implemented and tested in worktree `crazzy-docs-handover-b1a088` (branch `claude/crazzy-docs-handover-b1a088`, base `687c156`).

It is **not committed, not deployed and not activated.** Automatic generation is OFF until an operator enables it.

Related documents:
- `STAGE_3_32E_ARCHITECTURE.md`
- `STAGE_3_32E_CONTENT_POLICY.md`
- `STAGE_3_32E_SCHEDULING.md`
- `STAGE_3_32E_API.md`
- `STAGE_3_32E_TEST_RESULTS.md`

## 1. What it does

1. Once a day (default **10:00 Asia/Kolkata**), or on demand, it picks a relevant, unused topic from CrazzyDocs' own research.
2. It writes a post in one of eight rotating formats.
3. It attaches one licence-verified image, or a locally generated title card.
4. It validates text and image.
5. It stores the result as a draft for review.
6. The operator can preview, edit, regenerate text or image, replace the image, approve, reject or archive, and inspect the full generation, validation and audit history.
7. The operator is notified when a draft is ready or generation fails.

**It never publishes.** No code path in `backend/app/community` can reach a YouTube client. Approval is a recorded decision on the draft only.

## 2. Files

| File | Change |
|---|---|
| `backend/app/community/__init__.py` | new: package |
| `backend/app/community/store.py` | new: tables, lifecycle, transitions, retention |
| `backend/app/community/content.py` | new: sources, selection, formats, templates, optional Gemini |
| `backend/app/community/images.py` | new: licensed-asset cards, title cards, hashing |
| `backend/app/community/validation.py` | new: text and image validation |
| `backend/app/community/service.py` | new: pipeline, schedule, lifecycle, limits, notifications |
| `backend/app/scheduler/scheduler.py` | `run_community_draft_tick()` and the `community_draft_tick` job (5-minute check, `max_instances=1`, `coalesce`) |
| `backend/app/main.py` | 16 `/api/community` routes and request models |
| `backend/app/templates/dashboard.html` | Community Posts tab (CSS, HTML, JS) |
| `backend/tests/test_community_posts_332e.py` | new: 75 test cases |
| `.gitignore` | `backend/data/community/` and `backend/data/COMMUNITY_DRAFTS_DISABLED` |
| `docs/STAGE_3_32E_*.md` | new: 6 documents |
| `docs/PROJECT_HANDOVER.md` | updated |

## 3. Database (additive; production tables untouched)

All tables are created on the first **write**: saving the schedule or the first generation. Reads and scheduler ticks never create them.

| Table | Purpose | Key constraints |
|---|---|---|
| `community_config` | Schedule and settings (key/value JSON, who changed it and when) | PK `key` |
| `community_runs` | Generation runs: trigger, attempts, lease, duration metrics, error | `idem_key` UNIQUE (`community:scheduled:<date>`, `community:manual:<date>:<id>`) |
| `community_drafts` | Drafts: status, format, topic, source, text, fingerprint, rotation keys, validation and generation JSON, version, decision | **partial UNIQUE index: one active draft per `post_date`** |
| `community_images` | Image provenance: method, source, licence, attribution, SHA-256, difference hash, size, validation | index on `sha256` |
| `community_events` | Audit history (every generation step, edit with before/after text, decision, config change) | append-only: an UPDATE trigger aborts |
| `community_notifications` | Notifications shown on the dashboard, with delivery outcome | |

- **Image files** live in `backend/data/community/images/<draft id>/<image id>.jpg`, outside SQLite.
- **Content topics** are the drafts' `topic_key`, `topic_title`, `category`, `source_kind` and `source_ref`, indexed by `topic_key`. Topic reuse is checked from them, so a separate topic table would only duplicate them.

**Retention:**

| Data | Removed after |
|---|---|
| Inactive drafts and their images | 365 days (cap 5,000) |
| Runs | 180 days |
| Events | 365 days (cap 50,000) |
| Notifications | 90 days |

Orphan image folders are removed too. Only `cimg_*.jpg` and `.part` files inside `cdraft_*` folders are ever deleted, and never symlinks.

## 4. Draft lifecycle

```
GENERATING → IMAGE_PENDING → VALIDATING → READY_FOR_REVIEW → APPROVED → ARCHIVED
                                              │  ↑ edit / regenerate (approval withdrawn)
                                              ├→ REJECTED → ARCHIVED
failures:  GENERATION_FAILED · IMAGE_FAILED · VALIDATION_FAILED → (regenerate) or ARCHIVED
CANCELLED: the run was interrupted (process stopped); never resumed
```

- **Atomic transitions.** Every change is a compare-and-set on `(status, version)` inside `BEGIN IMMEDIATE`. Transitions outside `store.TRANSITIONS` raise 409.
- **Stale views are refused.** Each write carries the `version` the operator was shown, so a stale view or a duplicate request gets 409.
- **Approving:**
  - `VALID`: approves normally.
  - `VALID_WITH_WARNINGS` or `NEEDS_OPERATOR_REVIEW`: needs `acknowledge_warnings=true` and a reason of at least 5 characters, recorded with the acknowledged checks.
  - `REJECTED`: can never be approved.
  - The image's SHA-256 is re-checked at approval.
- **Any edit or regeneration** of an approved draft withdraws the approval and re-validates.
- **Archiving** frees the date for a new draft.

## 5. Operational safety

| # | Protection | Where |
|---|---|---|
| 1 | Independent kill switch: dashboard switch, `backend/data/COMMUNITY_DRAFTS_DISABLED`, and the global `backend/data/AUTONOMY_DISABLED` | `service.kill_switches()`; blocks scheduled, manual and regeneration |
| 2 | Daily limit: 1 scheduled draft per date, 3 manual generations per day (configurable 1–10) | UNIQUE key, `LimitExceeded` (429) |
| 3 | Duplicate prevention | One active draft per date (DB index), text fingerprint and trigram overlap, image SHA-256 and difference hash |
| 4 | Bounded retries: 2 scheduled attempts, 10 minutes apart, inside the catch-up window | `_tick` |
| 5 | Generation timeout: 120 s deadline between stages; Gemini 30 s; run lease 300 s | `GENERATION_TIMEOUT`, `RUN_LEASE_SECONDS` |
| 6 | Image limits | Source ≤ 25 MB and ≤ 60 MP; output 1080 × 1080, 8 KB–5 MB |
| 7 | Request limits | Regenerations 10 per day; Gemini 2 calls per day (off by default); notifications 10 per day; bounded API pagination |
| 8 | Resource locking | `BEGIN IMMEDIATE` claims, run lease, version compare-and-set, exclusive-create file writes |
| 9 | Safe cleanup | Retention only inside the Community image folder, by validated id patterns |
| 10 | Audit history | `community_events`, append-only |

**Crash recovery:**
- An expired run lease becomes `ABANDONED` and its in-progress draft becomes `CANCELLED`.
- An in-progress draft older than 10 minutes without a run moves to its failed state.
- Nothing is resumed blindly. The scheduled retry (attempt 2) starts a fresh draft.

## 6. Publishing isolation (proof)

- `test_publishing_is_unreachable_from_community_code` parses every module in `backend/app/community`.
  - The only `backend` imports allowed are the package itself, `control_plane`, `registry`, `core.paths`, `core.config`, `content.number_words` and `content.hook_evaluator`.
  - None of the existing publishing modules can be imported: `youtube_service`, `youtube_publishing_engine`, `youtube_connection_service`, `googleapiclient`, OAuth, `upload_short`.
  - The source contains no `posts.insert`, `imageUpload`, `token.json` or `client_secrets`.
- The registry keeps `youtube.community.publish` PROHIBITED.
- The 16 Community routes contain no publish or upload route.
- The approve handler is a database transition only. Its audit record says "publishing: none".

## 7. Configuration before activation

Nothing runs until an operator acts. To activate on the live system:

1. **Deploy:**
   - restart the **dashboard** (for the API and tab);
   - restart the **scheduler daemon** to load the `community_draft_tick` job. That needs a separate approval, because the daemon runs production.
2. In **Community Posts → Schedule settings**:
   - enter an operator name;
   - choose the time, timezone and formats;
   - tick **Automatic generation enabled**;
   - click **Save schedule**.
3. Optional: try **Generate now** first. Manual generation works without the daemon restart.
4. Optional: Gemini wording. Set Text to Gemini; it needs the existing `GEMINI_API_KEY`, is limited to 2 calls a day, and falls back to templates.

## 8. Known limitations

- **Watermarks** are not detected automatically. Images come only from licence-verified, credited sources or are generated locally.
- **The inappropriate-content and sensitive-subject checks** are keyword lists. Sensitive subjects are routed to the operator (NEEDS_OPERATOR_REVIEW), never auto-approved.
- **Video references are rare today.** The video follow-up format, and links, are only possible for **public** videos; 42 of 43 uploads are currently private.
- **Template wording is deliberately plain.** Gemini wording is optional and bounded.
- **Timing:** the scheduled draft is generated within 5 minutes of the configured time (tick interval).
- **The quiz answer** is shown to the operator (dashboard) for the comment reveal; it is not posted automatically.
- **Replacing an image** is limited to the licensed assets of the draft's own topic and the title card. Uploading arbitrary files is deliberately not supported (no arbitrary file write).
