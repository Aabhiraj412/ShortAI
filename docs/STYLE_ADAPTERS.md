# Reference Style Adapters Specification & Vertical Framing Guide
**Project Directory:** `D:\AI Arts\AI\`  
**Date:** November 10, 2026  
**Author:** Senior Software Architect, Animation Specialist & Technical Director  
**Target Medium:** YouTube Shorts (1080x1920 Vertical, 9:16 Aspect Ratio)

---

## 1. Architectural Philosophy: Zero Modifications to Ground Truth

The four original reference files located in `docs/reference_styles/`:
1. `docs/reference_styles/pop_info.html`
2. `docs/reference_styles/data_story.html`
3. `docs/reference_styles/chalk_talk.html`
4. `docs/reference_styles/creator_motion_graphics.html`

**Remain 100% byte-for-byte unchanged.**

To adapt these styles to YouTube Shorts vertical video format ($1080 \times 1920$, 9:16 aspect ratio), we implemented four dedicated portrait adapters in `backend/app/rendering/styles/`. Each adapter preserves the mathematical motion functions (`CM`), visual aesthetics, and sound score (`SFX`) while restructuring the layout for mobile screens and enforcing safe zones ($y \in [280, 1380]$).

---

## 2. Style-by-Style Adapter Technical Details

### 2.1 Pop Info (`backend/app/rendering/styles/pop_info/adapter.html`)
- **Visual Family:** Illustrated Explainer (Flat Vector).
- **Typography:** `Fredoka` (Google Fonts: 400, 500, 600, 700).
- **Original Space:** Designed in $680 \times 383$ horizontal space.
- **Vertical 1080x1920 Framing:**
  - **Upper Wall & Lighting ($y \in [0, 480]$):** Soft warm wall `#D7DCCB` with subtle ceiling perspective.
  - **Middle Stage ($y \in [480, 1380]$):** 
    - Flanking grocery shelves sliding in dynamically from left and right with colorful product jars.
    - Flat-vector cartoon shopper with breathing and walking sway standing at $(x=540, y=1420)$.
    - Expressive eye blinking keyed to `(t % 3.5)`.
    - Main thought bubble with soft drop shadow popping up at $(x=540, y=780)$ using `eOutBack(u, 1.4)`.
    - Dotted path connecting character to shelf.
  - **Atmosphere:** 35 ambient dust motes drifting across the full 1920 vertical canvas.
  - **Transitions:** Iris opening wipe at $t < 0.6s$ centered at $(540, 960)$.
- **Sound Score:** Gentle toy-like pops, airy whooshes, wooden taps, and warm background triangle synth.

---

### 2.2 Data Story (`backend/app/rendering/styles/data_story/adapter.html`)
- **Visual Family:** Editorial Data Journalism.
- **Typography:** `Archivo` (800, 900) & `IBM Plex Mono` (500, 600).
- **Original Space:** $1280 \times 720$ horizontal.
- **Vertical 1080x1920 Framing:**
  - **Background ($y \in [0, 1920]$):** Deep ink background `#0f141c` with subtle coordinate grid lines and radial teal spotlight.
  - **Header & Metric Counter ($y \in [280, 520]$):**
    - Pill tag: `GLOBAL DEMOGRAPHICS` in teal border.
    - High-impact dynamic population counter growing smoothly from `2.50B` (1950) to `8.10B` (2026).
  - **Monotone Spline Chart Card ($y \in [580, 1200]$):**
    - High-contrast card (`#161d28`) with coordinate grid.
    - Animated monotone cubic spline path with area gradient fill.
    - Glowing tracer head dot at the tip of the curve with radial blur shadow.
    - Milestone year markers along the baseline: 1950, 1970, 1990, 2010, 2026.
  - **Comparative Ratio Cards ($y \in [1270, 1550]$):**
    - Dual side-by-side cards: Baseline (1950: `2.5B`) vs. Surge (2026: `+224%`).
  - **Permanent Citation ($y = 1680$):** `SOURCE: UN WORLD POPULATION PROSPECTS`.
- **Sound Score:** Thoughtful pentatonic triangle plucks synchronized to milestone population thresholds.

---

### 2.3 Chalk Talk (`backend/app/rendering/styles/chalk_talk/adapter.html`)
- **Visual Family:** Hand-Drawn Classroom Blackboard.
- **Typography:** `Cabin Sketch` (700) & `Kalam` (400, 700).
- **Original Space:** Continuous board of $2700 \times 720$.
- **Vertical 1080x1920 Framing:**
  - **Board Slate ($y \in [0, 1920]$):** Forest green chalkboard `#24392f` with natural tooth texture and oak wood perimeter border.
  - **Title ($y \approx 360$):** `THE EINSTEIN FIELD EQUATIONS` written in warm yellow chalk (`#f4d35e`).
  - **Formula Staging ($y \in [480, 680]$):**
    - Hand-drawn formula: $G_{\mu\nu} + \Lambda g_{\mu\nu} = \frac{8\pi G}{c^4} T_{\mu\nu}$ chalked character-by-character.
    - Chalk strokes skip over the slate tooth using dashed jitter paths.
    - Yellow underline stroke and pink chalk emphasis circle around curvature tensor $G_{\mu\nu}$.
  - **Curved Spacetime Diagram ($y \in [780, 1200]$):**
    - Concentric blue chalk curved coordinate lines.
    - Central yellow chalk star representing mass/energy.
  - **Active Chalk Stick:** Realistic chalk stick body and tip tracking active drawing coordinates.
  - **Felt Eraser Wipe ($t \in [6.8s, 8.2s]$):**
    - Wooden-backed felt eraser wipes clean the takeaway quote with realistic chalk smudge trail.
- **Sound Score:** High-frequency chalk noise bursts, felt eraser swishes, and room tone.

---

### 2.4 Creator Motion Graphics (`backend/app/rendering/styles/creator_motion_graphics/adapter.html`)
- **Visual Family:** Kinetic Typography & Fast Creator Edits.
- **Typography:** `Anton` (900 caps), `Instrument Serif` (italic), `Inter` (700, 800).
- **Original Space:** $1280 \times 720$ horizontal.
- **Vertical 1080x1920 Framing:**
  - **Editing Room Surface ($y \in [0, 1920]$):** Near-black `#0b0b0d` with animated 24 FPS procedural film grain and radial vignette.
  - **Kinetic Word Slams ($y \in [340, 860]$):**
    - Beat 1 (0.2s): `STOP` (Anton 150px) slams with exponential scale bounce ($2.2 \rightarrow 1.0$).
    - Beat 2 (0.9s): `CREATING` (Anton 130px).
    - Beat 3 (1.7s): `BORING` (Coral `#ff4d4d`) with animated red strikethrough line (`strike()`).
    - Beat 4 (2.8s): Yellow felt highlighter swipe (`#ffd60a`) followed by `CONTENT` (Anton 160px).
  - **3D Tilted Card Mockup ($y \in [980, 1420]$):**
    - Analytics card with browser window dots, `+482%` metric, and status badge.
    - 3D perspective yaw transform (`Math.tan(yawAngle)`).
  - **Footer Hook ($y \approx 1580$):** `ENGINEERED FOR RETENTION`.
- **Sound Score:** Heavy low-frequency thump slam, felt marker squeaks, glitch bursts, and risers.

---

## 3. Export Hook Interface

Every adapter strictly implements the standard export interface:
```javascript
// Frame seek hook (t in seconds)
window.seek = (t) => explainer(ctx, clamp(t, 0, DURATION));

// Duration in seconds
window.DURATION = DURATION;

// Font rasterization readiness barrier
window.isReady = async () => {
  await window.__initPromise;
  return { total: document.fonts.size, loaded: document.fonts.status === 'loaded' };
};

// Procedural audio synthesis hook
window.scoreSound = scoreSound;
window.SFX = SFX;
```
This contract allows the `BrowserCanvasRunner` to render any adapter deterministically without knowing its internal implementation details.

---

## 4. Multi-Scene Production Architecture

In Phase 4.2, each portrait adapter was extended from a single static loop into a **full 4-scene narrative production**:
- **Pop Info (16.0s):**
  - Scene 1 ($0.0 - 4.0\text{s}$): The Cue (Aisle shelves slide in, character breathing, thought bubble pops).
  - Scene 2 ($4.0 - 8.0\text{s}$): The Friction (Red crossroads icon, friction warning, posture change).
  - Scene 3 ($8.0 - 12.0\text{s}$): The 2-Minute Rule (Spring clock, animated progress bar to 100%).
  - Scene 4 ($12.0 - 16.0\text{s}$): The 3 Laws of Habits (3 staggered bullet cards with checkboxes).
- **Data Story (16.0s):**
  - Scene 1 ($0.0 - 4.0\text{s}$): The Global Surge (Dynamic population counter 2.50B -> 8.10B, pill tag).
  - Scene 2 ($4.0 - 8.0\text{s}$): Cost Spline Curve (Monotone cubic spline, gradient fill, glowing tracer bead).
  - Scene 3 ($8.0 - 12.0\text{s}$): Comparative Bars (Solar vs Wind vs Gas growing vertical bars).
  - Scene 4 ($12.0 - 16.0\text{s}$): The Tipping Point (Takeaway card, permanent IEA citation at $y = 1680$).
- **Chalk Talk (16.0s):**
  - Scene 1 ($0.0 - 4.0\text{s}$): Equivalence Principle (Rocket elevator diagram, acceleration vector).
  - Scene 2 ($4.0 - 8.0\text{s}$): Einstein Field Equations (Hand-chalked formula, pink emphasis circle).
  - Scene 3 ($8.0 - 12.0\text{s}$): Spacetime Curvature (Concentric blue grid lines, yellow mass, bent ray).
  - Scene 4 ($12.0 - 16.0\text{s}$): Felt Eraser Wipe (Wooden eraser wipes slate, John Wheeler quote).
- **Creator Motion Graphics (16.0s):**
  - Scene 1 ($0.0 - 4.0\text{s}$): The Attention Hook (Kinetic slam "STOP CREATING", bounce scale).
  - Scene 2 ($4.0 - 8.0\text{s}$): The Retention Killer ("BORING" red strikeout, yellow highlighter swipe).
  - Scene 3 ($8.0 - 12.0\text{s}$): 3D Analytics Card (Yaw perspective transform, +482% retention metric).
  - Scene 4 ($12.0 - 16.0\text{s}$): The 3 Retention Pillars (Staggered number badges, film grain, vignette).

