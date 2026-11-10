# CrazzyDocs — AI-Powered Automated YouTube Shorts Studio

An autonomous, end-to-end production platform for creating, animating, scoring, subtitling, and publishing high-retention 9:16 vertical YouTube Shorts (1080×1920 @ 30 FPS).

Engineered specifically for personal laptop hardware (**AMD Ryzen 7 3700U, 16 GB RAM, integrated graphics**) using zero-cost, lightweight tools, portable isolated runtimes, and CPU-optimized direct drawing.

---

## 1. System Architecture

```
                                  REACT DASHBOARD (Port 3000 / 8000)
    [Overview] [Topic Research] [Script Studio] [Storyboard] [Video Studio] [Thumbnail] [Publishing] [Settings]
                                                 │
                                                 ▼
                                        FASTAPI BACKEND
                     - SQLite Database (SQLAlchemy)       - APScheduler (10:00 AM IST)
                     - REST API Endpoints                - Unified Production Pipeline
                                                 │
         ┌───────────────────────────────────────┼───────────────────────────────────────┐
         ▼                                       ▼                                       ▼
  AI AGENT SUITE                         ANIMATION ENGINE                        MEDIA PIPELINE
1. Trend Researcher                     - 2D/2.5D Modular Character Rig         - Edge-TTS Neural Voice
2. Topic Selector                         (Head, Blink, Phonemes, Gestures)     - Millisecond ASS Subtitles
3. Scriptwriter (115-135w, 2s hook)     - Kinetic Typography Banners            - Ducked Ambient Music (-18dB)
4. Visual Director                      - Parallax Background & Particles       - Whoosh & Pop Sound Effects
7. SEO, Thumbnail & YouTube Publisher   - Direct FFmpeg Stream Composition      - 15-Point Quality Control
```

---

## 2. Hardware Performance Benchmark

Tested on AMD Ryzen 7 3700U (4 Cores / 8 Threads) & 16 GB RAM:

| Metric | Result | Target Requirement |
|---|---|---|
| **Frame Rendering Speed** | **13.46 ms / frame (~60 FPS)** | < 33 ms (Real-time) |
| **Full Video Render Speed** | **24.5 FPS** (16s video rendered in 19.9s) | CPU-based offline rendering |
| **Audio Synthesis** | **1.67s** for 16-second narration | Natural human-like speech |
| **Subtitle Synchronization** | **Exact millisecond word-boundary** | Glowing active word highlight |
| **Quality Control Pass Rate** | **15/15 checks passed** | 0 black frames, exact 1080x1920 |

---

## 3. Directory Structure

All tools, runtimes, assets, and caches reside strictly in `D:\Agents\CrazzyDocs\`:

```
D:\Agents\CrazzyDocs\
├── tools\              # Portable runtimes (Python 3.11, Node.js LTS, FFmpeg, UV)
├── .venv\         # Python virtual environment (FastAPI, Edge-TTS, Pillow, etc.)
├── backend\            # FastAPI backend, Agents, Services, Scheduler
│   ├── app\
│   │   ├── agents\     # Logical Agents 1 through 7
│   │   ├── api\        # REST API endpoints
│   │   ├── database\   # SQLite models & sessions
│   │   ├── services\   # Animation, TTS, QC, YouTube services
│   │   ├── scheduler\  # APScheduler (10:00 AM IST)
│   │   └── workers\    # Pipeline orchestrator
│   └── studio.db       # Local database
├── frontend\           # React + TypeScript + Tailwind Dashboard
│   ├── dist\           # Pre-compiled production bundle
│   └── src\            # React source code & components
├── animation-engine\   # Motion Canvas & SVG rasterizer
├── assets\
│   ├── audio\          # Generated royalty-free ambient music and sound effects
│   ├── backgrounds\    # 1080x1920 style-specific backgrounds (Pop Info, Data Story, Chalk Talk, Creator)
│   ├── branding\       # CrazzyDocs official channel assets (Icon, Banner, Branded Outro 1080x1920)
│   ├── characters\     # Multi-style character rigs (Dr. Doc Pop host, Chalk figures, Data silhouettes)
│   ├── props\          # Reusable transparent storytelling props across all four styles
│   └── media_library\  # Verified openly licensed real-world media (NASA, Wikimedia, Openverse)
├── archive\
│   └── legacy_assets\  # Non-destructively archived initial development characters and backgrounds
├── media\
│   ├── audio\          # Generated narration MP3s
│   ├── video\          # Rendered 1080x1920 vertical MP4s
│   └── subtitles\      # Generated ASS & SRT subtitle files
├── thumbnails\         # High-CTR 9:16 vertical thumbnails
├── cache\              # Local caches (uv, npm, temp)
├── logs\               # Development & rendering logs
├── start_studio.bat    # One-click Windows dashboard launcher
├── run_pipeline.bat    # Headless pipeline runner
└── README.md
```

---

## 4. Production Standards, Stage 3.1 & Stage 3.2 Innovations

The studio enforces strict production video duration standards and advanced storytelling optimizations:
- **Duration Standard**: All production shorts target **55.0 seconds** (range: 50.0s – 60.0s, 1,500 – 1,800 frames @ 30.0 FPS).
- **First Two Seconds Hook**: Mandatory curiosity-driven opening statement with zero intro fluff, greetings, or logos.
- **Editorial Text System (Stage 3.2)**: Eliminates word-for-word verbatim subtitle clutter. Spoken narration is accompanied by punchy **2–5 word power phrases** (`RAT POISON & BRANDY`, `STRYCHNINE COCKTAIL`, `CARRIED TO GOLD`) positioned in the safe vertical band (`MarginV: 430`, $y \approx 1490\text{px}$) with 60–70% of the video duration left as clean visual breathing room. Multi-track decoupling generates burned editorial text, full accessibility captions (`.srt`), and word-level timing telemetry (`.json`).
- **Complete Story Delivery & Additive Outro Policy (Stage 3.2)**: Strictly mandates that the main narrative video is never trimmed to fit an arbitrary target. The 2.0s branded outro is appended strictly after complete story delivery ($\text{Final Duration} = \text{Complete Main Duration} + 2.0\text{s Outro}$). Target durations are calibrated at planning time (118–128 words @ 2.3 wps). The Universal Compositor enforces an automated 8-point validation check with zero subtitle outro leakage.
- **Content Diversity & Deduplication Ledger (Stage 3.2)**: Persistent SQLite (`studio.db`) and JSON ledger tracking script SHA-256 fingerprints, key entities, titles, and hooks. Multi-dimensional collision detection blocks repeated topics (e.g., $1 bank story duplicate score 0.75). Multi-hook evaluator tests 3 distinct hook archetypes (Paradox/Contradiction, Shocking Event, High-Stakes Question) across 6 weighted criteria (Curiosity, Surprise, Clarity, Accuracy, Visual, Payoff).
- **6-Phase Viewer Retention Pacing**: Hook (0–2.5s), Context (2.5–8s), Build Curiosity (8–17s), Progressive Revelation (17–37s), Breakthrough Payoff (37–50s), Closing (50–53s).
- **Expressive Narration Engine**: Sentence-level emotion delivery (`[Curious]`, `[Suspenseful]`, `[Serious]`, `[Dramatic]`, `[Surprised]`, `[Emphatic]`) modulating pitch, rate, volume, and conversational breath pauses.
- **CrazzyDocs Icon-Only Outro**: Exactly 2.0s (60 frames @ 30 FPS). Minimalist circular channel icon on dark obsidian slate with spring overshoot entrance and chime SFX. Zero banners, zero text, zero buttons.
- **Robust Compositor Concat**: Stream-normalized filter_complex concatenation preventing frozen frame glitches across all video lengths.

Detailed Documentation:
- [`docs/EDITORIAL_TEXT_SYSTEM.md`](file:///D:/AI%20Arts/AI/docs/EDITORIAL_TEXT_SYSTEM.md)
- [`docs/STORY_COMPLETION_AND_DURATION_POLICY.md`](file:///D:/AI%20Arts/AI/docs/STORY_COMPLETION_AND_DURATION_POLICY.md)
- [`docs/CONTENT_DIVERSITY_AND_DEDUPLICATION.md`](file:///D:/AI%20Arts/AI/docs/CONTENT_DIVERSITY_AND_DEDUPLICATION.md)
- [`docs/PHASE_5_STAGE_3_2_QC_REPORT.md`](file:///D:/AI%20Arts/AI/docs/PHASE_5_STAGE_3_2_QC_REPORT.md)
- [`docs/HOOK_AND_RETENTION_GUIDELINES.md`](file:///D:/AI%20Arts/AI/docs/HOOK_AND_RETENTION_GUIDELINES.md)
- [`docs/EXPRESSIVE_NARRATION_SYSTEM.md`](file:///D:/AI%20Arts/AI/docs/EXPRESSIVE_NARRATION_SYSTEM.md)
- [`docs/CRAZZYDOCS_OUTRO_FINAL_SPEC.md`](file:///D:/AI%20Arts/AI/docs/CRAZZYDOCS_OUTRO_FINAL_SPEC.md)
- [`docs/PHASE_5_STAGE_3_1_QC_REPORT.md`](file:///D:/AI%20Arts/AI/docs/PHASE_5_STAGE_3_1_QC_REPORT.md)
- [`docs/BRANDED_OUTRO_IMPLEMENTATION.md`](file:///D:/AI%20Arts/AI/docs/BRANDED_OUTRO_IMPLEMENTATION.md)

---

## 5. Quick Start

### Start the Unified Studio Dashboard

Double-click `start_studio.bat` or run:

```powershell
.\start_studio.bat
```

Open your browser to:
**`http://127.0.0.1:8000`**

From the dashboard, you can:
1. View live production statistics, queue status, and hardware health.
2. Browse discovered trending topics or enter any custom topic.
3. Review and edit the 50-60 second generated script.
4. Inspect the scene-by-scene visual storyboard.
5. Watch the rendered 1080×1920 vertical video with burned-in subtitles.
6. Customize the thumbnail headline and badge.
7. Connect YouTube OAuth2 and toggle between **Manual Approval** and **Autonomous Publishing**.

---

## 5. Daily Publication Schedule

The system contains an active **APScheduler** instance configured for:
- **10:00 AM IST (04:30 UTC)** every day.
- Prevents duplicate daily uploads.
- Automatically validates all 15 Quality Control checks before publishing.
- Uploads video with privacy set to `private` initially, monitors processing, uploads custom thumbnail, and schedules release.

---

## 6. YouTube OAuth2 Setup (Optional)

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project and enable the **YouTube Data API v3**.
3. Create an **OAuth 2.0 Client ID** (Desktop Application).
4. Download the JSON credential and save it as:
   `D:\Agents\CrazzyDocs\backend\client_secrets.json`
5. On first upload, authenticate in your browser to generate `token.json`.
