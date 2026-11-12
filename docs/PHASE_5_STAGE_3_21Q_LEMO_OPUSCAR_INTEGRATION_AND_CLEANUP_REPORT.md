# Stage 3.21Q — Lemo-Opuscar Integration and Cleanup Report

**Result:** The pinned upstream Lemo-Opuscar Blueprint renderer produced the sample video and a separately rendered thumbnail. The final video passes metadata checks and a full decode. The authorized Stage 3.21M–P validation cleanup is complete, and the dashboard's recent-run section is removed. No commit, push, upload, or production schedule activation occurred.

## 1. Upstream project, license, and runtime

- Upstream: [lemomo-ai/lemo-opuscar](https://github.com/lemomo-ai/lemo-opuscar)
- Pinned commit: `9eb7739a0fca4c6990fe966cb8b87b1930f7193a`
- License: MIT, verified from the upstream [LICENSE](../tools/dependencies/lemo-opuscar/LICENSE).
- Renderer source is retained under the isolated `tools/dependencies/lemo-opuscar` directory; the adapter uses the workspace-local Node and FFmpeg binaries.
- Verified local versions: Node `v20.18.0`, Python `3.11.9`, FFmpeg/FFprobe `9.0.2`. Rendering used one worker on the available local Windows browser/Playwright/WebGL2 path; no paid cloud service or external asset download was used.
- The upstream Blueprint renderer requires a local WebGL2-capable browser for this style. This remains an environment prerequisite for rerendering; no system-wide runtime configuration was changed.
- The temporary `.npm-bootstrap` and `.validation` directories were inspected and removed after use. The pinned source and installed renderer dependencies remain.

## 2. Integration and render execution

The [LemoOpuscarAdapter](../backend/app/rendering/lemo_opuscar_adapter.py) isolates the pinned dependency, checks for its video/still entry points and local tools, then invokes the upstream renderer. CrazzyDocs retains its existing downstream FFmpeg/audio/QC workflow; the video stream is copied when muxing rather than replaced by the native CrazzyDocs visual renderer. The POC wrote only to its isolated validation workspace.

The recorded renderer invocation is in the [run manifest](../media/productions/val_3_21q_black_hole_20261111_2138/manifest.json):

```text
tools/nodejs/node.exe
  tools/dependencies/lemo-opuscar/core/render/video.mjs
  <workspace>/film --fps 30 --workers 1 --size 1080x1920
  --out <workspace>/black_hole_lemo_opuscar_visual_only.mp4
```

The separate thumbnail was rendered through the upstream still renderer at the selected timeline cue with `thumbnail=1`; it was not extracted from the final MP4. FFmpeg then muxed the visual-only video with the local narration and score, normalized the mix, and encoded AAC audio.

### Selected style

The treatment selected **Blueprint** based on the upstream [STYLE.md](../tools/dependencies/lemo-opuscar/styles/blueprint/STYLE.md): cyanotype paper, white technical line work, annotations, drafting order, and camera movement over a single sheet suit an explanation of an invisible gravitational boundary. The [treatment](../media/productions/val_3_21q_black_hole_20261111_2138/TREATMENT.md) records the style rationale, NASA factual basis, narrative, cue timings, and composition plan.

## 3. Video and thumbnail verification

| Output | Verification |
|---|---|
| [Final H.264 video](../media/productions/val_3_21q_black_hole_20261111_2138/black_hole_lemo_opuscar.mp4) | 1080×1920, 30/1 FPS, 1,569 frames, 52.4 seconds, 15,199,032 bytes; AAC mono audio at 96 kHz. SHA-256: `7cdddacdff0a5a31e040f44990496dcc725326dc3f015c3f2541695ae5601f65`. FFprobe checks and full FFmpeg decode passed. |
| [Visual-only upstream render](../media/productions/val_3_21q_black_hole_20261111_2138/black_hole_lemo_opuscar_visual_only.mp4) | Present in the same isolated workspace; this is the upstream renderer output used as the final video's visual stream. |
| [Separate thumbnail](../media/productions/val_3_21q_black_hole_20261111_2138/thumbnail.jpg) | JPEG, 1080×1920, 188,540 bytes. SHA-256: `b7c951bc5be88a823871d7dd7be0708d9f50fdd12113520084e2ae206aaae6f9`. Rendered separately in the Blueprint style. |
| [Actual-frame contact sheet](../media/productions/val_3_21q_black_hole_20261111_2138/qc/contact_sheet.png) | Generated from the final encoded video at eight points across its timeline. |

The contact sheet and actual full-resolution frames were inspected, including the [30-second accretion-disk frame](../media/productions/val_3_21q_black_hole_20261111_2138/qc/frame_30s_accretion.png), [42-second comparison frame](../media/productions/val_3_21q_black_hole_20261111_2138/qc/frame_42s_comparison.png), and [50-second final hold](../media/productions/val_3_21q_black_hole_20261111_2138/qc/frame_50s_final_hold.png).

The rendered grammar is materially expressed in the frames: a ray is drafted across a cyanotype sheet, the grid and ray bend, the event horizon is labeled as a one-way boundary, inward paths appear, a luminous gas ring remains outside the horizon, and an equal-mass comparison ends with a red “NOT A VACUUM” stamp and NASA credit. The pictures are schematics marked “not to scale,” not purported astronomical photographs. The separately rendered thumbnail uses the same technical-sheet grammar and a three-word hook.

The measured cue map is 52.835 seconds; the encoded video is 52.4 seconds. Narration ends at 48.035 seconds, leaving approximately 4.365 seconds of final hold. The output loudness measurement was `-16.38 LUFS` integrated and `-1.20 dBTP`. The final sheet credits NASA's black-hole explainer.

**Visual/audio limitations:** This is a 2D explanatory schematic, not a scientific visualization of observed data; no independent subject-matter review was performed. The narration and score are locally synthesized, without separately recorded foley. The upstream style's local browser/WebGL2 requirement remains for future renders.

## 4. Permanent validation cleanup

The metadata-only [cleanup manifest](./PHASE_5_STAGE_3_21Q_CLEANUP_MANIFEST.json) records the exact deletion inventory and verification. After the sample passed media checks, the cleanup permanently removed:

- 20 obsolete Stage 3.21M–P validation workspaces: 1,326 files totaling 446,151,667 bytes.
- 15 validation progress/observability records, including the orphan validation record without a workspace.
- 19 run-specific validation-topic-history entries and 14 validation category-distribution entries.
- Workspace-contained event logs and generated media as part of the listed workspace removals. No quarantine or media archive was created.

The Stage 3.21Q workspace was explicitly preserved. The shared-asset audit found no exact duplicate groups outside the deleted workspaces and no external absolute references in their manifests. No production records or database rows were removed; `backend/studio.db` had no candidate run-ID references and the manifest records no database mutation.

Publishing-ledger verification remained unchanged:

- Active ledger: 0 rows; SHA-256 `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945`.
- Archived ledger: 253 rows; SHA-256 `6487eaed55415c5e2c2fe0a05aa12576a449e012f0968bb1c1c820b97e002ba3`.

## 5. Dashboard changes and timestamps

- Removed the complete **Recent Production Observability Runs** section and its run-list renderer. The live progress panel remains.
- Retained the history endpoint and its dashboard fetch because that fetch supplies the separate genuine publishing-ledger history table; it is not the removed observability-run list.
- Terminal validation completion is rendered as `IDLE` in the worker badge; failure states remain visible. Backend worker reconciliation also selects only active validation records, so terminal history does not itself populate the worker.
- The operational timestamp formatter treats timezone-less ISO timestamps as UTC and formats them explicitly with `Asia/Kolkata`, suffixed `IST`. For example, `2026-11-11T08:28:03Z` formats as `2026-11-11 13:58:03 IST`; UTC values are not labeled as IST without conversion.

**Live-server caveat:** The already-running dashboard process has not loaded the updated Python worker-reconciliation module. Its overview API still reports `COMPLETED` for historical Stage 3.21L run `val_3_21l_20261111_135230`; the corresponding worker process is absent, its state is terminal, and the corrected dashboard badge renders `IDLE`. The checked-in source and focused regression test return `IDLE` for terminal-only history. The server was not restarted because its startup runs database initialization and a restart was unnecessary to serve the template update; this avoids an out-of-scope database mutation or scheduler side effect. A future controlled dashboard restart will make the API field match the corrected source.

## 6. Tests and verification

- Focused pytest selection covering the Lemo adapter, creative-architecture regression, terminal validation-worker reconciliation, and dashboard terminal-state rendering: **8 passed**.
- Python compilation of the adapter, Q runner, and touched dashboard test module: **passed**.
- Final MP4 full decode: **passed**; FFprobe metadata and output hashes verified.
- `git diff --check`: **passed**. Git emitted line-ending conversion warnings for existing working-copy files; no whitespace errors were reported.
- The ES-module syntax check and actual renderer invocation/QC were completed during the render stage.
- The complete repository test suite was **not run**. Pytest emitted existing pytest-asyncio fixture-scope and Pydantic class-config deprecation warnings.

## 7. Git state and preservation

The final status is `main...origin/main [ahead 2]`, with 47 modified/untracked status entries. The worktree was already dirty at handover, with Stage 3.21N/O/P implementation and report files and other project changes. Those changes were preserved; no reset, stash, broad formatting, commit, or push was performed. Stage 3.21Q adapter, runner, tests, treatment, output manifest, cleanup manifest, dashboard correction, and this report remain uncommitted. No database or publishing-ledger files were modified by the Q verification.

## 8. Final operational safety

| Check | Result |
|---|---|
| `CrazzyDocs-Production-Scheduler` scheduled task | Disabled |
| Scheduler daemon | Stopped; no matching project scheduler process found; `ENABLE_EMBEDDED_SCHEDULER` is unset |
| Dashboard service | Running and responsive; recent-run section is absent |
| Production worker | `IDLE` in the live overview |
| Validation worker | No worker process found; terminal Stage 3.21L history remains accessible. The live API's stale `COMPLETED` value is documented above; the dashboard badge shows `IDLE`. |
| Publishing mode | `manual_approval` |
| Active schedule profile | `FOUR_CYCLES_DAILY` |
| `SINGLE_DAILY_2300` | Inactive |
| Active / archived publishing ledgers | Unchanged; hashes and counts verified above |
| YouTube uploads / production publishing | None performed |
| Stage 3.21Q workspace | Preserved at its canonical validation-workspace path |

No autonomous production was started or enabled.
