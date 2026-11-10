# Selective Impact Word Subtitle Architecture
## Eliminating Subtitle Clutter Through Timed Emphasis Bursts

**Author:** AI YouTube Shorts Studio Architecture Team  
**Date:** November 2026  
**Status:** Approved & Implemented (`backend/app/rendering/subtitles/impact_words.py`)  
**Standard:** 1080 × 1920 @ 30 FPS, Mobile Safe-Zone Compliant (y: 1380 - 1560)

---

## 1. Motivation: The Anti-Clutter Principle

In mainstream short-form content generation, automated tools routinely paste full word-by-word captions across the entire duration of the video. In an informative, high-production studio, this approach causes severe problems:

1. **Cognitive Fatigue:** Viewers are forced to read every word of conversational English they are already hearing, distracting them from the diagrams, data charts, and character animation.
2. **Visual Occlusion:** Continuous subtitle bars block up to 25% of the central canvas, destroying composition balance and obscuring fine details (e.g., chart axis labels, formulas, character expressions).
3. **Loss of Emphasis:** When every word is highlighted with identical neon yellow or cyan styling, nothing actually feels important.

### The Solution: The Selective Impact Word System
Instead of a continuous transcription stream, the studio uses **selective impact words and short phrases**:
- **1 to 4 Words Maximum:** Only the core concept, metric, or punchline is displayed.
- **$\sim 70\%$ Screen Silence:** The subtitle band remains completely clear for roughly 70% of the video runtime, allowing the primary visual animation to breathe.
- **Peak Synchronization:** Each burst is timed strictly to the spoken acoustic peak of the voiceover using millisecond word boundaries.

---

## 2. Distinction: Subtitles vs. On-Canvas Typography

A foundational rule of this architecture is the strict separation between two independent layers:

```
+-------------------------------------------------------------------------+
| CANVAS LAYER (0 - 1340)                                                |
| - On-canvas diagram titles, formulas, metric numbers, and charts.       |
| - Rendered directly by the graphics engine (Browser Canvas or Skia).     |
| - Statically and dynamically bound to coordinate space.                 |
+-------------------------------------------------------------------------+
| MOBILE SUBTITLE BAND (1380 - 1560)                                      |
| - Selective impact word overlays (e.g., "CHEAPEST POWER", "-89% DROP"). |
| - Rendered via Advanced SubStation Alpha (.ass) subtitle track.         |
| - Zero collision with canvas layer above, and zero collision with       |
|   YouTube platform UI (title, description, sound icon) below (1620+).  |
+-------------------------------------------------------------------------+
```

Under no circumstances may on-canvas visual text enter the $y \ge 1380$ band, and under no circumstances may subtitle overlays move into the $y \le 1340$ visual canvas zone.

---

## 3. Style-Specific Typography System

Subtitles inherit the visual identity of the active theme to preserve holistic design cohesion:

| Visual Style | Subtitle Font | Primary Color (BGR) | Outline Color | Font Size | Border Style |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Chalk Talk** | *Kalam* | `&H00E6EFEF` (Chalk White) | `&H0018261F` (Dark Slate) | $76\text{px}$ | Outline + Shadow |
| **Data Story** | *Archivo* | `&H004EC1F2` (Amber Sand) | `&H001C140F` (Deep Ink) | $78\text{px}$ | Heavy Outline |
| **Creator Motion** | *Anton* | `&H000AD6FF` (Highlighter Yellow) | `&H000D0B0B` (Near Black) | $92\text{px}$ | Impact Outline |
| **Pop Info** | *Fredoka* | `&H004A5DE8` (Vibrant Coral) | `&H002A2A1D` (Ink Black) | $76\text{px}$ | Rounded Pill |

---

## 4. Phase 4C 20-Second Sample Implementations

### A. Data Story (*Why Solar Energy Is Becoming Cheaper*)
- Total video duration: $20.76\text{s}$
- Total impact text display time: $6.6\text{s}$ (**$31.8\%$ active**, **$68.2\%$ clear**)

| Cue Time | Spoken Narration | Impact Phrase | Word Count |
| :--- | :--- | :--- | :--- |
| **1.2s - 3.2s** | *"Solar is now the cheapest electricity in history."* | `CHEAPEST POWER` | 2 words |
| **5.2s - 7.5s** | *"Since 2010, the cost collapsed by eighty-nine percent."* | `-89% COLLAPSE` | 2 words |
| **9.5s - 12.0s**| *"It follows Swanson's Law: every time global manufacturing capacity doubles..."* | `SWANSON'S LAW` | 2 words |
| **17.5s - 20.0s**| *"Today, nothing can compete with the economics of silicon."* | `UNBEATABLE SILICON` | 2 words |

---

### B. Chalk Talk (*Why Does the Moon Not Fall to Earth?*)
- Total video duration: $20.93\text{s}$
- Total impact text display time: $7.6\text{s}$ (**$36.3\%$ active**, **$63.7\%$ clear**)

| Cue Time | Spoken Narration | Impact Phrase | Word Count |
| :--- | :--- | :--- | :--- |
| **1.5s - 3.8s** | *"The Moon is constantly falling toward Earth, pulled by gravity."* | `PULLED BY GRAVITY` | 3 words |
| **7.5s - 9.8s** | *"Because the Moon also flies forward at one kilometer per second."* | `1 KM / SECOND` | 3 words |
| **12.0s - 14.5s**| *"Newton proved this: the Moon is in perpetual orbit..."* | `ORBITAL VELOCITY` | 2 words |
| **16.5s - 19.5s**| *"...forever falling and forever missing!"* | `FOREVER MISSING` | 2 words |

---

### C. Creator Motion Graphics (*Why Most People Lose Interest in Seconds*)
- Total video duration: $19.00\text{s}$
- Total impact text display time: $7.5\text{s}$ (**$39.5\%$ active**, **$60.5\%$ clear**)

| Cue Time | Spoken Narration | Impact Phrase | Word Count |
| :--- | :--- | :--- | :--- |
| **1.2s - 3.5s** | *"You have three seconds before a viewer swipes away forever."* | `FIRST 3 SECONDS` | 3 words |
| **7.5s - 9.8s** | *"The fatal mistake creators make is polite greetings. Cut the fluff immediately!"* | `CUT THE FLUFF` | 3 words |
| **11.0s - 13.2s**| *"Start inside the conflict to trap curiosity."* | `OPEN THE LOOP` | 3 words |
| **15.5s - 18.2s**| *"When your first three seconds hook the viewer, the algorithm explodes your reach!"* | `EXPLOSIVE REACH` | 2 words |

---

## 5. ASS File Architecture & FFmpeg Burn-In

The generator produces standards-compliant Advanced SubStation Alpha (`.ass`) files using the `MarginV=410` parameter, placing the text baseline at exactly $y = 1920 - 410 = 1510\text{px}$:

```ini
[Script Info]
Title: Selective Impact Words (data_story)
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: ImpactPrimary,Archivo,78,&H004EC1F2,&H00FFFFFF,&H001C140F,&H80000000,-1,0,0,0,100,100,2,0,1,5,4,2,80,80,410,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.20,0:00:03.20,ImpactPrimary,,0,0,0,,CHEAPEST POWER
Dialogue: 0,0:00:05.20,0:00:07.50,ImpactPrimary,,0,0,0,,-89% COLLAPSE
Dialogue: 0,0:00:09.50,0:00:12.00,ImpactPrimary,,0,0,0,,SWANSON'S LAW
Dialogue: 0,0:00:17.50,0:00:20.00,ImpactPrimary,,0,0,0,,UNBEATABLE SILICON
```

Burn-in is executed cleanly by `UniversalCompositor` via the FFmpeg `ass` video filter:
```bash
ffmpeg -i video_raw.mp4 -vf "ass='path/to/subtitles.ass'" -c:v libx264 -crf 18 final_video.mp4
```

---

## 6. Automated Quality Gate Checks

1. **Word Count Ceiling:** Cues exceeding 4 words are automatically rejected or truncated to the first 4 words.
2. **Safe Band Invariant:** All subtitle bounding boxes must satisfy $y_{\text{top}} \ge 1380\text{px}$ and $y_{\text{bottom}} \le 1560\text{px}$.
3. **Contrast Verification:** The subtitle text outline and primary color must produce a WCAG AA contrast ratio of $\ge 4.5:1$ against the underlying video background.
