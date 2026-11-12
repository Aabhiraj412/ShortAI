# Stage 3.21R — Black-Hole Short, Dashboard Reconciliation, and Safety Report

## Summary

The approved 50-second black-hole Short was rendered in an isolated validation workspace and delivered to the requested canonical video and thumbnail paths. The dashboard was restarted to load the stale-run and timestamp corrections. No publishing, scheduling, database, or cleanup action was performed.

## Git state and preservation

The worktree was already dirty with Stage 3.21N–Q work and other uncommitted changes. Those changes were preserved; nothing was reset, stashed, discarded, broadly reformatted, committed, or pushed. This stage added the Stage 3.21R renderer workflow and its focused tests, including a zero-energy audio-fingerprint guard discovered during rendering. The final change check (`git diff --check`) passed; Git emitted existing line-ending warnings.

The narration preview first contained an inaccurate reference to M87 as a star. That line was corrected to identify M87 as a galaxy, the audio was regenerated, and the corrected preview was auditioned and approved before the final render.

## Dashboard correction and verification

- The live observability panel now reports no active production when there is no confirmed active run. It shows an explicit idle message rather than reusing a completed historical run as live progress. The `History of Antarctica` run remains available in history.
- Recent operational events exclude `test_*` jobs. Stale subtages are not presented as live `RUNNING` work.
- The running dashboard process was restarted (PID 4188 stopped; the dashboard task brought the service back on port 8000) so the served template and backend included the changes.
- The refreshed page showed pipeline, production worker, validation worker, and overall operational state as `IDLE`; the active-progress API returned `has_active: false`.
- The activity API returned `2026-11-10T21:17:09.061492Z`; the dashboard displayed the same event as `2026-11-11 02:47:09 IST`. This is the correct UTC-to-IST conversion (+05:30), not a UTC value mislabeled as IST.
- After asynchronous dashboard data loaded, the active publishing ledger showed 0 active records and 253 archived records. Historical records were not deleted.

## Rendered video and frame review

The approved narration is 46.987 seconds; the final video is exactly 50 seconds. Independently probed workspace and canonical MP4s both report H.264 video at 1080×1920, 30 fps, with 48 kHz stereo audio. Their SHA-256 hashes match (`f6ed8b3d8b51af9f057f2e1e06d4ab94d6567ba0e52c71cd74fc0ebc995b3035`), and full FFmpeg decode checks passed for both.

- Workspace video: [black_hole_stage_3_21r_candidate.mp4](../media/productions/val_3_21r_black_hole_20261111_2257/black_hole_stage_3_21r_candidate.mp4)
- Canonical video: [crazzydocs_black_hole_final.mp4](../media/video/crazzydocs_black_hole_final.mp4)
- Canonical thumbnail: [crazzydocs_black_hole_thumbnail.jpg](../media/thumbnail/crazzydocs_black_hole_thumbnail.jpg) (1080×1920; 115,373 bytes)
- Twenty sampled rendered frames: [contact_sheet.png](../media/productions/val_3_21r_black_hole_20261111_2257/qc/contact_sheet.png)
- Render manifest: [manifest.json](../media/productions/val_3_21r_black_hole_20261111_2257/manifest.json)

The contact sheet was generated from the encoded video, not storyboard metadata. It shows a progression through the EHT M87 ring observation, Spitzer infrared galaxy/jet imagery, curved-spacetime grids, redshift wave diagrams, distant/falling clock comparisons, an event-horizon schematic, tidal-force comparison, and a mass-scale diagram. These visuals support the narration's factual points and use scientific-image and explanatory-diagram grammars rather than a single repeated card layout. The EHT ring is deliberately reused as a visual anchor, and several source images or schematic motifs recur; the 20 beats are not 20 unique source images.

Seven existing videos were compared against the render. None triggered the duplicate rejection rule; sampled-frame near-match fractions were 0.0, with mean perceptual-hash distances from 21.0 to 29.923. The closest mean pixel difference was 0.05923 for `production_55sec_short.mp4`, but its hash distance and near-match fraction did not satisfy the duplicate criteria.

Image provenance is recorded in the workspace asset manifest. EHT imagery is attributed under CC BY 4.0; the NASA/JPL image uses NASA public-use guidance. The locally run Kokoro ONNX model and pinned local Lemo/FFmpeg rendering path add no paid cloud dependency.

## Focused validation and cleanup audit

- Focused dashboard/delivery and Stage 3.21R tests: **15 passed**.
- Python syntax compilation for the changed renderer, dashboard service, and delivery module: passed.
- `git diff --check`: passed.
- Full repository test suite: **not run**; these results are limited to the focused tests and syntax checks above.
- The render workspace was inventoried without deleting anything. It contains the source/asset files, local audio/model inputs, renderer files, encoded candidate, thumbnail, manifest, and actual-frame contact sheet needed for traceability or reproduction. No `.tmp`, `.part`, `.bak`, `.log`, staging, or render-work cleanup candidates were found. No workspace or database records were deleted.
- The first comparison attempt exposed a zero-division when a prior audio track contained only silence. The fingerprint now represents a silent window with zero-valued bands; a regression test covers this case.

## Final operational safety state

The refreshed dashboard overview reported `FOUR_CYCLES_DAILY` as the active configuration profile, with `SINGLE_DAILY_2300` inactive. The scheduler task was `Disabled`, the scheduler daemon was `STOPPED`, and production and validation workers were `IDLE`. Publishing mode remained `manual_approval`. The manifest records `scheduler_started: false`, `youtube_uploads: false`, and `database_mutated: false`. The active publishing ledger remained empty (0 active; 253 archived). No YouTube upload or production schedule activation occurred.

