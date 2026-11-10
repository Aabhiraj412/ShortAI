# CrazzyDocs Character Design Guidelines

**Document Status:** Production Standard  
**Host Character:** "Dr. Doc" (The Inquisitive Researcher)  
**Applicable Styles:** Pop Info (Host Character), Data Story (Silhouettes), Chalk Talk (Chalk Figures), Creator (Device Framing)  
**Location:** [`assets/characters/`](file:///D:/AI%20Arts/AI/assets/characters/)  

---

## 1. Character Design Philosophy & Selection Rationale

Rather than generating disconnected characters for every scene—which causes noticeable identity drift across scenes and videos—the CrazzyDocs studio employs a **Locked Identity Character System**.

### Concept Direction Evaluation

Before finalizing the character system, three distinct design directions were evaluated (documented in [`docs/previews/character_concept_sheet.png`](file:///D:/AI%20Arts/AI/docs/previews/character_concept_sheet.png)):

```
+---------------------------------------------------------------------------------------------------------+
|                                    CHARACTER CONCEPT MATRIX                                             |
+--------------------------+--------------------------------+--------------------+------------------------+
| Direction                | Archetype                      | Visual Language    | Verdict & Status       |
+--------------------------+--------------------------------+--------------------+------------------------+
| 1. "Dr. Doc"             | Quirky Researcher & Scientist  | Round teal specs,  | SELECTED FOR POP INFO  |
|                          |                                | espresso hair tuft | (Direct brand fit)     |
| 2. "Dr. Nova"            | Modern Tech Documentarian      | Mustard hoodie,    | Alternative Runner-up  |
|                          |                                | geometric frames   | (Modern YouTube style) |
| 3. "Prof. Barnaby"       | Whimsical Classic Professor    | Tweed vest, bowtie,| Not Selected           |
|                          |                                | wild white hair    | (Too caricatured/dated)|
+--------------------------+--------------------------------+--------------------+------------------------+
```

### Why "Dr. Doc" Was Selected:
1. **Direct Brand Alignment**: Connects immediately with the channel identity **CrazzyDocs**—conveying scientific curiosity, enthusiasm, and credibility with a playful, eccentric edge.
2. **Warmth & Approachability**: Horn-rimmed spectacles and warm pastel clothing avoid the cold, sterile look of generic corporate explainers.
3. **High Expressiveness**: The oversized glasses and expressive eyebrow geometry make emotional reactions easily readable on small mobile screens.

---

## 2. "Dr. Doc" Locked Identity Specification

To guarantee zero character identity drift between scenes and videos, all assets must conform strictly to these locked parameters:

### 2.1 Color Palette

```
Skin Base:        #F8D7B8  (RGB: 248, 215, 184)
Skin Shadow:      #F0BEA0  (RGB: 240, 190, 160)
Skin Cheek Blush: #E85D4A  (RGBA: 232, 93, 74, 0.35)
Hair (Espresso):  #2A221E  (RGB: 42, 34, 30)
Glasses (Teal):   #2F6F73  (RGB: 47, 111, 115)
Jacket (Teal):    #2F6F73  (RGB: 47, 111, 115)
Scarf / Collar:   #E85D4A  (RGB: 232, 93, 74)
Inner Shirt:      #1A2238  (RGB: 26, 34, 56)
Eyes / Pupils:    #1D262B  (RGB: 29, 38, 43)
```

### 2.2 Standardized Anchor Points (800 × 1200 Native Asset Scale)

- **Head Center**: `(400, 480)`
- **Eye Level**: `y = 485`
- **Mouth Level**: `y = 540`
- **Neck Joint**: `(400, 600)`
- **Shoulder Line**: `y = 650` (Span: `x = 240` to `x = 560`)
- **Chest Center**: `(400, 720)`

When placed into a 1080 × 1920 vertical video frame:
- **Default Frame Position**: Center `x = 540`, baseline `y = 1420` (upper-body framing).
- **Head Level in Short**: `y ≈ 760` (perfect upper-third composition above subtitle banner).

---

## 3. Facial Expression Matrix

Located in [`assets/characters/pop_host/expressions/`](file:///D:/AI%20Arts/AI/assets/characters/pop_host/expressions/) and previewed in [`docs/previews/pop_host_expression_sheet.png`](file:///D:/AI%20Arts/AI/docs/previews/pop_host_expression_sheet.png):

1. **`exp_neutral.png`**:
   - Eyes: Calm resting circular pupils with top-right specular highlight.
   - Brows: Flat resting arcs above spectacles.
   - Mouth: Subtle friendly upturned line.
   - Usage: General exposition, context delivery.
2. **`exp_curious.png`**:
   - Eyes: Looking upward-left toward information badges.
   - Brows: Left brow raised high in an exaggerated inquisitive arch; right brow flat.
   - Mouth: Small round 'o' shape.
   - Usage: Hook, paradoxes, questions, "Did you know?".
3. **`exp_shocked.png`**:
   - Eyes: Wide circular pupils, high eyebrow arches.
   - Mouth: Dropped open jaw (elongated rounded rectangle).
   - Usage: Sudden twists, counter-intuitive facts, spaghettification, cost plunges.
4. **`exp_explaining.png`**:
   - Eyes: Direct forward gaze connecting with the viewer.
   - Brows: Confident, angled slightly inward.
   - Mouth: Open half-moon talking mouth showing upper teeth line.
   - Usage: Scientific mechanisms, data breakdowns, logical explanations.
5. **`exp_smiling.png`**:
   - Eyes: Happy curved crescents (smiling eyes).
   - Brows: Relaxed, raised happy arcs.
   - Mouth: Wide open toothy smile.
   - Usage: Closing payoffs, CTA ("Subscribe to CrazzyDocs"), satisfying conclusions.

---

## 4. Reusable Pose Library

Located in [`assets/characters/pop_host/poses/`](file:///D:/AI%20Arts/AI/assets/characters/pop_host/poses/) and previewed in [`docs/previews/pop_host_pose_sheet.png`](file:///D:/AI%20Arts/AI/docs/previews/pop_host_pose_sheet.png):

| Pose ID | Stance | Arm Actions | Narrative Purpose |
| :--- | :--- | :--- | :--- |
| **`pose_idle_welcoming`** | Relaxed | Arms naturally at sides, welcoming torso | Scene entry, context setting, neutral transition |
| **`pose_thinking_chin`** | Inquisitive | Right hand resting beneath chin, head tilted | Presenting paradoxes, questions, thought bubbles |
| **`pose_presenting_left`** | Active | Left arm extended toward upper-left graphic card | Directing viewer attention to left infographic cards |
| **`pose_presenting_right`**| Active | Right arm extended toward upper-right graphic card | Directing viewer attention to right charts / props |
| **`pose_pointing_up`** | Emphatic | Right hand raised with index finger pointing high | "Key takeaway", vital rule, final call-to-action |
| **`pose_shocked_hands`** | Reactive | Both hands raised to cheeks, wide stance | Counter-intuitive revelations, extreme physics |

> [!TIP]
> **Seamless Swapping**: Because all poses share the identical anchor origin `(400, 480)`, scenes can hot-swap poses at transition cut points without any character jumping or visual discontinuity.

---

## 5. Style-Specific Visual Guidelines (No Forced Characters)

Crucially, **the cartoon character is not forced into all four styles**:

### Pop Info (Full Vector Host)
- Uses "Dr. Doc" multi-pose system with subtle procedural breathing sway ($2.2\text{ Hz}$) and eye blinking (every $3.2\text{ s}$).
- Complemented by friendly props (milk carton mascot, lightbulb idea, thought bubbles).

### Data Story (Monochromatic Silhouettes)
- Uses [`human_scale_monochrome.png`](file:///D:/AI%20Arts/AI/assets/characters/silhouettes/human_scale_monochrome.png) and [`consumer_avatar_stamp.png`](file:///D:/AI%20Arts/AI/assets/characters/silhouettes/consumer_avatar_stamp.png).
- Cartoon figures are forbidden in Data Story to maintain strict institutional credibility.

### Chalk Talk (Authentic Chalk Figures)
- Uses [`chalk_teacher_pointing.png`](file:///D:/AI%20Arts/AI/assets/characters/chalk_figures/chalk_teacher_pointing.png) and [`chalk_student_thinking.png`](file:///D:/AI%20Arts/AI/assets/characters/chalk_figures/chalk_student_thinking.png).
- Rendered with chalk jitter, rough line boundaries, and chalk particle dust against physical blackboard slate.

### Creator Motion Graphics (Device Framing & Badges)
- Uses [`device_iphone_mockup.png`](file:///D:/AI%20Arts/AI/assets/props/creator/device_iphone_mockup.png) and [`verified_badge.png`](file:///D:/AI%20Arts/AI/assets/props/creator/verified_badge.png).
- Human presence is conveyed through UI profile badges or real-world verified photo cutouts.
