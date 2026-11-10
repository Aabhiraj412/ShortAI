# CrazzyDocs Editorial Text System

## 1. Overview & Core Philosophy

In traditional short-form video pipelines, spoken narration is transcribed verbatim as subtitles across 100% of the screen duration. While intended for clarity, this approach creates substantial cognitive friction:
1. **Screen Clutter**: Verbatim subtitles cover critical visual illustrations, charts, animations, and character emotions.
2. **Reading vs. Watching**: Viewers focus on reading full sentences rather than engaging with visual storytelling.
3. **Pacing Competition**: Static sentence streams fail to emphasize dramatic numbers, dates, and turning points.

The **CrazzyDocs Editorial Text System** solves this by replacing verbatim subtitles with **Power-Word Storytelling**:
- Spoken narration is delivered at natural, expressive pacing.
- The visual canvas displays **only focused 2–5 word power phrases** at key narrative moments.
- Between 60% and 70% of the total video duration remains completely free of on-screen text, creating essential **visual breathing room**.

---

## 2. Multi-Track Subtitle Architecture

The system decouples accessibility compliance from creative on-screen visual design:

| Subtitle Track | Destination Path | Content | Deployment |
|---|---|---|---|
| **Editorial Text (`.ass`)** | `media/subtitles/editorial/` | 2–5 word punchy power phrases styled per visual theme | **Burned into final video** |
| **Accessibility Captions (`.srt` / `.ass`)** | `media/subtitles/accessibility/` | Full word-for-word transcript with timestamps | Uploaded as Closed Captions (CC) |
| **Telemetry Transcript (`.json`)** | `media/subtitles/transcripts/` | Word-level timing telemetry, emotion tags, phoneme boundaries | Engine alignment and analytics |

---

## 3. Power-Word Extraction & Pacing Rules

The `EditorialTextEngine` (`backend/app/content/editorial_text.py`) analyzes narrative sentences and isolates high-impact concepts using semantic regex pattern extractors:

### 3.1 Extraction Priority
1. **Financial & Numerical Metrics**:
   - Matches: `$1.3B`, `90-DEGREE`, `ONLY 14 FINISHED`, `48 HOURS`.
   - Examples: `RAT POISON BREAKFAST`, `ONLY FOURTEEN THIRTY-TWO`.
2. **Dramatic Actions & Critical Turning Points**:
   - Matches: `CHASED INTO CORNFIELD`, `ONE-HOUR NAP`, `STRYCHNINE COCKTAIL`, `CARRIED TO GOLD`.
3. **Paradox & Curiosity Questions**:
   - Matches: `THE PARADOX?`, `WHY DOES MOON NOT FALL?`.
4. **Informational Core Words**:
   - Strips common grammatical stop words (`the`, `a`, `in`, `on`, `was`, `is`).
   - Isolates the 2–4 most meaningful keywords.

### 3.2 Timing & Breathing Room Invariants
- **Cue Duration**: Strictly clamped between **1.6 seconds and 2.4 seconds**.
- **Visual Breathing Gap**: At least **1.0 to 3.0 seconds** of clean gap with zero on-screen text between consecutive cues.
- **Narrative End Boundary**: The final cue must disappear at least **1.5 seconds before narrative conclusion**, guaranteeing zero visual clutter as the story finishes.

---

## 4. Typography & Safe Band Layout

Editorial text is rendered using Advanced SubStation Alpha (`.ass`) specifications calibrated for vertical 1080x1920 Shorts:

```
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.30,0:00:02.70,EditorialAlert,,0,0,0,,RAT POISON & BRANDY
Dialogue: 0,0:00:05.70,0:00:08.10,EditorialPrimary,,0,0,0,,ST LOUIS MARATHON
Dialogue: 0,0:00:13.57,0:00:15.17,EditorialPrimary,,0,0,0,,ONLY FOURTEEN THIRTY-TWO
Dialogue: 0,0:00:16.69,0:00:19.09,EditorialPrimary,,0,0,0,,CHASED INTO CORNFIELD
Dialogue: 0,0:00:23.36,0:00:25.76,EditorialPrimary,,0,0,0,,ONE-HOUR NAP
Dialogue: 0,0:00:31.81,0:00:34.21,EditorialAlert,,0,0,0,,STRYCHNINE COCKTAIL
Dialogue: 0,0:00:39.37,0:00:41.77,EditorialPrimary,,0,0,0,,CARRIED TO GOLD
Dialogue: 0,0:00:45.73,0:00:47.77,EditorialPrimary,,0,0,0,,MOST CHAOTIC RACE
```

### 4.1 Style-Specific Typography Mapping

| Visual Style | Primary Font | Point Size | Primary Color | Outline Color | Shadow |
|---|---|---|---|---|---|
| **Pop Info** | Fredoka | 82–86pt | Warm Amber (`#FFD60A`) / Coral (`#FF4D4D`) | Dark Slate (`#1D2A2A`) | Soft 4px |
| **Data Story** | Archivo | 76–80pt | Clean White (`#FFFFFF`) / Cyan (`#38BDF8`) | Deep Navy (`#0F172A`) | Crisp 3px |
| **Chalk Talk** | Kalam | 88–92pt | Chalk White (`#F8FAFC`) / Yellow Chalk (`#FEF08A`) | Slate Charcoal (`#1E293B`) | Smudge 2px |
| **Creator Motion** | Anton | 84–90pt | Electric Yellow (`#EAB308`) / Crimson (`#EF4444`) | Deep Obsidian (`#000000`) | Bold 5px |

### 4.2 Subtitle Safe Band
- **Vertical Placement**: `MarginV: 430` (centered at $y \approx 1490$ px in a 1920px height canvas).
- **Protection**: Clear of the top header safe area ($y: 0–280$) and clear of YouTube Shorts bottom UI controls and audio titles ($y: 1600–1920$).

---

## 5. Text Bounds Engine & Auto-Scaling (Stage 3.3.1)

To prevent visual clutter, label clipping, and box overflow across all video styles, the rendering pipeline integrates `TextBoundsEngine` (`backend/app/rendering/text/text_bounds_engine.py`):

### 5.1 Dynamic Font Scaling & Word Wrapping
1. **Measured Glyph Bounding Boxes**: Uses `draw.textbbox()` to measure exact rendered pixel widths for every line rather than estimating by character count.
2. **Progressive Step-Down Scaling**: When text exceeds the container width or height, font size is dynamically stepped down from `max_font_size` toward `min_font_size` until the text fits comfortably within container padding.
3. **Intelligent Greedy Wrapping**: Breaks long phrases into balanced lines while preserving semantic words.

### 5.2 Strict Safe Zone Enforcement (1080x1920 Vertical)
- **Horizontal Safe Margin**: Minimum 60px on both left and right edges ($60 \le x \le 1020$).
- **Top Safe Zone**: Avoids $y < 180\text{px}$ to clear YouTube Shorts header overlays.
- **Bottom Safe Zone**: Avoids $y > 1660\text{px}$ to clear YouTube mobile title, like/share buttons, and audio pill.
- **Editorial Subtitle Safe Band**: Sits between $y = 1450$ and $y = 1530\text{px}$ (`MarginV: 430`).

### 5.3 Automated Boundary Validator
Every render pass executes `validate_rendered_elements()`:
- Verifies that zero text overflows its container box.
- Verifies that all bounding boxes remain strictly inside the canvas.
- Rejects and fails QC if any text clipping or interface collision occurs.

