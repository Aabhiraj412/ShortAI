# AI YouTube Shorts Studio — Current Project Structure
**Project Directory:** `D:\AI Arts\AI\`  
**Date:** November 10, 2026 (Post-Phase 2 Cleanup)  
**Status:** Cleaned, Consolidated, Verified (13/13 Integration Tests Passing)

---

## 1. Directory Tree Overview

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
│   │   │   ├── trend_researcher.py  # RSS & Gemini trend discovery (3s timeout)
│   │   │   ├── topic_selector.py    # Virality & interest scoring algorithm
│   │   │   ├── scriptwriter.py      # 50–60s vertical scriptwriter
│   │   │   ├── visual_director.py   # Multi-style storyboard & scene generator
│   │   │   └── seo_publisher.py     # Title, description, tags, and 9:16 thumbnail
│   │   ├── services\                # Shared Production Services
│   │   │   ├── stick_animation_service.py # 2D Stick-Figure & Motion Graphics (35 FPS)
│   │   │   ├── animation_service.py # Facial-component sprite rig (preserved legacy)
│   │   │   ├── tts_service.py       # Edge-TTS voice & millisecond ASS subtitle burn-in
│   │   │   ├── qc_service.py        # 15-Point automated quality control engine
│   │   │   └── youtube_service.py   # YouTube Data API v3 upload & scheduling
│   │   ├── workers\
│   │   │   ├── job_manager.py       # Persistent state machine, checkpoints, heartbeats
│   │   │   └── pipeline.py          # In-memory pipeline runner
│   │   └── scheduler\
│   │       └── scheduler.py         # APScheduler for daily internal triggers
│   ├── studio.db                    # Active SQLite production database (7 tables, 1 active job)
│   ├── studio.db.bak_20261110_050337 # Timestamped pre-cleanup database backup
│   ├── requirements.txt             # Verified backend dependencies
│   └── tests\                       # Integration & Unit Test Suites
│       ├── run_audit_tests.py       # Comprehensive 13-point backend test suite (PASS 13/13)
│       ├── test_job_manager.py      # State machine & recovery tests (PASS 3/3)
│       └── test_stick_animation.py  # 2D Stick figure poses & video render tests (PASS 3/3)
│
├── frontend\                        # React 18 + TypeScript + Tailwind Studio UI
│   ├── src\                         # React source components and dashboard
│   │   ├── App.tsx                  # Tabbed studio dashboard (Overview, Topics, Video, etc.)
│   │   ├── main.tsx                 # React DOM mount point
│   │   └── index.css                # Tailwind styling
│   ├── dist\                        # Pre-compiled production bundle (served at /)
│   │   ├── index.html               # Main dashboard HTML
│   │   └── assets\                  # Bundled JS (176 KB) & CSS (20 KB)
│   ├── package.json                 # Frontend dependencies
│   └── vite.config.ts               # Vite build config with WASM rollup override
│
├── assets\                          # Global Static Production Assets
│   ├── audio\
│   │   ├── music\ambient_tech_theme.wav # Ambient background theme
│   │   ├── sfx\                     # Reusable transition & accent SFX
│   │   │   ├── whoosh_transition.wav
│   │   │   └── chime_pop.wav
│   │   └── generate_assets.py       # Procedural audio generator
│   ├── backgrounds\rendered\        # Reusable 1080x1920 vertical background plates
│   │   ├── bg_space.png
│   │   └── bg_tech_lab.png
│   ├── characters\host\             # Preserved character limb/facial vectors
│   └── icons\                       # Directory for SVG iconography
│
├── media\                           # Generated Pipeline Media Outputs
│   ├── audio\                       # Voiceover MP3 files (sample_narration.mp3)
│   ├── subtitles\                   # Word-level .ass and .srt files
│   ├── video\                       # Final rendered 1080x1920 MP4 shorts
│   │   ├── sample_short.mp4         # Engine 1 render proof (1.40 MB)
│   │   └── stick_sample_short.mp4   # Engine 2 render proof (1.62 MB)
│   └── thumbnails\output\           # High-CTR vertical thumbnails
│       ├── sample_short_thumb.png
│       └── stick_sample_thumb.png
│
├── Youtube Channel Src\             # Permanent User Channel Branding (NEVER TOUCH)
│   ├── Youtube-Icon.png             # Original channel icon (1.97 MB)
│   ├── Compressed-Youtube-Icon.png  # Compressed channel icon (396 KB)
│   └── Compressed-Youtube-Banner.png # Channel banner (1.16 MB)
│
├── scripts\                         # Operating System Automation Scripts
│   ├── start_daily_pipeline.ps1     # 11:00 PM IST nightly execution script
│   ├── start_backend.ps1            # FastAPI dashboard launcher
│   ├── run_worker.ps1               # Standalone worker loop runner
│   ├── check_pipeline_status.ps1    # Status & progress inspection CLI
│   ├── recover_interrupted_jobs.ps1 # Recovers stale/interrupted jobs
│   ├── install_scheduled_tasks.ps1  # Installs Windows Task Scheduler triggers
│   └── uninstall_scheduled_tasks.ps1 # Uninstalls scheduled tasks
│
├── docs\                            # Comprehensive Project Documentation
│   ├── reference_styles\            # Ground-truth HTML style references (PRESERVED UNTOUCHED)
│   │   ├── chalk_talk.html          # Style 1: Hand-Drawn Blackboard (56.5 KB)
│   │   ├── creator_motion_graphics.html # Style 2: Kinetic Type (73.9 KB)
│   │   ├── data_story.html          # Style 3: Sourced Charts (56.2 KB)
│   │   └── pop_info.html            # Style 4: Illustrated Explainer (57.5 KB)
│   ├── PROJECT_AUDIT.md             # Full technical audit report
│   ├── CLEANUP_PLAN.md              # Safe file disposition & dry-run plan
│   ├── PROPOSED_ARCHITECTURE.md     # Evolved multi-style architecture specification
│   ├── PROJECT_STRUCTURE.md         # This structure document
│   ├── CLEANUP_REPORT.md            # Execution report of Phase 2 cleanup
│   ├── ANIMATION_ENGINE.md          # Character kinematics & stick animation spec
│   ├── SYSTEM_ARCHITECTURE.md       # High-level architecture overview
│   ├── AUTOMATION_SETUP.md          # Windows Task Scheduler guide
│   ├── YOUTUBE_OAUTH_SETUP.md       # YouTube OAuth2 authentication guide
│   ├── TEST_RESULTS.md              # Test benchmarks log
│   └── audit_test_results.json      # Machine-readable test execution report
│
├── tools\                           # 100% Portable Local Runtime Environment
│   ├── python\                      # Official signed PSF Python 3.11.9
│   ├── nodejs\                      # Portable Node.js v20.18.0 & npm 10.8.2
│   ├── ffmpeg\                      # FFmpeg & FFprobe 9.0.2 essentials
│   ├── uv\                          # Portable UV 0.5.11 package installer
│   └── downloads\
│       └── python-embed.zip         # Preserved download archive (11.2 MB)
│
├── archive\                         # Isolated Storage for Obsolete/Legacy Code
│   ├── legacy_scripts\              # One-off bootstrap & diagnostic utilities
│   │   ├── setup_tools.ps1
│   │   ├── finish_tools.ps1
│   │   ├── diagnose_tts.py
│   │   ├── test_render.py
│   │   └── render_sample_full.py
│   ├── experimental_prototypes\     # Incomplete / exploratory prototypes
│   │   └── animation-engine\        # Unfinished TypeScript/Node animation prototype
│   └── obsolete_documentation\      # Superseded documentation drafts
│       └── ARCHITECTURE.md          # Early prototype architecture draft
│
├── cache\                           # Local Package & Scratch Caches
│   ├── uv\                          # UV wheel cache (cleaned, 0 MB)
│   ├── npm\                         # npm tarball cache (cleaned, ~0.2 MB)
│   └── temp\                        # Temporary rendering scratchpad
│
├── models\                          # Preserved directory for future local ML models
├── thumbnails\templates\            # Preserved directory for thumbnail templates
├── start_studio.bat                 # One-click studio desktop launcher
├── start_studio.ps1                 # PowerShell studio launcher
├── run_in_env.ps1                   # Local PowerShell environment runner
├── run_in_env.cmd                   # Local CMD environment runner
├── env.ps1                          # Environment variable configuration
├── run_pipeline.bat                 # Batch pipeline trigger
└── README.md                        # Quickstart and overview
```

---

## 2. Key Resource Invariants

- **User Assets:** `Youtube Channel Src/` is preserved untouched.
- **Reference Styles:** All four reference HTML files in `docs/reference_styles/` are verified and unmodified.
- **Database Safety:** `backend/studio.db` was backed up to `backend/studio.db.bak_20261110_050337` prior to operations.
- **Runtime Integrity:** `tools/python/`, `tools/nodejs/`, `tools/ffmpeg/`, `tools/uv/`, and `tools/downloads/python-embed.zip` are intact.
- **Test Status:** 13/13 backend audit tests pass, 3/3 job manager tests pass, 3/3 stick animation tests pass.
