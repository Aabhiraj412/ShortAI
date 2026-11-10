# 2D Stick-Figure Storytelling & Motion Graphics Engine

**Renderer Engine:** `backend/app/services/stick_animation_service.py`  
**Canvas:** 1080 × 1920 (9:16 Vertical) @ 30 FPS  
**Target Hardware:** AMD Ryzen 7 3700U (CPU-only, zero GPU requirement)  
**Render Benchmark:** **35.0 FPS** (16s Short renders in 13.70 seconds)  

---

## 1. Visual Direction & Style

The visual presentation uses clean 2D stick-figure silhouettes paired with high-impact motion graphics and infographics:
- **Clean Silhouettes**: 16px smooth anti-aliased bone strokes with round caps and glowing under-stroke highlights.
- **Consistent Head Rig**: 46px radius head circle with expressive eye states (open dots, wide surprised whites, arched brow thinking) and subtle audio-synced mouth gestures.
- **Forward Kinematics Poses**:
  - `standing`: Relaxed breathing idle pose with subtle spine flex.
  - `thinking`: Right hand raised to chin, head tilted, thought particles.
  - `pointing`: Arm extended towards infographic data or headline banner.
  - `presenting`: Both arms open wide in a welcoming storytelling stance.
  - `falling`: Tumbling limb kinematics with velocity blur (for black hole gravity).
  - `surprised`: Both hands raised, wide eyes.

---

## 2. Motion Graphics Elements

1. **Cosmic Singularity**:
   - Accretion disk with multi-ring color gradient (orange to cyan).
   - Swirling orbital particle arcs revolving with angular velocity `angle = t * 1.5`.
   - Photon sphere and pitch-black event horizon core.
2. **Spacetime Curvature Grid**:
   - 3D perspective grid lines warping dynamically near the gravitational center.
3. **Kinetic Typography**:
   - High-contrast rounded headline pills with drop shadows and cyan/yellow accents.
   - Elastic pop-in scale easing on scene entrance.
4. **Floating Cosmic Particles**:
   - 30 independent stardust particles drifting across the vertical frame.

---

## 3. Preserved Legacy Assets

The original facial-component sprite assets (`assets/characters/host/`) and original compositor (`animation_service.py`) remain completely preserved on disk for backwards compatibility.
