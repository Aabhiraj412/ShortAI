# Black Hole Production Stick Animation Root Cause Analysis

**Investigation Target:** Root-cause forensics for unwanted stick/character animation on astrophysical & black-hole topics.  
**Report Date:** 2026-11-10 23:55:00 UTC+05:30  
**Status:** **ROOT CAUSE IDENTIFIED & CORRECTIVE ARCHITECTURE DEFINED**  

---

## 1. Executive Summary

During prior automated testing, a production based on the concept *"Could You Survive Falling Into a Black Hole?"* generated stick-figure animations and cartoon silhouettes instead of authentic astronomical imagery.

A complete forensic trace through the codebase identified that this failure was **not an asset acquisition download glitch**, but a **hardcoded legacy architectural defect**:
1. Legacy worker scripts (`start_daily_pipeline.ps1`, `run_worker.ps1`) executed `backend/app/workers/job_manager.py`.
2. `job_manager.py` had `render_stick_animation` hardcoded in Stage 5 as its sole renderer.
3. `backend/app/services/stick_animation_service.py` contained a dedicated `MotionGraphicsDirector.draw_singularity()` method explicitly built to render a black hole event horizon surrounded by a stick-figure rig.
4. `backend/app/agents/visual_director.py` contained a hardcoded keyword hook:
   ```python
   "COULD YOU SURVIVE?" if "black hole" in title.lower() or "survive" in title.lower() else title.upper()[:24]
   ```
5. `ContentIntegrityGate` lacked a check for prohibited renderers, allowing stick animation renders to pass technical QC.

---

## 2. End-to-End Decision Trace

```
TOPIC: "Could You Survive Falling Into a Black Hole?"
  │
  ▼
CREATIVE DIRECTOR (backend/app/agents/visual_director.py)
  ├─ Matches keyword "black hole"
  ├─ Hardcodes headline: "COULD YOU SURVIVE?"
  └─ Maps scenes to stick figure poses ("think", "present", "falling")
  │
  ▼
RENDERER SELECTION (backend/app/workers/job_manager.py)
  ├─ Line 24: from app.services.stick_animation_service import render_stick_animation
  ├─ Line 234: Calls render_stick_animation(...)
  └─ Bypasses Phase 5 Master Orchestrator & documentary renderers
  │
  ▼
RENDER EXECUTION (backend/app/services/stick_animation_service.py)
  ├─ Invokes MotionGraphicsDirector.draw_singularity()
  ├─ Draws 2D StickFigureRig around the accretion disk
  └─ Composites 1080x1920 video with cartoon character
  │
  ▼
QUALITY CONTROL GATE (backend/app/content/content_integrity_gate.py)
  ├─ Checks Checks A–J (aspect ratio, duration, audio, outro)
  ├─ Missing Check K (Prohibited Animation Gate)
  └─ PASSES QC DESPITE CARTOON CHARACTERS!
```

---

## 3. Root Cause Breakdown

| Pipeline Layer | Component | Defect Found |
|---|---|---|
| **Pipeline Runner** | `job_manager.py` | Line 234 directly invokes `render_stick_animation` instead of delegating to dynamic renderers or `CrazzyDocsMasterOrchestrator`. |
| **Animation Service** | `stick_animation_service.py` | Contains `StickFigureRig` and `draw_singularity()` designed specifically for space/black-hole topics. |
| **Storyboard Director** | `visual_director.py` | Contains hardcoded title checks mapping "black hole" to survivor questions and stick character poses. |
| **Integrity Gate** | `content_integrity_gate.py` | Zero validation of renderer identity or prohibited visual motifs. |

---

## 4. Corrective Actions Required

1. **Deprecate Legacy Stick Animation in Workers:**
   - Update `job_manager.py` to route production through `CrazzyDocsMasterOrchestrator` using authentic documentary renderers (`CleanSlateRenderer` / `VesnaFastRenderer`).
2. **Implement Prohibited Animation Gate (`CHECK_K`):**
   - In `content_integrity_gate.py`, add `CHECK_K_PROHIBITED_ANIMATION_GATE`:
     - Inspects production manifest and artifacts.
     - Rejects any output created by `render_stick_animation`, `StickFigureRig`, or character sprite directories.
     - Immediately fails with `BLOCKED_QC` if prohibited character animation is detected.
3. **Establish Black Hole Creative Standard:**
   - Visual beats for astrophysics and black-hole stories must source authentic public-domain telescope imagery (NASA Hubble/James Webb, ESO Event Horizon Telescope) or scientifically accurate accretion-disk visualizations.
   - If authentic imagery is unavailable, fall back to diagrammatic motion graphics, typography, and spacetime curvature grids—**never cartoon characters**.
4. **Regression Tests:**
   - Add automated test verifying that black hole stories are rejected if rendered with stick figures, and accepted when rendered with documentary or clean-slate scientific motion graphics.
