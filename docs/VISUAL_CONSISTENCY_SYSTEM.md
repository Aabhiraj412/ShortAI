# Visual Consistency System Specification
**Project Directory:** `D:\AI Arts\AI\`  
**Date:** November 10, 2026  
**Author:** Senior Software Architect, Animation Specialist & Technical Director  
**Target Medium:** YouTube Shorts (1080x1920 Vertical, 9:16 Aspect Ratio, 30.0 FPS)

---

## 1. Architectural Mandate: Strict Single-Style-Per-Video

In previous iterations of the Hybrid Visual Engine, different scenes within a single video arbitrarily switched between disjoint visual languages—jumping from cartoon flat vectors to financial coordinate grids, then blackboard chalk drawings, and finally kinetic creator slams. This created severe aesthetic dissonance, disoriented viewers within the first 3 seconds, and prevented the establishment of a cohesive channel identity.

### The Single-Style Rule
> **Every YouTube Short must select exactly ONE primary visual style during the storyboard planning phase and maintain that aesthetic language across all scenes from timestamp $t = 0.0\text{s}$ to the final frame.**

Once a primary style is assigned (`pop_info`, `data_story`, `chalk_talk`, or `creator_motion`):
1. **Typography is Locked:** Only the approved typeface pairings for that style may be rendered.
2. **Color Palette is Locked:** All text, cards, outlines, accents, and fills must strictly adhere to the style's color tokens.
3. **Background Treatment is Locked:** The environmental surface (warm paper, deep navy ink, green slate tooth, or dark studio grain) remains continuous across scene transitions.
4. **Motion Language is Locked:** Easing curves, entrance velocities, and physical metaphors (elastic pops, monotone splines, chalk skips, or kinetic slams) must stay uniform.
5. **Sound Design is Locked:** Procedural audio synthesis stems and musical beds must align with the visual metaphor.

---

## 2. The Four Visual Style Design Systems

```
┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                       VISUAL STYLE TAXONOMY & DESIGN SYSTEMS                                   │
├───────────────────┬────────────────────────────┬────────────────────────────┬───────────────────────────────────┤
│ Style             │ Pop Info                   │ Data Story                 │ Chalk Talk                        │
├───────────────────┼────────────────────────────┼────────────────────────────┼───────────────────────────────────┤
│ Target Topics     │ Psychology, Habits, Health │ Economics, Tech, Climate   │ Physics, Math, Deep Philosophy    │
│ Typeface Primary  │ Fredoka (600/700)          │ Archivo (800/900)          │ Cabin Sketch (700)                │
│ Typeface Secondary│ Fredoka (400/500)          │ IBM Plex Mono (500/600)    │ Kalam (400/700)                   │
│ Background        │ Warm Off-White (#F5F7F0)   │ Deep Ink Slate (#0F141C)   │ Blackboard Green (#24392F)        │
│ Accent Colors     │ Coral (#E85D4A), Teal      │ Amber (#F2C14E), Cyan      │ Yellow (#F4D35E), Chalk Blue      │
│ Motion Dynamics   │ Elastic Out-Back, Bounces  │ Monotone Splines, Smooth   │ Mechanical skips, hand-drawn path │
│ Sound Metaphor    │ Wooden pops, airy whooshes │ Crisp glass plucks, hum    │ Chalk tooth scratch, felt eraser  │
└───────────────────┴────────────────────────────┴────────────────────────────┴───────────────────────────────────┤
│ Style (4th): Creator Motion Graphics                                                                            │
├─────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Target Topics: Productivity, Social Media Strategy, Growth Hacks, Fast Creator Edits                           │
│ Typeface Primary: Anton (900 Heavy Display) | Secondary: Inter (600/700) / Instrument Serif (Italic)          │
│ Background: Dark Studio Graphite (#0B0B0D) with 24 FPS Film Grain & Radial Vignette                             │
│ Accent Colors: Retention Yellow (#FFD60A), Coral Strike (#FF4D4D), Neon Green (#22C55E)                        │
│ Motion Dynamics: Exponential scale slams (2.2 -> 1.0), 3D perspective yaw tilts, yellow highlighter wipes      │
│ Sound Metaphor: Sub-bass 808 thump drops, felt squeaks, analog glitch static, whoosh risers                    │
└─────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 2.1 Pop Info (Illustrated Explainer)
- **Concept:** Warm, accessible, friendly explainer for self-improvement and psychology.
- **Color Tokens:**
  - Ink / Primary Text: `#1D2A2A`
  - Secondary Text: `#5A6E68`
  - Coral Accent: `#E85D4A`
  - Teal Accent: `#2A7B7B`
  - Yellow Accent: `#F4B942`
  - Warm Card Surface: `#FFFFFF`
  - Background: `#F5F7F0`
- **Animation Grammar:**
  - Entrance: Elastic overshoot `CM.ez.outBack(u, 1.4)`.
  - Stagger: Items pop into view with a 0.25s cadence.
  - Character Motion: Continuous organic breathing sway (`Math.sin(t * 3.0) * 4px`) and periodic natural eye-blinks.
- **Transitions:** Iris circle wipes centered on the primary visual element or soft horizontal slide pushes.

### 2.2 Data Story (Editorial Data Journalism)
- **Concept:** Authoritative, clean, quantitative journalism inspired by The Economist and Financial Times.
- **Color Tokens:**
  - White Ink / High-Contrast Text: `#EEF1F5`
  - Dimmed Grid / Axis Labels: `#7C8BA1`
  - Card Surface: `#161D28`
  - Amber Data Accent: `#F2C14E`
  - Teal Flow Accent: `#4ECDC4`
  - Background: `#0F141C`
- **Animation Grammar:**
  - Monotone cubic Hermite splines with glowing tracer beads.
  - Numbers counter: Interpolated numerical rollups (`2.50B` $\rightarrow$ `8.10B`).
  - Bar charts: Cubic ease-out vertical growth from zero baseline.
- **Transitions:** Vertical slide reveal with coordinate line sweeps; permanent source citation pinned at $y = 1680$.

### 2.3 Chalk Talk (Hand-Drawn Slate)
- **Concept:** Intimate classroom blackboard lecture inspired by 3Blue1Brown and Veritasium.
- **Color Tokens:**
  - Primary Chalk: `#F2EFE6`
  - Yellow Chalk: `#F4D35E`
  - Blue Chalk: `#93CBEF`
  - Pink Chalk: `#F28E8E`
  - Board Surface: `#24392F` with procedural chalk dust texture.
- **Animation Grammar:**
  - Active Chalk Stick: A physically rendered white/yellow chalk cylinder ($14 \times 44\text{px}$) with directional rotation tracking writing coordinates.
  - Stroke Realism: Chalk strokes skip across the slate tooth using randomized dashed jitter intervals.
  - Erase Mechanic: Wooden felt eraser sweeps across the board with a semi-transparent chalk smudge wake.
- **Transitions:** Full felt eraser wipe from top-left to bottom-right across scene changes.

### 2.4 Creator Motion Graphics (High-Energy Kinetic)
- **Concept:** Punchy, high-retention creator content for mobile engagement.
- **Color Tokens:**
  - Background: `#0B0B0D`
  - Primary Display Text: `#FFFFFF`
  - Secondary Readout Text: `#A1A1AA` (Tailwind Zinc 400, WCAG AA compliant $> 7:1$)
  - Retention Yellow: `#FFD60A`
  - Coral Strike: `#FF4D4D`
  - Card Surface: `#16161A` with border `#26262B`
- **Animation Grammar:**
  - Kinetic Slams: Rapid exponential scale bounce ($2.2 \times \rightarrow 1.0\times$) with zero overshoot lag.
  - Strikeout: Horizontal red marker line crossing out forbidden words.
  - Highlighter: Yellow transparent swipe revealing behind headline text.
  - 3D Mockup Card: Dynamic yaw perspective tilt using trigonometric projection matrix.
- **Transitions:** Zoom punches and glitch crossfades.

---

## 3. Media Integration Guidelines (Photographs & External Assets)

When a script requires supporting real-world assets (e.g. historical photos, satellite maps, hardware schematics), the asset must NOT be pasted as a raw rectangular overlay. It must be styled to match the active visual identity:

### 3.1 Photographic Integration by Style
1. **Under `pop_info`:**
   - Real photos must be enclosed inside a rounded card with a 32px corner radius, a 6px solid border (`#FFFFFF`), and a soft warm drop shadow (`rgba(29, 42, 42, 0.12)`).
   - Card enters using `outBack` pop or gentle 4-degree tilt.
2. **Under `data_story`:**
   - Photos must receive a cool-toned desaturated color grade (80% saturation, +10% contrast, blue shadow tint).
   - Displayed inside an editorial coordinate frame with hairline cyan tick marks, coordinate numbers, and an editorial caption below in `IBM Plex Mono` 24px.
3. **Under `chalk_talk`:**
   - Real photos are rendered through a high-contrast Canny edge / line-art filter, displaying as white or yellow chalk line drawings against the slate background.
   - Alternatively, pinned as a "taped paper index card" on the blackboard with taped corners.
4. **Under `creator_motion`:**
   - Photos are placed inside a 3D browser/app window mockup with top dot controls (red, yellow, green), tilted at a dynamic 12-degree perspective angle, layered over 24 FPS film grain.

---

## 4. Automated Style Consistency Enforcement (`StyleConsistencyValidator`)

To prevent regression and guarantee that no developer or AI agent mixes styles, the system includes `StyleConsistencyValidator` in `backend/app/rendering/styles/style_system.py`:

```python
validator = StyleConsistencyValidator(target_style=VisualStyle.POP_INFO)

# Validates scene beat transitions
for beat in storyboard.beats:
    validator.validate_beat(beat)

# Throws StyleMixingError if any beat specifies a mismatched engine or theme
```

### Validation Rules Enforced by the Validator:
1. **Forbidden Modality Switching:** If `target_style` is `CHALK_TALK`, any beat referencing `CREATOR_MOTION` kinetic slams or `DATA_STORY` spline graphs immediately halts generation with `StyleMixingError`.
2. **Transition Grammar Check:** Cross-style transitions (such as an iris wipe directly into a chalk felt erase) are blocked.
3. **Typography & Palette Audit:** In `QualityGate.check_01_style_consistency`, every scene's rendered text elements are compared against the active theme's approved font families and color palette.
