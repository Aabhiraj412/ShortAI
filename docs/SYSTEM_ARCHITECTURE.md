# Evolved System Architecture — AI YouTube Shorts Studio

**Target Window:** Daily production: 11:00 PM IST (Asia/Kolkata), Publication: 10:00 AM IST  
**Runtime Model:** Local-First, Zero-Recurring Cost, Portable CPU Execution  
**Hardware Profile:** AMD Ryzen 7 3700U, 16 GB RAM, integrated graphics  

---

## 1. High-Level Architecture Diagram

```
                 WINDOWS TASK SCHEDULER
                           │
                11:00 PM IST (Asia/Kolkata)
                           │
                           ▼
                  FASTAPI BACKEND
             (REST API, SQLite, React UI)
                           │
                           ▼
                 PERSISTENT JOB QUEUE
            (Checkpoints, Heartbeats, Retries)
                           │
    ┌──────────────────────┼──────────────────────┐
    ▼                      ▼                      ▼
RESEARCH & SELECTION   SCRIPTWRITING       VISUAL DIRECTION
- RSS Feeds            - 50-60s Script     - Storyboard JSON
- Gemini/Offline Pool  - 120-135 Words     - Kinematics Poses
- Weighted Scoring     - 2s Curiosity Hook - Motion Graphics
    │                      │                      │
    └──────────────────────┼──────────────────────┘
                           │
                           ▼
             2D STICK-FIGURE & MOTION GRAPHICS
           - Clean Silhouettes & Natural Poses
           - Forward Kinematics (Walk, Jump, Fall)
           - Singularity & Spacetime Curvature HUD
           - Kinetic Typography & Floating Particles
                           │
                           ▼
                AUDIO & SUBTITLE SYNTHESIS
           - Neural Voiceover (Edge-TTS 7.3)
           - Millisecond ASS Subtitle Timestamps
           - Audio Ducking (-18dB BGM, SFX)
                           │
                           ▼
                   FFMPEG COMPOSITOR
           - Single-Pass 1080x1920 @ 30 FPS MP4
           - H.264 Video, AAC Audio
                           │
                           ▼
                15-POINT QUALITY CONTROL
           - 0 Black Frames, Exact Aspect Ratio
           - Stream Verification & Non-Empty Check
                           │
                           ▼
              YOUTUBE PUBLISHING ENGINE
           - OAuth 2.0 Client (Manual Approval Mode)
           - Private Upload & Thumbnail Assignment
           - Scheduled Release for 10:00 AM IST
```

---

## 2. Persistent Job State Machine

Each production job runs through discrete, checkpointed stages:

```
[PENDING] ──> [RESEARCH] ──> [TOPIC_SELECTION] ──> [SCRIPT]
                                                      │
[ANIMATION] <── [SUBTITLES] <── [VOICEOVER] <─────────┘
     │
     └──> [THUMBNAIL] ──> [SEO] ──> [QUALITY_CHECK] ──> [COMPLETED]
```

- **Resumption**: If a crash occurs at `ANIMATION`, the worker restarts from `ANIMATION` without regenerating the script or audio.
- **Heartbeat**: Recorded every stage. Abandoned jobs (>5 min without heartbeat) transition to `RETRYING`.
- **Max Retries**: 3 attempts with exponential backoff before marking `FAILED`.
- **Duplicate Prevention**: Guaranteed single job per date string (`YYYY-MM-DD`).
