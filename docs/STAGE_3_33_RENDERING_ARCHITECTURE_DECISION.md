# Stage 3.33: rendering technology evaluation and architecture decision

**Status:** decided 2026-11-21. **Decision: keep Lemo-Opuscar + Pop Info as the production renderer. No migration.**

**Constraints:**
- Windows 11 laptop: AMD Ryzen (family 23, model 24; 8 logical CPUs), **AMD Radeon RX Vega 10** (integrated), **13.9 GB RAM** (3.4–4.0 GB free during the benchmark, with the user's applications open);
- no paid cloud rendering, no per-render charges, no mandatory subscriptions, no heavyweight local models.

## 1. Current architecture

```
Script + research -> Pop Info scene planner / creative director (Python) -> plan.json + index.html + engine.js
      -> Lemo-Opuscar renderer (pinned commit 9eb7739, MIT): Node 20 + playwright-core -> installed Microsoft Edge
         (headless) -> per-frame window.render(t) + JPEG screenshot -> FFmpeg (libx264) -> silent film
      -> FFmpeg: narration/music mux, subtitles, outro concat (compositor) -> showcase MP4
Fallback/auxiliary: Python/Pillow procedural engine (explainer clips, Priority B adapter), FFmpeg filter graphs.
```

**Production evidence (Stage 3.32A Operational Intelligence, last 7 days):**
- 27 renders completed, **0 failed (100 %)**;
- 53 videos produced, all on `lemo-opuscar`;
- average render stage **310 s** (this includes planning, photo preparation, the frame render and muxing);
- 0 render timeouts; 3 FFmpeg errors, all from the Priority B padding defect fixed in 3.32H.

**Assessment:**
- **Strengths:**
  - HTML/CSS/canvas gives full typographic and animation freedom, and the Pop Info templates, environments and transitions are built on it;
  - the engine is deterministic (seeked frames, not wall-clock playback);
  - it reuses the system browser (no Chromium download);
  - single-render slots (`RENDER_SLOTS`) prevent overlap;
  - pinned and MIT-licensed;
  - creative evaluation (roadmap 3.32) scores its output 97–100 on animation, transitions, scene diversity, audio and sync.
- **Weaknesses:**
  - CPU-bound screenshot capture (about 2.6 s of rendering per second of video);
  - the browser-capture class pipes JPEG frames, so the output is **full-range `yuvj420p`** (confirmed on a production file);
  - frame capture does not use the GPU.

## 2. Candidates

These were assessed from their documentation as known at the time of writing; **none was installed**. Licences must be re-verified before any adoption.

| Candidate | Technique | Licence / cost | Windows + Vega 10 + 13.9 GB | Integration effort | Fit |
|---|---|---|---|---|---|
| **Lemo-Opuscar + Pop Info (current)** | HTML/canvas, headless Edge capture, FFmpeg | MIT, free | Proven in production (1.4 GB peak) | none | **Best**: already carries the creative system |
| HyperFrames | HTML/CSS (+GSAP) compositions, headless Chromium capture, FFmpeg | open source (verify licence); free | Same class; needs its own Chromium and Node | High: rewrite the Pop Info templates as compositions | Same capabilities as the current engine, no added benefit |
| Remotion | React components, headless Chromium capture, FFmpeg | Source-available; free for individuals and small companies, **company licence above the threshold** (verify the current terms) | Works on Windows; bundles Chrome Headless Shell (download) | High: React rewrite, licence monitoring | Rich ecosystem, but licence risk and a rewrite for the same output class |
| Motion Canvas (and the Revideo fork) | TypeScript generator animations on canvas; rendering through its editor (Revideo adds headless rendering) | MIT, free | Same browser class | High: imperative scene code, limited audio and layout tooling | Good for diagrams; weaker fit for templated Shorts |
| FFmpeg-only composition | filter graphs (zoompan, drawtext, xfade, overlay) | LGPL/GPL build, free | Lightest per frame | Medium for simple scenes, **very high** for rich typography and animation | Already used for muxing, padding and the Priority B adapter; not a replacement for designed scenes |
| Python/Pillow procedural (existing) | Pillow frames → FFmpeg | free | Lowest RAM | Exists | Auxiliary clips only (less expressive) |
| Manim, MoviePy, Blender | maths animation, Python NumPy frames, 3D | MIT / MIT / GPL | Manim and MoviePy are CPU-bound and slower per frame; Blender is heavy on RAM and VRAM for an iGPU | High | Not justified for this format |

## 3. Benchmarks (this machine, 2026-11-21 02:12–02:16 IST, no production job active)

`backend/scripts/benchmark_renderers_333.py`. Raw results: `docs/benchmarks/stage_3_33_render_benchmarks.json`. The synthetic clip is 6 s at 1080×1920 and 30 fps (180 frames), H.264. Memory is the sampled peak working set of the whole process tree.

| Case | Wall | Render s per video s | CPU s (tree) | Peak RAM | Output |
|---|---|---|---|---|---|
| FFmpeg composition (gradients, Ken Burns, drawtext, 2 xfades) | 13.2 s | **2.20** | 63.4 | 1004 MB | yuv420p, 1.9 MB |
| Pillow procedural (existing engine) | 16.8 s | 2.81 | 22.0 | **435 MB** | yuv420p |
| Browser canvas + CSS text, 1 worker (the HyperFrames / Remotion / Motion Canvas class, via the installed engine) | 26.0 s | 4.34 | 91.0 | 1342 MB | yuvj420p, 5.7 MB |
| Same, 2 workers | 22.9 s | 3.81 | 115.2 | 2488 MB | yuvj420p |
| **Real Pop Info production film** (54.2 s, re-rendered into scratch) | 138.6 s | **2.56** | 358.6 | **1379 MB** | yuvj420p, 1625 frames |

**Readings:**
1. The production renderer is not the bottleneck. A real 54 s film renders in about 2.3 minutes at 1.4 GB, so even with only about 4 GB free there is headroom. The remaining time in the 310 s render stage is planning, photo preparation and muxing.
2. A second worker gives **12 %** more speed for **85 %** more RAM. Keep `--workers 1`, as production does.
3. Pure FFmpeg is about 15 % faster per video second, but only for simple scenes. Rebuilding Pop Info's typography and templates as filter graphs is impractical, and the gain would be minutes per day.
4. Any HTML-based alternative (HyperFrames, Remotion, Motion Canvas) is the same technique as the current engine, so similar speed and memory are expected. Changing frameworks would not change the performance class.
5. The Vega 10 iGPU is not used by headless capture (software compositing). GPU encoding (AMF) could offload H.264 encoding but is not needed at these speeds.

## 4. Recommended architecture

Keep the current layered design and **change nothing structural**:
- **Designed scenes:** Lemo-Opuscar + Pop Info, pinned, one worker, with a single render slot.
- **Composition, mux, subtitles, outro, padding:** FFmpeg, with even geometry since 3.32H.
- **Auxiliary procedural clips:** the Pillow engine.
- **Quality loop:** the 3.32 creative evaluation of every rendered video, plus the existing QC and integrity gates.

**Recommended small follow-ups** (each a separate, tested change; none applied in this milestone):
1. **Normalise the final colour range:** add `-pix_fmt yuv420p` with an explicit limited range in the final mux, so delivery files are `yuv420p` (tv range) instead of `yuvj420p` (pc range). The risk is low; verify it on a test render and the QC parity checks.
2. Optionally evaluate AMF hardware encoding (`h264_amf`) for the final encode. Benchmark the quality at equal bitrate first.

## 5. Migration strategy

**None justified.** No candidate offers enough benefit for the cost of rewriting Pop Info's templates, environments and creative director. Revisit only if one of these changes:
- the pinned Lemo-Opuscar becomes unmaintainable;
- a required capability (for example 3D, or real-time captions with word timing) cannot be built in HTML/canvas;
- render time becomes a cycle bottleneck. The current margin is large: about 2.3 minutes of frame rendering inside an hourly cycle.

If a migration is ever justified, follow this order:
1. Build the new engine behind the existing render-parity check (`render_parity.py`).
2. Render the same plans in shadow and compare them with creative evaluation.
3. Switch per category with a flag.
4. Keep the current engine as the fallback until 14 days of parity.

## 6. Risks and rollback

| Risk | Mitigation |
|---|---|
| Edge update changes rendering | The engine is pinned, but the browser is the system Edge. QC and creative evaluation flag regressions. A pinned `chrome-headless-shell` could be configured through `PLAYWRIGHT_CHROME` if needed |
| Full-range output looks different on some players | Follow-up 1 above |
| Low free RAM when the user's applications are open | Single worker; one render slot; 1.4 GB peak measured |
| Benchmark representativeness | Synthetic clips plus one real production re-render; the production 7-day statistics are the ground truth |

**Rollback:** nothing was changed in the rendering path by this stage, so there is nothing to roll back. The benchmark script only writes to a temporary directory.
