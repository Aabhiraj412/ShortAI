# Hybrid Dual-Engine Rendering Architecture & Implementation Guide
**Project Directory:** `D:\AI Arts\AI\`  
**Date:** November 10, 2026  
**Author:** Senior Software Architect, Animation Specialist & Technical Director  
**Target Hardware:** AMD Ryzen 7 3700U (4 Cores / 8 Threads, 2.3–4.0 GHz), 16 GB DDR4 RAM, Integrated AMD Radeon Vega 10  
**Target Medium:** YouTube Shorts (1080x1920 vertical, 9:16 aspect ratio, 30.0 FPS, H.264 / AAC)

---

## 1. Executive Summary

To elevate the visual production quality of the AI YouTube Shorts Studio from basic 2D line sketches to broadcast-grade educational content, we have implemented a **Hybrid Dual-Engine Rendering Architecture**.

The system dynamically balances two specialized rendering engines with a universal FFmpeg compositor:
1. **Engine 1 (Python Photographic Canvas Engine):** Executes high-resolution photographic pans, Ken Burns zooms, comparative split-screens, and ASS subtitles at **14–17 FPS**.
2. **Engine 2 (Browser Canvas Evaluator Engine):** Executes rich code-driven vector animations, monotone cubic splines, kinetic typography, and 3D tilts via Headless Microsoft Edge (Chromium CDP) at **16–21 FPS**.
3. **Universal FFmpeg Compositor:** Stitches multi-engine video clips, layers procedural Web Audio sound stems, ducks ambient music beds (-14 dB), and encodes the final vertical Short.

```
                               ┌────────────────────────────────────────────────────────┐
                               │             STORYBOARD BEATS SPECIFICATION             │
                               └───────────────────────────┬────────────────────────────┘
                                                           │
                                                           ▼
                                      ┌──────────────────────────────────────────┐
                                      │        HybridVisualEngine Router         │
                                      └────────────┬─────────────────────────────┘
                                                   │
                         ┌─────────────────────────┴─────────────────────────┐
                         ▼                                                   ▼
      ┌─────────────────────────────────────┐             ┌─────────────────────────────────────┐
      │   Engine 1: Python Photographic     │             │     Engine 2: Headless Chromium     │
      │   (Pillow / Numpy / FreeType)       │             │     (Microsoft Edge via CDP)        │
      ├─────────────────────────────────────┤             ├─────────────────────────────────────┤
      │ • Real Photos & Space Imagery       │             │ • Pop Info (Flat-Vector Explainer)  │
      │ • Ken Burns Camera Pan & Zoom       │             │ • Data Story (Monotone Splines)     │
      │ • Split-Screen Comparative Layouts  │             │ • Chalk Talk (Slate & Chalk Stick)  │
      │ • Radial Vignettes & Film Grain     │             │ • Creator Motion (Anton Slams & 3D) │
      └──────────────────┬──────────────────┘             └──────────────────┬──────────────────┘
                         │                                                   │
                         │ Raw Video Stream (rgb24)                          │ MJPEG Frame Stream & Web Audio WAV
                         ▼                                                   ▼
      ┌─────────────────────────────────────────────────────────────────────────────────────────┐
      │                         Universal FFmpeg Compositor & Audio Mux                         │
      ├─────────────────────────────────────────────────────────────────────────────────────────┤
      │ • Seamless Concat Demuxer                                                               │
      │ • Procedural Web Audio SFX Stems                                                        │
      │ • Sidechain-Ducked Ambient Tech Music (-14 dB under voiceover)                          │
      │ • Synchronized Edge-TTS Voiceover & ASS Subtitles                                       │
      │ • Output: 1080x1920 @ 30.0 FPS, H.264 High Profile, AAC 48kHz Stereo                   │
      └─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Directory Structure & Module Layout

The Hybrid Visual Engine resides entirely in `backend/app/rendering/` without modifying or deleting existing database, queue, or TTS logic:

```
backend/app/rendering/
├── __init__.py                # Package exports (HybridVisualEngine, SceneBeat, StoryboardPlan)
├── types.py                   # Dataclasses & Enums (EngineType, VisualModality, Telemetry)
├── engine.py                  # Master HybridVisualEngine coordinator
├── engines/
│   ├── browser_canvas/        # Headless Chromium Canvas Engine
│   │   ├── __init__.py
│   │   ├── client.py          # Native Python CDP WebSocket client
│   │   └── runner.py          # Process manager, seek loop, MJPEG pipe, SFX extractor
│   └── python_canvas/         # Python Photographic Canvas Engine
│       ├── __init__.py
│       ├── photo_scene.py     # Ken Burns camera pan/zoom, vignette, grain
│       └── split_screen.py    # Stacked/horizontal comparative layouts
├── styles/                    # Portrait 1080x1920 Style Adapters
│   ├── pop_info/adapter.html
│   ├── data_story/adapter.html
│   ├── chalk_talk/adapter.html
│   └── creator_motion_graphics/adapter.html
└── compositing/               # Universal Compositor
    ├── __init__.py
    └── compositor.py          # Concat demuxer, multi-track audio mixer, ducking
```

---

## 3. Communication Protocols & Streaming Pipeline

### 3.1 CDP WebSocket Architecture (`client.py`)
Rather than pulling in heavyweight third-party packages, the system uses a custom Python WebSocket client to speak JSON-RPC 2.0 directly to Microsoft Edge:
- **Port:** Local ephemeral or default `9222`.
- **Payload Capacity:** 64 MB buffer limit for large base64 canvas exports and synthesized audio WAVs.
- **Asynchronous Correlated Dispatch:** Requests and responses are mapped by auto-incrementing integer IDs without blocking the Python event loop.

### 3.2 High-Throughput Canvas Streaming (`runner.py`)
To achieve 17–21 FPS without writing thousands of loose frame images to disk, frames are extracted in a single combined round-trip:
```javascript
window.seek(t);
document.getElementById('stage').toDataURL('image/jpeg', 0.88);
```
The resulting base64 JPEG payload is decoded in memory and written directly into FFmpeg `stdin`:
```bash
ffmpeg -y -f image2pipe -vcodec mjpeg -framerate 30 -i pipe:0 -c:v libx264 -preset veryfast -pix_fmt yuv420p output.mp4
```
**Key Reliability Guard:** Standard error (`stderr`) is redirected to `DEVNULL` or read asynchronously to prevent OS pipe deadlocks during long renders.

### 3.3 Procedural Audio Synthesis Extraction (`OfflineAudioContext`)
Each reference style features an integrated procedural sound synthesis kit (`SFX`). The runner extracts this audio by invoking `window.SFX.render`:
1. Creates an in-browser `OfflineAudioContext(2, duration * 48000, 48000)`.
2. Evaluates the sound score deterministically.
3. Packages the resulting 32-bit floating point PCM into a standard 16-bit 48kHz stereo RIFF WAV header in JavaScript.
4. Returns the base64 WAV string to Python, saving it as `beat_XX_sfx.wav` for final muxing.

---

## 4. Safe Zones & Aspect Ratio Adaptation

All video outputs adhere strictly to the YouTube Shorts mobile safe zone specification:
- **Canvas Resolution:** $1080 \times 1920$ (9:16 aspect ratio).
- **Primary Content Band:** $y \in [280, 1380]$ (central 1100 pixels).
- **Upper Safe Zone ($y < 280$):** Reserved for YouTube Channel name, subscription button, and search header.
- **Lower Safe Zone ($y > 1380$):** Reserved for caption overlay, like/comment action buttons, and sound attribution marquee.

---

## 5. Fallback & Fault Tolerance

If Headless Chromium encounters an environmental fault, the `HybridVisualEngine`:
1. Catches the exception at the beat level.
2. Falls back to the native Python Stick Figure & Motion Graphics renderer (`stick_animation_service.py`), which runs at **29–34 FPS** on CPU.
3. Logs the failure to `logs/` and completes the Short without failing the scheduled production run.

---

## 6. Single-Style Architecture & Quality Gate

In Phase 4.2, the Hybrid Visual Engine was upgraded with:
1. **Single-Style Rule:** Strict prohibition of style mixing within a single video. Each video selects one primary style (`pop_info`, `data_story`, `chalk_talk`, or `creator_motion`) maintained across all scene beats.
2. **Deterministic Frame Pipeline:** Discrete virtual clock ($t = i / 30.0$) and `await document.fonts.ready` font rasterization barrier in `runner.py`.
3. **10-Point Automated Quality Gate:** Pre-release verification (`backend/app/rendering/quality/quality_gate.py`) auditing single-style consistency, 1080x1920 dimensions, 30.0 FPS timescale, PTS continuity, audio sync, audio headroom, safe zones, font readability, WCAG AA contrast, and font delivery.

See accompanying specifications:
- [Visual Consistency System](file:///D:/AI%20Arts/AI/docs/VISUAL_CONSISTENCY_SYSTEM.md)
- [Smooth Motion Engine](file:///D:/AI%20Arts/AI/docs/SMOOTH_MOTION_ENGINE.md)
- [Typography & Layout System](file:///D:/AI%20Arts/AI/docs/TYPOGRAPHY_AND_LAYOUT_SYSTEM.md)
- [Visual QC Report](file:///D:/AI%20Arts/AI/docs/VISUAL_QC_REPORT.md)

