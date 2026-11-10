# CrazzyDocs Visual Asset Design System

**Channel Identity:** CrazzyDocs  
**Production Standard:** 1080 × 1920 (9:16 Vertical Video) @ 30 FPS  
**Target Duration:** 50–60 Seconds (Default: 55.0 Seconds, including 2.5s Branded Outro)  
**Status:** Implemented & Verified (Phase 5 Stage 3)  

---

## 1. Executive Overview

The CrazzyDocs Visual Asset Design System establishes a unified, modular visual foundation across all automated YouTube Shorts production pipelines. Moving beyond legacy ad-hoc assets, this system enforces distinct visual languages tailored to each of the studio's four approved video styles:

1. **Pop Info**: Warm, friendly, approachable flat-vector educational explainer featuring the signature "Dr. Doc" character.
2. **Data Story**: Authoritative financial, statistical, and geopolitical data journalism with clean isometric grids and verified photojournalism.
3. **Chalk Talk**: Authentic hand-drawn classroom chalkboard aesthetics featuring chalk stick cursors, slate textures, and hand-drawn diagrams.
4. **Creator Motion Graphics**: High-energy kinetic typography, 3D yaw containers, smartphone mockups, and dynamic studio lighting.

---

## 2. Directory Architecture & Asset Taxonomy

All production assets are strictly organized within the [`assets/`](file:///D:/AI%20Arts/AI/assets/) directory:

```
assets/
├── audio/                                    # Royalty-free procedural & synthesized audio
│   ├── music/
│   │   └── ambient_tech_theme.wav            # 65.0s ambient lo-fi synth chord progression (Dm9-Bb-F-C)
│   ├── sfx/
│   │   ├── chime_pop.wav                     # 0.9s synthetic chime pop (outro & callouts)
│   │   └── whoosh_transition.wav            # 0.8s frequency-sweep whoosh
│   └── generate_assets.py                    # Standalone CPU-friendly audio generator
│
├── backgrounds/                              # Native 1080x1920 (9:16) Master Background Plates
│   ├── pop_info/
│   │   ├── bg_warm_cream.png                 # Warm cream (#F5EFEB) with subtle vignette & floor shadow
│   │   └── bg_supermarket_aisle.png          # Stylized isometric retail aisle perspective
│   ├── data_story/
│   │   ├── bg_dark_slate_grid.png            # Deep slate (#0F141C) with coordinate grid lines
│   │   └── bg_editorial_dots.png             # Financial Times / Bloomberg dot matrix texture
│   ├── chalk_talk/
│   │   ├── bg_slate_blackboard.png           # Textured deep green slate with 40px oak frame & chalk dust
│   │   └── bg_deep_green_board.png           # Classic schoolhouse blackboard with felt eraser trails
│   └── creator/
│       ├── bg_studio_black.png               # Ultra-clean studio dark (#0B0B0D) with soft top spotlight
│       └── bg_neon_ambient.png               # Dual-tint kinetic yellow / cyber cyan ambient rim glow
│
├── branding/                                 # Official CrazzyDocs Channel Identity
│   └── crazzydocs/
│       ├── crazzydocs_icon_original.png      # High-res square master channel icon (1254x1254)
│       ├── crazzydocs_icon_circular.png      # Circular cropped transparent icon with gold/teal rim
│       ├── crazzydocs_banner_original.png    # High-res channel banner (2752x1536)
│       └── crazzydocs_outro_1080x1920.mp4    # Master 2.5s 1080x1920 30 FPS outro with audio chime
│
├── characters/                               # Reusable Character Systems
│   ├── pop_host/                             # "Dr. Doc" Signature Host System
│   │   ├── base/
│   │   │   └── head_neutral.png              # Master anatomical head (espresso hair, teal glasses)
│   │   ├── expressions/
│   │   │   ├── exp_neutral.png               # Calm, confident resting gaze
│   │   │   ├── exp_curious.png               # Asymmetric raised brow, inquisitive eye tilt
│   │   │   ├── exp_shocked.png               # Wide round pupils, dropped jaw
│   │   │   ├── exp_explaining.png            # Warm speaking mouth, direct gaze
│   │   │   └── exp_smiling.png               # Curved crescent eyes, wide toothy grin
│   │   └── poses/                            # Upper-body transparent PNGs (800x1200 scale)
│   │       ├── pose_idle_welcoming.png       # Welcoming open arms posture
│   │       ├── pose_thinking_chin.png        # Hand to chin, curious head tilt
│   │       ├── pose_presenting_left.png      # Left arm gesturing outward to graphic cards
│   │       ├── pose_presenting_right.png     # Right arm gesturing outward
│   │       ├── pose_pointing_up.png          # Index finger raised high ("Key takeaway")
│   │       └── pose_shocked_hands.png        # Hands raised to cheeks, startled pose
│   ├── chalk_figures/                        # Hand-drawn chalk lecturers & students
│   │   ├── chalk_teacher_pointing.png        # Pure chalk stick teacher pointing to diagram
│   │   └── chalk_student_thinking.png        # Chalk figure scratching head with question mark
│   └── silhouettes/                          # Monochromatic editorial data silhouettes
│       ├── human_scale_monochrome.png        # Minimalist slate silhouette for scale comparisons
│       └── consumer_avatar_stamp.png         # Circular user/consumer icon badge
│
├── props/                                    # Reusable Storytelling Props (Transparent PNGs)
│   ├── pop_info/
│   │   ├── milk_carton_mascot.png            # Cheerful milk carton with straw and smiling face
│   │   └── lightbulb_idea.png                # Popping vibrant yellow idea lightbulb
│   ├── data_story/
│   │   ├── institutional_badge_iea.png       # Formal IEA / official dataset institutional seal
│   │   └── trend_surge_arrow.png             # Glowing emerald green upward surge vector
│   ├── chalk_talk/
│   │   ├── chalk_stick_white.png             # 3D angled white chalk stick cursor
│   │   └── felt_eraser_dust.png              # Wooden felt eraser with chalk dust cloud
│   └── creator/
│       ├── device_iphone_mockup.png          # Sleek 3D smartphone frame with Dynamic Island
│       └── verified_badge.png                # Cyan verified checkmark badge
│
└── media_library/                            # Verified CC / Public Domain Real-World Media (Stage 2)
    ├── images/                               # Raw downloaded media from NASA, Wikimedia, Openverse
    ├── portrait/                             # 1080x1920 adapted portraits (smart focal & blurred fit)
    └── manifests/                            # AttributionManifest JSON files
```

---

## 3. Style-by-Style Visual Specifications

### 3.1 Pop Info
- **Character System:** "Dr. Doc" (The Quirky Researcher). Round teal horn-rimmed spectacles, swept dark espresso hair with an inquisitive crest tuft, deep teal educator jacket (`#2F6F73`), and coral collar accent (`#E85D4A`).
- **Color Palette:**
  - Background: Cream `#F5EFEB`, Floor `#E8E0D8`
  - Primary Accent: Deep Teal `#2F6F73`
  - Energy Accent: Vibrant Coral `#E85D4A`
  - Highlight: Mustard Gold `#ECA82C`
  - Ink & Text: Charcoal `#1D262B`
- **Typography:** Fredoka (Display / Headlines), DM Sans (Subtitles / Body).
- **Relatable Props:** Smiling milk carton mascot, lightbulb idea, grocery basket, question bubbles.

### 3.2 Data Story
- **Character System:** Strictly monochromatic silhouettes (`human_scale_monochrome.png`) and badge stamps (`consumer_avatar_stamp.png`). No cartoon character is ever forced into Data Story to protect institutional authority.
- **Color Palette:**
  - Background: Deep Slate Ink `#0F141C`
  - Positive Surge: Metric Emerald `#10B981`
  - Accent / Ticker: Electric Blue `#3B82F6`
  - Warning: Amber `#F59E0B`
  - Grid & Text: Slate Gray `#94A3B8` / Crisp White `#F8FAFC`
- **Typography:** Archivo (Headlines), IBM Plex Mono (Data points, percentages, axis ticks).
- **Visual Elements:** Institutional seals (IEA, Eurostat, IMF), trend surge arrows, coordinate grids, verified real-world photojournalism from `assets/media_library/portrait/`.

### 3.3 Chalk Talk
- **Character System:** Authentic hand-drawn chalk stick figures (`chalk_teacher_pointing.png`, `chalk_student_thinking.png`). Figures feature chalk stroke jitter, imperfect line thicknesses, and chalk particle dust. Glossy digital artwork is strictly prohibited.
- **Color Palette:**
  - Blackboard Slate: Deep Green Slate `#24392F`
  - Oak Wooden Frame: Warm Timber `#3A2312`
  - Chalk White: `#F8F9FA`
  - Chalk Yellow: `#FCD34D`
  - Chalk Sky Blue: `#7DD3FC`
- **Typography:** Cabin Sketch (Headlines), Kalam (Annotations & body labels).
- **Physical Tools:** White chalk stick cursor tracking drawing paths, wooden felt eraser creating chalk dust clouds.

### 3.4 Creator Motion Graphics
- **Character System:** Floating channel badges, verified creator stamps, and 3D device framing.
- **Color Palette:**
  - Studio Backdrop: Pitch Black `#0B0B0D`
  - Kinetic Accent: Kinetic Yellow `#FFE600`
  - Impact Accent: Vivid Red `#FF3366`
  - Cyber Cyan: `#00F0FF`
  - Body Text: Clean White `#FFFFFF`
- **Typography:** Anton (Slam headlines), Inter (Subtitles), Instrument Serif (Emphatic editorial quotes).
- **Visual Elements:** 3D tilted iPhone 16 mockup frame, verified checkmark badges, timeline scissors, highlighter swipe overlays.

---

## 4. Visual Previews & QA Reference Sheets

Visual preview sheets have been compiled in [`docs/previews/`](file:///D:/AI%20Arts/AI/docs/previews/):

1. **Character Concept Sheet**: [`docs/previews/character_concept_sheet.png`](file:///D:/AI%20Arts/AI/docs/previews/character_concept_sheet.png)  
   Evaluates 3 distinct design directions ("Dr. Doc", "Dr. Nova", "Prof. Barnaby") with selection rationale for Dr. Doc.
2. **Expression Matrix**: [`docs/previews/pop_host_expression_sheet.png`](file:///D:/AI%20Arts/AI/docs/previews/pop_host_expression_sheet.png)  
   Shows all 5 facial states with consistent facial identity and signature spectacles.
3. **Pose System**: [`docs/previews/pop_host_pose_sheet.png`](file:///D:/AI%20Arts/AI/docs/previews/pop_host_pose_sheet.png)  
   Shows 6 full upper-body postures aligned to native 1080-width scale with standardized anchor coordinates.
4. **Background Plates**: [`docs/previews/backgrounds_preview_sheet.png`](file:///D:/AI%20Arts/AI/docs/previews/backgrounds_preview_sheet.png)  
   Displays native 1080×1920 background plates across Pop Info, Data Story, Chalk Talk, and Creator Motion Graphics.
5. **Storytelling Props**: [`docs/previews/props_preview_sheet.png`](file:///D:/AI%20Arts/AI/docs/previews/props_preview_sheet.png)  
   Catalog of transparent PNG props for each style.
6. **CrazzyDocs Branded Outro**: [`docs/previews/crazzydocs_outro_preview.png`](file:///D:/AI%20Arts/AI/docs/previews/crazzydocs_outro_preview.png)  
   Peak keyframe preview of the official channel outro.

---

## 5. Dual-Engine Renderer Integration

The redesigned asset library integrates directly with both rendering pipelines:

1. **Python Canvas Engine (`photo_scene.py`, `split_screen.py`)**:
   - Fallback plates automatically resolve to `assets/backgrounds/pop_info/bg_warm_cream.png` and `assets/backgrounds/data_story/bg_dark_slate_grid.png`.
   - Split-screen compositions utilize style-specific dividers and verified portrait media.
2. **Headless Chromium Canvas Engine (Style Adapters)**:
   - Pop Info adapter renders Dr. Doc's signature horn-rimmed spectacles and animated breathing sway.
   - Data Story mounts verified CC media directly into institutional data containers.
   - Chalk Talk renders hand-drawn chalk strokes and physics-based felt eraser wipes.
   - Creator Motion Graphics projects iPhone frames onto 3D yaw planes.
3. **Universal FFmpeg Compositor (`compositor.py`)**:
   - `append_branded_outro()` stitches the master 2.5s CrazzyDocs outro to every completed Short without disrupting narration, background music ducking, or ASS subtitles.

---

## 6. Archival Invariant & Non-Destructive Storage

All legacy assets from initial development have been safely relocated to [`archive/legacy_assets/`](file:///D:/AI%20Arts/AI/archive/legacy_assets/) preserving their original directory trees:
- `assets/characters/host/*` -> `archive/legacy_assets/characters/host/*`
- `assets/backgrounds/bg_space.*` -> `archive/legacy_assets/backgrounds/bg_space.*`
- `assets/backgrounds/bg_tech_lab.*` -> `archive/legacy_assets/backgrounds/bg_tech_lab.*`

Zero assets were permanently deleted. The four immutable reference HTML implementations in `docs/reference_styles/*.html` remain strictly untouched.
