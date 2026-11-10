# AI YouTube Shorts Studio — Proposed Evolved Architecture
**Project Directory:** `D:\AI Arts\AI\`  
**Date of Architecture:** November 10, 2026  
**Architect:** Senior Software Architect, AI Engineer & Animation Specialist  
**Target Hardware:** AMD Ryzen 7 3700U (4C/8T, 2.3–4.0 GHz), 16 GB DDR4 RAM, Integrated Radeon Vega 10  
**Target Medium:** YouTube Shorts (1080x1920 vertical, 9:16 aspect ratio, 30.0 FPS, H.264 / AAC)

---

## 1. Architectural Vision & Core Principles

The evolved architecture transitions the studio from a single-engine prototype into an enterprise-grade, **modular multi-style autonomous production studio**. 

The architecture is governed by five foundational pillars:

1. **Four Ground-Truth Visual Style Modules:** Instead of forcing every video into one rigid visual format, the system integrates four distinct visual style families derived from the ground-truth HTML references:
   - `Chalk Talk` (Hand-Drawn Blackboard / Educational)
   - `Creator Motion Graphics` (Kinetic Typography / Business-Creator)
   - `Data Story` (Editorial Data Explainer / Sourced Charts)
   - `Pop Info` (Illustrated Explainer / Flat-Vector Cartoon & Thought Bubble)
2. **Shorts-Native Vertical Design (1080x1920):** Every visual primitive, font scale, camera movement, and canvas layout is mathematically designed for 9:16 vertical mobile screens with strict UI safe-zone adherence.
3. **Dual-Path Rendering Architecture:**
   - **Path A (Fast Native CPU Canvas):** Direct Python 2D canvas drawing (Pillow/Aggdraw) rendering 1080x1920 frames at **30–35 FPS** with zero browser overhead, perfectly optimized for the Ryzen 7 3700U CPU.
   - **Path B (Headless Web Canvas):** Headless browser evaluation of the reference HTML files via `window.seek(t)` and Web Audio `SFX.render()`, enabling pixel-perfect execution of complex WebGL/Canvas effects.
4. **Resilient Local-First Orchestration:** Windows Task Scheduler triggers the production pipeline nightly at 11:00 PM IST. The SQLite state machine records checkpoints after every stage, enabling automatic resumption if interrupted.
5. **Quality-Gated Publishing:** Every video must pass the 15-point automated QC engine before being approved. Publishing defaults to `MANUAL_APPROVAL` and can be toggled to `AUTOMATIC_PUBLISH` for 10:00 AM IST daily release.

---

## 2. Target Project Directory Structure

```
D:\AI Arts\AI\
├── backend\                         # Python FastAPI Backend & Core Engine
│   ├── app\
│   │   ├── main.py                  # FastAPI REST API entry point (20 endpoints)
│   │   ├── core\
│   │   │   └── config.py            # Settings, paths, and environment configuration
│   │   ├── database\
│   │   │   └── db.py                # SQLAlchemy models & SQLite connection pooling
│   │   ├── models\                  # Pydantic request/response schemas
│   │   ├── agents\                  # Multi-Agent Content Intelligence
│   │   │   ├── trend_researcher.py  # RSS & Gemini trend discovery
│   │   │   ├── topic_selector.py    # Virality & interest scoring algorithm
│   │   │   ├── scriptwriter.py      # 50–60s vertical scriptwriter
│   │   │   ├── visual_director.py   # Multi-style storyboard & scene generator
│   │   │   └── seo_publisher.py     # Title, description, tags, and 9:16 thumbnail
│   │   ├── styles\                  # Modular Visual Style Implementations
│   │   │   ├── __init__.py          # Style registry and selector factory
│   │   │   ├── base_style.py        # Abstract base class for all render styles
│   │   │   ├── chalk_talk.py        # Style 1: Hand-Drawn Blackboard / Chalk Stick
│   │   │   ├── creator_motion.py    # Style 2: Kinetic Caps, Slams, Highlighter, 3D Yaw
│   │   │   ├── data_story.py        # Style 3: Sourced Charts, Monotone Curves, Bars
│   │   │   └── pop_info.py          # Style 4: Illustrated Explainer, Cartoon, Thought Bubble
│   │   ├── services\                # Shared Infrastructure Services
│   │   │   ├── tts_service.py       # Edge-TTS audio & millisecond ASS subtitle burn-in
│   │   │   ├── qc_service.py        # 15-Point automated quality control engine
│   │   │   ├── youtube_service.py   # YouTube Data API v3 upload & scheduling
│   │   │   └── audio_service.py     # Procedural audio generator & ducking mixer
│   │   ├── workers\
│   │   │   └── job_manager.py       # Persistent state machine, checkpoints, heartbeats
│   │   └── scheduler\
│   │       └── scheduler.py         # APScheduler for daily internal triggers
│   ├── studio.db                    # Active SQLite production database
│   ├── requirements.txt             # Verified backend dependencies
│   └── tests\                       # Comprehensive Test Suites
│       ├── run_audit_tests.py       # 13-Point backend integration test suite
│       ├── test_job_manager.py      # State machine & recovery tests
│       └── test_styles.py           # Multi-style rendering verification tests
│
├── frontend\                        # React 18 + TypeScript + Tailwind Studio UI
│   ├── src\                         # React source components and dashboard
│   ├── dist\                        # Pre-compiled production bundle (served at /)
│   ├── package.json                 # Frontend dependencies
│   └── vite.config.ts               # Vite build config with WASM rollup override
│
├── assets\                          # Global Static Production Assets
│   ├── audio\
│   │   ├── music\ambient_tech_theme.wav # Ambient background theme
│   │   └── sfx\                     # Reusable transition & accent SFX
│   │       ├── whoosh_transition.wav
│   │       └── chime_pop.wav
│   ├── backgrounds\rendered\        # Reusable 1080x1920 vertical background plates
│   └── fonts\                       # Bundled TTF fonts for deterministic rendering
│       ├── Anton-Regular.ttf
│       ├── InstrumentSerif-Italic.ttf
│       ├── Inter-Bold.ttf
│       ├── CabinSketch-Bold.ttf
│       ├── Kalam-Regular.ttf
│       ├── Archivo-Black.ttf
│       ├── Fredoka-Bold.ttf
│       └── IBMPlexMono-Medium.ttf
│
├── media\                           # Generated Pipeline Media Outputs
│   ├── audio\                       # Voiceover MP3 files
│   ├── subtitles\                   # Word-level .ass and .srt files
│   ├── video\                       # Final rendered 1080x1920 MP4 shorts
│   └── thumbnails\                  # High-CTR vertical thumbnails
│
├── Youtube Channel Src\             # Permanent User Channel Branding (NEVER TOUCH)
│   ├── Youtube-Icon.png
│   ├── Compressed-Youtube-Icon.png
│   └── Compressed-Youtube-Banner.png
│
├── scripts\                         # Operating System Automation Scripts
│   ├── start_daily_pipeline.ps1     # 11:00 PM IST nightly execution script
│   ├── start_backend.ps1            # FastAPI dashboard launcher
│   ├── check_pipeline_status.ps1    # Status & progress inspection CLI
│   ├── recover_interrupted_jobs.ps1 # Recovers stale/interrupted jobs
│   ├── install_scheduled_tasks.ps1  # Installs Windows Task Scheduler triggers
│   └── uninstall_scheduled_tasks.ps1
│
├── docs\                            # Comprehensive Project Documentation
│   ├── reference_styles\            # Ground-truth HTML style references
│   │   ├── chalk_talk.html
│   │   ├── creator_motion_graphics.html
│   │   ├── data_story.html
│   │   └── pop_info.html
│   ├── PROJECT_AUDIT.md             # Full technical audit report
│   ├── CLEANUP_PLAN.md              # Safe file disposition & dry-run plan
│   └── PROPOSED_ARCHITECTURE.md     # This architectural specification
│
├── tools\                           # 100% Portable Local Runtime Environment
│   ├── python\                      # Official signed PSF Python 3.11.9
│   ├── nodejs\                      # Portable Node.js v20.18.0 & npm
│   ├── ffmpeg\                      # FFmpeg & FFprobe 9.0.2 essentials
│   └── uv\                          # High-performance UV 0.5.11 package installer
│
├── archive\                         # Isolated Storage for Superseded Code & Tools
│   ├── setup\                       # One-off bootstrap scripts
│   ├── diagnostics\                 # Standalone debug scripts
│   ├── legacy_engines\              # Facial-component sprite renderer
│   └── animation_engine_prototype\  # Incomplete TypeScript prototype
│
├── cache\                           # Local Package & Scratch Caches (Clearable)
│   ├── uv\                          # UV wheel cache
│   ├── npm\                         # npm tarball cache
│   └── temp\                        # Temporary rendering scratchpad
│
├── start_studio.bat                 # One-click studio desktop launcher
├── start_studio.ps1                 # PowerShell studio launcher
└── README.md                        # Quickstart and overview
```

---

## 3. The Visual Style Subsystem

The visual style subsystem is the artistic heart of the studio. Rather than generating homogeneous videos, the Visual Director analyzes the topic and script, selects the optimal visual style, and delegates rendering to a specialized style module.

```
                    ┌─────────────────────────┐
                    │     Visual Director     │
                    │  (Topic & Script Input) │
                    └────────────┬────────────┘
                                 │ Selects Style Based on Topic
          ┌──────────────────────┼──────────────────────┬──────────────────────┐
          ▼                      ▼                      ▼                      ▼
┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
│   Chalk Talk     │   │  Creator Motion  │   │    Data Story    │   │     Pop Info     │
│ (Hand-Drawn 2D)  │   │  (Kinetic Type)  │   │  (Sourced Data)  │   │  (Illustrated)   │
├──────────────────┤   ├──────────────────┤   ├──────────────────┤   ├──────────────────┤
│ • Slate Green    │   │ • Near-Black     │   │ • Deep Ink       │   │ • Sage & Warm    │
│ • Chalk Stick    │   │ • Anton Caps     │   │ • Monotone Spline│   │ • Cartoon Scene  │
│ • Tooth Skip     │   │ • Word Slams     │   │ • Growing Bars   │   │ • Parallax Hero  │
│ • Felt Eraser    │   │ • Highlighter    │   │ • Metric Cards   │   │ • Thought Bubble │
│ • Dust Particles │   │ • Punch & Shake  │   │ • Mono Citation  │   │ • Mascot & Pin   │
│ • Procedural SFX │   │ • 3D Yaw Cards   │   │ • Pentatonic SFX │   │ • Iris Wipes/Pop │
└──────────────────┘   └──────────────────┘   └──────────────────┘   └──────────────────┘
```

### 3.1 Style 1: Chalk Talk (Hand-Drawn Blackboard)
- **Topic Affinity:** Physics, mathematics, astronomy, theoretical concepts, history lessons, step-by-step logic proofs.
- **Visual Design System:**
  - Background: Slate green `#24392f` procedurally textured with value noise and faint chalk ghosts of prior equations.
  - Tooth Alpha-Punch: Chalk strokes skip over board grain for authentic texture.
  - Chalk Stick: Visible chalk stick tracks the leading tip of text and lines as they are drawn.
  - Eraser Wipe: A felt eraser swipes back and forth with a soft white haze to clear the screen between major concept beats.
  - Colors: White `#f2efe6`, Yellow `#f4d35e` (main ideas), Pink `#f39bbb` (emphasis rings), Blue `#93cbef` (annotations).
  - Typography: `Cabin Sketch` (headings), `Kalam` (equations and handwritten notes).
- **Audio Profile:** Quiet classroom room tone, procedural chalk scratch bursts with touch taps, counter ticks, and felt eraser swishes.

### 3.2 Style 2: Creator Motion Graphics (Kinetic Typography)
- **Topic Affinity:** Productivity, psychology, finance, tech trends, life hacks, motivational lessons, business breakdowns.
- **Visual Design System:**
  - Background: Deep editing-suite black `#0b0b0d` with radial warm/cool glows, subtle drifting grid, and 24 FPS film grain.
  - Word Slam: Big condensed caps slam onto the screen with exponential easing from 1.42x scale.
  - Highlighter Swipe: Yellow highlighter path drawn behind emphasis words, turning the text black inside.
  - Camera Kinetics: Punch zooms (`PUNCH`) on stressed syllables, camera shakes (`SHAKE`), and light leaks (`LEAK`).
  - Code-Drawn UI Proofs: Browser cards, chat mockups, and mobile notifications rendered with 3D yaw tilt (`drawYaw`).
  - Transitions: Directional whip pans with motion blur, RGB glitch artifacts, and flash cuts.
  - Typography: `Anton` (80–220px caps), `Instrument Serif` (italic yellow emotional phrase), `Inter` (UI labels).
- **Audio Profile:** Sub-bass booms, text slams, whooshes, tape stops, typewriter keys, UI pops, and ducked half-time beat.

### 3.3 Style 3: Data Story (Editorial Data Explainer)
- **Topic Affinity:** Demographics, global economics, climate metrics, market shares, scientific measurements, comparative statistics.
- **Visual Design System:**
  - Background: Deep ink `#0f141c` with subtle ambient glows and clean gridlines.
  - Monotone Splines: Data curves drawn smoothly using Hermite monotone interpolation (`popAt`) preventing artificial overshoot.
  - Dynamic Line Head: A glowing leading circle leads the line as it traces from left to right.
  - Milestone Dots: Milestone years or values pop with scale bounce as the curve passes them.
  - Bar Animations: Vertical bars rise sequentially from baseline with numeric counters ticking above them.
  - Metric Comparisons: Side-by-side cards with a sand-yellow multiplier ratio.
  - Citation Banner: Permanent "Source: [Official Sourced Name]" anchored on screen for editorial authority.
  - Typography: `Archivo` (800) for headlines and numbers; `IBM Plex Mono` for labels, axes, and citations.
- **Audio Profile:** Low thoughtful synthesizer pad bed, pitch-scaled pentatonic plucks following data values, swipes, and milestone bells.

### 3.4 Style 4: Pop Info (Illustrated Cartoon & Thought Bubble)
- **Topic Affinity:** Everyday phenomena, retail psychology, consumer habits, curiosity trivia, biology facts, surprising daily questions (e.g., *"Why is milk at the back of the store?"*).
- **Visual Design System:**
  - Design Coordinate Space: $680 \times 383$ base design coordinate system scaled via `renderAt()`.
  - Expressive Character Anchor (`drawShopper`): Foreground character with gentle parallax exhibiting multi-stage emotional acting (spring pop-up and landing with wobble, annoyed eyebrow twitch, head tilt toward thought bubble, eye blinks, chest drop sigh, and falling sweat drop).
  - Floating Idea Bubble (`drawBubble`): Deep teal `#2F6F73` thought bubble with speed streaks, milk carton mascot waving and winking at 7.5s with star sparkle, animated numeric counter ticking upward to 500 with unit label ("steps"), and an "away!" pill badge.
  - Perspective Environment Staging (`drawRoom`): Illustrated perspective room with sliding shelves (`drawShelf`), illuminated ceiling lights receding to vanishing point, perspective floor tiles, cooler glow, and swinging hanging sign.
  - Journey Path & Pin (`drawPath`): Dotted curve path tracing through the scene with a destination pin dropping and bobbing into place.
  - Iris Transitions (`drawWipe`): Circular teal iris opening and closing with a sunny yellow stroke ring.
  - Colors: Sage wall `#D7DCCB`, warm floor `#C4B79D`, warm accents (`#E07A5F`, `#F2CC8F`, `#81B29A`, `#6C9BB8`), deep teal `#2F6F73`, coral `#E85D4A`, sunny yellow `#FFD86B`.
  - Typography: `Fredoka` (500, 600, 700) rounded font for warm, approachable cartoon infographics.
- **Audio Profile:** Soft, toy-like and sparse audio kit: rounded pops for bubbles and pins, sine bloops for cartoon reactions and winks, wooden taps for shopper landings, steps, and path dots, airy whooshes for iris wipes and sliding shelves, pink noise breathy sigh, and calm ducked musical bed.

---

## 4. Adapting Reference Styles to YouTube Shorts (1080x1920)

The four reference HTML files were built for 16:9 landscape screens (1280x720). Adapting them to 9:16 vertical video requires explicit architectural rules:

```
┌─────────────────────────────────────────────────────────────┐
│          1080 x 1920 VERTICAL SHORTS CANVAS SPEC            │
├─────────────────────────────────────────────────────────────┤
│ y = 0     ┌───────────────────────────────────────────┐     │
│           │ TOP SAFE ZONE (y: 0 - 280)                │     │
│           │ [Topic Category Pill / Progress Bar]      │     │
│ y = 280   ├───────────────────────────────────────────┤     │
│           │                                           │     │
│           │                                           │     │
│           │ PRIMARY VISUAL STAGE                      │     │
│           │ (y: 280 - 1380)                           │ ◄───┼─ Kinetic Text (80-160px)
│           │                                           │     │  Diagrams / Character
│           │ • Chalkboard / Diagrams / Cards           │     │  Charts / 3D Mockups
│           │ • Cartoon Scene & Thought Bubble          │     │  Source Citations
│           │ • Main Kinetic Word Slams                 │     │
│           │                                           │     │
│           │                                           │     │
│ y = 1380  ├───────────────────────────────────────────┤     │
│           │ SUBTITLE ZONE (y: 1380 - 1640)            │ ◄───┼─ Word-Level Active Karaoke
│           │ [Burned ASS Subtitles with Yellow Pop]    │     │  High-Contrast Drop Shadow
│ y = 1640  ├───────────────────────────────────────────┤     │
│           │ BOTTOM OVERLAY DEAD ZONE (y: 1640 - 1920) │ ◄───┼─ Occluded by YouTube UI
│           │ [Channel Avatar, Sound Tag, Like Buttons] │     │  (Keep clear of text)
│ y = 1920  └───────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────┘
```

### Vertical Safe-Zone Rules:
1. **Vertical Aspect Ratio:** `1080` pixels wide by `1920` pixels high (9:16 aspect ratio).
2. **Top Safe Margin:** Keep critical text below `y = 280` to avoid YouTube channel headers and top search overlay.
3. **Bottom Safe Margin:** Keep all key visuals and subtitles above `y = 1640` to avoid YouTube Shorts title, sound tag, and like/comment buttons.
4. **Horizontal Safe Margin:** Keep content within `x = 80` to `x = 1000` (right margin accounts for Shorts engagement buttons).
5. **Subtitle Placement:** Subtitles anchor at `y = 1480` (center of the subtitle band) with 2-line maximum wrap and word-level yellow highlight (`\c&H00D6FF&`).

---

## 5. Dual-Path Rendering Architecture

To balance rendering performance on the Ryzen 7 3700U with visual fidelity, the architecture supports two complementary rendering paths:

```
                             ┌────────────────────────┐
                             │    Visual Director     │
                             │ (Selects Render Mode)  │
                             └───────────┬────────────┘
                                         │
                 ┌───────────────────────┴───────────────────────┐
                 ▼                                               ▼
   ┌───────────────────────────┐                   ┌───────────────────────────┐
   │    PATH A: Fast Python    │                   │   PATH B: Headless Web    │
   │      Canvas Renderer      │                   │      Canvas Renderer      │
   ├───────────────────────────┤                   ├───────────────────────────┤
   │ • Native Pillow + Aggdraw │                   │ • Node.js / Puppeteer     │
   │ • 30–35 FPS on CPU        │                   │ • 10–18 FPS on CPU        │
   │ • Zero browser overhead   │                   │ • Exact HTML/Canvas eval  │
   │ • Direct pipe to FFmpeg   │                   │ • Pure Web Audio PCM eval │
   │ • Ideal for daily 11 PM   │                   │ • Ideal for high-end WebGL│
   │   scheduled automation    │                   │   motion graphics         │
   └─────────────┬─────────────┘                   └─────────────┬─────────────┘
                 │                                               │
                 └───────────────────────┬───────────────────────┘
                                         ▼
                             ┌────────────────────────┐
                             │   FFmpeg Mux & Burn    │
                             │ • H.264 video encode   │
                             │ • AAC audio muxing     │
                             │ • ASS subtitle burn-in │
                             └───────────┬────────────┘
                                         ▼
                             ┌────────────────────────┐
                             │ 15-Point Quality Gate  │
                             └────────────────────────┘
```

### Path A: Native Python Canvas Engine (Default Production Mode)
- **Technology:** Python `PIL.Image`, `PIL.ImageDraw`, and direct memory pipes to FFmpeg.
- **Speed:** **35.0 FPS** on AMD Ryzen 7 3700U (a 55-second Short renders in under 50 seconds!).
- **Resource Footprint:** ~180 MB RAM, 45% CPU utilization.
- **Audio Handling:** Mixes Edge-TTS voiceover with ducked procedural music (`ambient_tech_theme.wav`) and timed transition SFX.

### Path B: Headless Web Canvas Engine (Precision Reference Mode)
- **Technology:** Portable Node.js running headless Chrome, seeking every frame via `window.seek(frame / 30)`, capturing the 1080x1920 canvas, and extracting synthesized stereo PCM audio via `SFX.render()`.
- **Fidelity:** Exact 1:1 reproduction of all CSS filter blurs, WebGL shaders, and procedural Web Audio synthesizers.
- **Speed:** ~12–15 FPS on Ryzen 7 3700U.

---

## 6. Daily Automation Lifecycle & State Machine

The autonomous daily production cycle operates on a reliable schedule:

```
   11:00 PM IST                       11:30 PM IST                      10:00 AM IST
        │                                  │                                 │
        ▼                                  ▼                                 ▼
┌───────────────┐                  ┌───────────────┐                 ┌───────────────┐
│ Windows Task  │ ──► [Pipeline] ─►│ 15-Point QC   │ ──► [Hold in] ─►│ Scheduled     │
│ Scheduler     │     Execution    │ Validation    │     Queue       │ YouTube       │
│ Trigger       │                  │ Engine        │                 │ Publication   │
└───────────────┘                  └───────────────┘                 └───────────────┘
                                           │
                                  If QC Fails:
                                  Alert on Dashboard
                                  Retry with backoff
```

### Stage Checkpointing in SQLite (`persistent_jobs`):
The state machine advances through 11 discrete stages, writing output data into SQLite after every step:
1. `RESEARCH`: Topic candidates found from RSS/Gemini.
2. `TOPIC_SELECTION`: Best candidate chosen based on virality and relevance.
3. `SCRIPT`: 50–60s script (130–150 words) written with hook/body/CTA.
4. `STORYBOARD`: Visual Director generates scene sequence and style selection.
5. `VOICEOVER`: Edge-TTS synthesizes crystal-clear audio file.
6. `SUBTITLES`: Word-level karaoke `.ass` subtitle file generated.
7. `ANIMATION`: Video frames rendered and encoded to 1080x1920 MP4.
8. `THUMBNAIL`: High-CTR 9:16 vertical thumbnail generated.
9. `SEO`: Title, description, tags, and category ID prepared.
10. `QUALITY_CHECK`: 15 automated validation checks executed.
11. `COMPLETED`: Video held for manual review or scheduled for YouTube upload.

If any failure occurs (e.g., unexpected reboot, network drop), the `recover_interrupted_jobs.ps1` script detects the stale job, reads the last valid checkpoint, and resumes execution without re-rendering or re-generating audio.

---

## 7. Migration Roadmap

To transition the existing codebase to this target architecture smoothly:

### Step 1: Cleanup & Archival (Requires User Approval)
- Execute `D:\AI Arts\AI\docs\CLEANUP_PLAN.md`.
- Move legacy setup scripts, one-off diagnostics, and unused prototypes to `archive/`.
- Purge ~400 MB of package caches.

### Step 2: Establish Modular Style Directory (`backend/app/styles/`)
- Create `backend/app/styles/base_style.py` with standard interface (`render_scene`, `get_audio_cues`, `get_color_palette`).
- Port current stick figure and motion graphics into `backend/app/styles/creator_motion.py`.
- Implement `backend/app/styles/chalk_talk.py` based on `chalk_talk.html`.
- Implement `backend/app/styles/data_story.py` based on `data_story.html`.
- Implement `backend/app/styles/pop_info.py` based on `pop_info.html`.

### Step 3: Integrate Style Selector into Visual Director
- Update `backend/app/agents/visual_director.py` to tag storyboards with the recommended style based on topic categorization.

### Step 4: Consolidate REST API & Worker
- Wire the FastAPI dashboard endpoints directly into `job_manager.py`.
- Update `run_pipeline.bat` to call the persistent worker.

### Step 5: End-to-End Validation
- Run `backend/tests/run_audit_tests.py` and verify that all style renders pass the 15-point QC engine.
- Verify live dashboard at `http://127.0.0.1:8000`.
