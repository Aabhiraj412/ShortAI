# AI-Powered Automated YouTube Shorts Studio — System Architecture

## 1. Executive Summary

The AI-Powered Automated YouTube Shorts Studio is an autonomous end-to-end production platform designed to operate on a Windows 11 workstation (AMD Ryzen 7 3700U, 16 GB RAM, AMD Radeon RX Vega 10 integrated graphics). 

All tools, runtimes, models, and assets reside exclusively within `D:\AI Arts\AI` without modifying system files, registry, or global paths. The architecture is engineered for low CPU overhead, zero mandatory cloud costs, and high-quality 2D/2.5D vertical (1080x1920, 30 FPS) video output.

---

## 2. System Architecture Diagram

```
+---------------------------------------------------------------------------------------+
|                                  REACT DASHBOARD                                      |
|  [Overview] [Research] [Script Studio] [Storyboard] [Video Studio] [Thumbnail] [SEO]  |
+-------------------------------------------+-------------------------------------------+
                                            | REST API / WebSocket
+-------------------------------------------v-------------------------------------------+
|                                 FASTAPI BACKEND                                       |
|  - API Router & Controllers          - Persistent Task Worker                         |
|  - APScheduler (10:00 AM IST)        - SQLite Database (SQLAlchemy ORM)               |
+-------------------------------------------+-------------------------------------------+
                                            |
         +----------------------------------+----------------------------------+
         |                                  |                                  |
         v                                  v                                  v
+------------------+              +-------------------+              +------------------+
|  AI AGENT SUITE  |              | ANIMATION ENGINE  |              | MEDIA PROCESSING |
| 1. Trend Res.    |              | - Motion Canvas   |              | - Portable FFmpeg|
| 2. Topic Sel.    |              | - Reusable SVGs   |              | - Kokoro/EdgeTTS |
| 3. Scriptwriter  |              | - Character Rig   |              | - Subtitles ASS  |
| 4. Visual Dir.   |              | - Camera & FX     |              | - SVG Thumbnails |
| 7. SEO & Publish |              | - 9:16 Templates  |              | - Quality Checks |
+------------------+              +-------------------+              +------------------+
```

---

## 3. Logical Agents

1. **Agent 1: Trend Researcher**:
   - Gathers candidate topics across tech, science, space, history, mysteries, psychology, and facts using Google Trends, RSS feeds, and YouTube search.
   - Computes weighted score: `Score = (Freshness * 0.25) + (SearchInterest * 0.25) + (Engagement * 0.20) + (StoryPotential * 0.20) + (Uniqueness * 0.10)`.
   - Filters out previously used topics stored in the database.

2. **Agent 2: Topic Selector**:
   - Evaluates top 10 candidates; applies channel niche alignment and performance history.
   - Outputs the winning topic or accepts manual topic override from the dashboard.

3. **Agent 3: Scriptwriter**:
   - Generates 50–60s script (115–135 words calibrated to TTS words-per-minute).
   - Format: 2-second hook -> tension/mystery -> factual body with references -> climax -> memorable CTA.
   - Verifies factual grounding; stores sources and citations alongside script text.

4. **Agent 4: Visual Director**:
   - Transforms approved script into structured storyboard JSON.
   - Each scene details: scene ID, timing, character expressions/gestures, background SVG, camera action (pan/zoom/parallax), overlays, sound effects, and animation style (Explainer, Sci-Tech, History, Mystery, Infographics).

5. **Agent 5: Animation Engine**:
   - Motion Canvas (TypeScript) rendering 1080x1920 @ 30 FPS.
   - Modular SVG character rigs (head, eyes, mouth phonemes, limbs, torso) with idle breathing, blinking, talking, and walking cycles.
   - Scene transitions, kinetic text overlays, and dynamic background layers.

6. **Agent 6: Audio & Video Editor**:
   - Narration generation via lightweight offline/free TTS (Kokoro/Edge-TTS/Piper).
   - Generates word-level synchronized subtitle files (ASS/SRT) with dynamic yellow/cyan active word highlight.
   - Mixes voiceover, subtle ducked background music (-18dB), and contextual sound effects.
   - FFmpeg composite: 1080x1920 vertical video, H.264 (yuv420p), AAC audio.

7. **Agent 7: SEO, Thumbnail & Publisher**:
   - Programmatic high-contrast 9:16 SVG/HTML thumbnail generator with bold typography and focal character/subject.
   - SEO generation: Click-worthy title (<60 chars), description with timestamps, tags, hashtags (#Shorts, #Science, etc.).
   - YouTube Data API v3: OAuth 2.0 flow, video upload, processing monitor, thumbnail upload, publication scheduled for 10:00 AM IST.

---

## 4. Hardware Optimization & Constraints

- **Execution Path**: Portable runtimes in `D:\AI Arts\AI\tools` (Node.js LTS, Portable Python 3.11, FFmpeg static).
- **CPU Offload**: Multi-threaded FFmpeg rendering (`-threads 6`), vector-based SVG rendering in Motion Canvas instead of heavy 3D or diffusion neural networks.
- **RAM Footprint**: Under 3 GB peak working set during rendering; background tasks run sequentially.
- **Storage Strategy**: Fast scratch directory in `D:\AI Arts\AI\cache`, persistent renders in `media/`, automatic cleanup of intermediate image frames after video compilation.

---

## 5. Phased Implementation Roadmap

- **Phase 1: Foundation & Tooling Setup**: Download & configure portable Python, Node.js, FFmpeg in `tools/`; build FastAPI core, SQLite DB, configuration system, and React skeleton.
- **Phase 2: Complete Sample Video Engine**: Produce an end-to-end 15-second sample Short with character animation, voiceover, synchronized highlight subtitles, background music, sound effects, thumbnail, and final MP4. Verify local rendering on CPU.
- **Phase 3: AI Content Generation Pipeline**: Implement Gemini API integration with local fallback; build Trend Researcher, Topic Selector, Scriptwriter, and Visual Director.
- **Phase 4: Motion Canvas Animation Automation**: Link storyboard JSON to reusable templates, character rigs, and automated headless rendering.
- **Phase 5: Audio & Video Assembly**: Complete TTS pipeline, ASS subtitle generator, audio ducking mixer, FFmpeg compositor, and 15-point Quality Control engine.
- **Phase 6: Thumbnail & SEO Studio**: Programmatic high-converting thumbnail generator, metadata engine, thumbnail preview/editor.
- **Phase 7: YouTube Publishing**: OAuth 2.0 integration, resilient upload service, thumbnail API upload, scheduling.
- **Phase 8: Automation, Persistent Scheduler & Analytics**: APScheduler daily runner (10:00 AM IST), failure recovery, analytics collection, and full React dashboard.
