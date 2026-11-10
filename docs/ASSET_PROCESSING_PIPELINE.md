# Asset Processing & Portrait Adaptation Pipeline
## 1080 × 1920 (9:16 Vertical) Image Processing, Cropping Modes, and Ken Burns Readiness

**Project Root:** `D:\AI Arts\AI\`  
**Date:** November 10, 2026  
**Module:** `backend/app/media/image_processor.py`  
**Output Resolution:** Strict $1080 \times 1920$ @ 30 FPS

---

## 1. Portrait Adaptation Challenge

Most authoritative photographs, astronomical observations, and scientific illustrations are captured in landscape ($16:9$, $4:3$, or $3:2$). Forcing horizontal imagery into vertical mobile video ($9:16$) without deliberate art direction results in:
- Unnatural stretching or distortion.
- Heavy letterboxing / black pillar bars that destroy perceived production quality.
- Severe cropping that cuts off primary astronomical subjects or diagram labels.

---

## 2. The Three Adaptation Modes

`ImageProcessor` provides three mathematically distinct adaptation strategies:

### Mode 1: Fit with Blurred Background (`FIT_BLURRED_BACKGROUND` — Default)
- **Concept:** Preserves the entire original image without any cropping.
- **Layer 1 (Background Plate):** The source image is scaled to completely cover $1080 \times 1920$, subjected to a $32\text{px}$ Gaussian blur (`ImageFilter.GaussianBlur(32)`), and darkened by $45\%$ to create a rich, ambient, color-matched backdrop.
- **Layer 2 (Foreground Card):** The uncropped original image is scaled to fit inside the visual safe stage ($w \le 980\text{px}$, $h \le 960\text{px}$), centered at $(540, 840)$, bordered with a crisp $2\text{px}$ translucent stroke (`rgba(255, 255, 255, 0.3)`), and composited with drop shadow.
- **Best For:** Complex diagrams, full astronomical vistas, wide scientific charts, multi-subject historical documents.

### Mode 2: Center Crop (`CENTER_CROP`)
- **Concept:** Full-bleed vertical immersion.
- **Algorithm:** Scales the source image by $\max(1080 / w_{\text{src}}, 1920 / h_{\text{src}})$ using high-quality Lanczos resampling, then symmetrically extracts the central $1080 \times 1920$ slice.
- **Best For:** Organic textures, nebulae, deep space starfields, macro closeups.

### Mode 3: Smart Focal Crop (`SMART_FOCAL_CROP`)
- **Concept:** Automated subject tracking.
- **Algorithm:** Downsamples the image to $120 \times 120$, applies edge detection (`ImageFilter.FIND_EDGES`), and calculates variance across vertical slices to compute the vertical center of interest ($y_{\text{focal}} \in [0.2, 0.8]$). The vertical crop window shifts towards the focal point, ensuring celestial bodies, horizons, or faces are never decapitated.
- **Best For:** Spacecraft imagery with off-center planets, planetary rings, historical portraiture.

---

## 3. Strict Quality Invariants

- **Prohibition of Non-Uniform Scaling:** Aspect ratios are strictly preserved; $x$-scale and $y$-scale are mathematically identical.
- **High-Quality Resampling:** Employs `Image.Resampling.LANCZOS` for downscaling and `BICUBIC` for ambient upscaling.
- **Optimized Storage:** Saves output as high-quality progressive JPEG ($Q=92$) in `assets/media_library/portrait/`.
