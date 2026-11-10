# Script Quality Framework & Production Pacing Standard
## 5-Stage Storytelling Architecture, Word Count Calibration, and Quality Assurance

**Project Root:** `D:\AI Arts\AI\`  
**Date:** November 10, 2026  
**Module:** `backend/app/content/script_generator.py`  
**Standard:** 1080 × 1920 @ 30 FPS, Production Duration Standard ($50.0 - 60.0\text{s}$, $120 - 135$ words)

---

## 1. Pacing & Duration Mathematics

YouTube Shorts audience retention drops precipitously when narration is rushed, dragged out, or artificially padded. The studio enforces strict mathematical pacing based on human conversational speech cadence:

| Pacing Metric | Value | Architectural Justification |
| :--- | :---: | :--- |
| **Conversational Speech Cadence** | **$2.2 - 2.4$ words / second** | Natural human speech rate for educational narrative audio ($132 - 144\text{ wpm}$) |
| **Target Runtime** | **$55.0$ Seconds** | Algorithmic retention sweet spot for YouTube Shorts |
| **Allowed Runtime Window** | **$50.0 - 60.0$ Seconds** | Hard platform bounds ($<50\text{s}$ rejected for brevity; $>60\text{s}$ classified as long-form) |
| **Optimal Script Word Count** | **$126$ Words** | $55.0\text{s} \times 2.3\text{ wps} = 126.5\text{ words}$ |
| **Strict Script Word Range** | **$115 - 140$ Words** | Ensures duration remains within $50.0 - 60.0\text{s}$ under all voice model variations |

---

## 2. The 5-Stage Storytelling Progression

Every script is partitioned into five distinct narrative beats with explicit psychological objectives:

```
0s ─────── 3s ───────────────── 12s ──────────────────────────────────────── 40s ───────────────────── 51s ────── 55s
│   HOOK   │      CONTEXT        │               MAIN EXPLANATION             │        PAYOFF         │ CLOSING  │
│  (0-3s)  │      (3-12s)        │                   (12-40s)                 │       (40-51s)        │ (51-55s) │
├──────────┼─────────────────────┼────────────────────────────────────────────┼───────────────────────┼──────────┤
│~8-10 wds │~20 words            │~60-65 words across 3 distinct beats        │~20-25 words           │~10-15 wds│
│Curiosity │Frame stakes without │Progressive mechanical breakdown            │Surprising revelation, │Punchy CTA│
│gap punch │introductory fluff   │(curves, formulas, biological mechanisms)   │climax, or paradox key │& loop out│
└──────────┴─────────────────────┴────────────────────────────────────────────┴───────────────────────┴──────────┘
```

### Stage 1: The Hook ($0.0 - 3.0\text{s}$, $8 - 12$ words)
- **Objective:** Stop the mobile scroll within $1.5$ seconds.
- **Rules:** Zero introduction fluff. Never say *"Hey guys"*, *"Welcome back"*, *"In this video"*, or *"Did you know"*. Open with an immediate high-stakes paradox or provocative fact.
- **Example:** *"The Moon is constantly falling directly toward Earth right now."*

### Stage 2: Context ($3.0 - 12.0\text{s}$, $18 - 25$ words)
- **Objective:** Establish the premise, tension, and why the viewer should care.
- **Rules:** State the apparent contradiction or historical baseline before the breakthrough.
- **Example:** *"Yet it never crashes into our ground, pulled relentlessly by planetary gravity every single second across 384,000 kilometers of silent empty space."*

### Stage 3: Main Explanation ($12.0 - 40.0\text{s}$, $55 - 65$ words)
- **Objective:** Deliver the substantive educational explanation across 3 evolving visual beats.
- **Rules:** Avoid repetitive phrases or static holds. Break into progressive mechanical steps:
  - *Beat A:* The Initial Mechanism / Friction.
  - *Beat B:* The Governing Formula, Curve Inflection, or Physical Law.
  - *Beat C:* The Comparative Proof or Real-World Data.
- **Example:** *"Because the Moon also flies forward through space at over one kilometer per second: thirty-six hundred kilometers per hour. As gravity pulls it downward, Earth's spherical surface curves away beneath it at the exact same rate. Sir Isaac Newton proved this orbital balance with his velocity formula: gravitational acceleration perfectly balances forward velocity in a frictionless vacuum."*

### Stage 4: The Payoff ($40.0 - 51.0\text{s}$, $20 - 25$ words)
- **Objective:** Deliver the climactic realization that resolves the opening hook.
- **Rules:** The narrative climax must directly answer the curiosity gap opened in Stage 1. This is the moment viewers screenshot, share, or re-watch.
- **Example:** *"The Moon is trapped in perpetual free fall: forever plummeting toward us, yet forever missing our planet's horizon across billions of years."*

### Stage 5: Closing ($51.0 - 55.0\text{s}$, $10 - 15$ words)
- **Objective:** Memorable takeaway punch and natural 5–8 word call to action.
- **Rules:** Fast, natural conclusion. Never spend more than 4 seconds on closing.
- **Example:** *"Orbit is simply falling without ever hitting the ground. Subscribe for more cosmic secrets!"*

---

## 3. Automated Validation & Rejection Engine

The `ScriptGenerator.validate_script()` method subjects every draft to four automated validation gates prior to staging:

```python
def validate_script(self, script: ProductionScript) -> Tuple[bool, List[str]]:
    # 1. Word Count Range Check: 115 <= N <= 140
    # 2. Duration Window Check: 49.5s <= t <= 60.5s
    # 3. Intro Fluff Detection: Rejects banned greeting phrases
    # 4. Mandatory Stage Presence: All 5 narrative stages non-empty
```

### Rejection Triggers:
1. **Under-length Scripts ($<115$ words):** Automatically rejected because runtime would drop below the 50.0s production floor.
2. **Over-length Scripts ($>140$ words):** Automatically rejected because runtime would exceed the 60.0s platform ceiling.
3. **Introductory Fluff:** Any script containing phrases from `FORBIDDEN_INTRO_PHRASES` (*"Hey guys"*, *"Welcome back"*, *"In this video"*) is blocked immediately.
4. **Unanchored Claims:** Any factual statement not linked to a verified source in the `ResearchDossier` fails validation.

---

## 4. Intelligent Visual Style Affinity

The script generator evaluates topic characteristics to route each script to its optimal visual style:

| Visual Style | Target Topics & Tone | Visual Primitives |
| :--- | :--- | :--- |
| **Data Story** | Global economics, percentage drops, energy costs, demographics | Monotone cubic splines, glowing tracer beads, plunging counters, comparative cost bars |
| **Chalk Talk** | Orbital mechanics, gravity vectors, physics equations, planetary systems | Hand-drawn chalk strokes, active chalk tip synchronization, physical felt eraser wiping |
| **Pop Info** | Consumer psychology, infantile amnesia, retail staging, cognitive curiosities | Warm cartoon characters, multi-stage emotional acting, thought bubbles, speed streaks |
| **Creator Motion Graphics** | Computer science, hash tables, algorithmic lookups, viral creator workflows | 3-second countdown dials, NLE timeline scissor cuts, 3D yaw retention analytics |
