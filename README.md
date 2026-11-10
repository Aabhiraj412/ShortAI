# AI-Powered Automated YouTube Shorts Studio

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

All tools, runtimes, assets, and caches reside strictly in `D:\AI Arts\AI\`:

```
D:\AI Arts\AI\
├── tools\              # Portable runtimes (Python 3.11, Node.js LTS, FFmpeg, UV)
├── python-env\         # Python virtual environment (FastAPI, Edge-TTS, Pillow, etc.)
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
│   ├── characters\     # Modular SVG character parts (Head, Eyes, Mouths, Arms)
│   ├── backgrounds\    # 1080x1920 SVG backgrounds (Space, Tech Lab, Mystery)
│   └── audio\          # Generated royalty-free ambient music and sound effects
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

## 4. Quick Start

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
   `D:\AI Arts\AI\backend\client_secrets.json`
5. On first upload, authenticate in your browser to generate `token.json`.
