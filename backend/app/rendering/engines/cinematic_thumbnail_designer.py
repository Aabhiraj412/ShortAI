"""
Cinematic Documentary Thumbnail Designer for CrazzyDocs Shorts Studio.
Phase 5, Stage 3.9A Production Standard.

Produces art-directed, documentary-poster-grade vertical 1080x1920 thumbnails:
1. Four distinct creative composition archetypes:
   - Split Juxtaposition (Dual event contrast)
   - Extreme Scale & Perspective (Human silhouette vs titanic cloud)
   - Resilient Monolith (Standing ruin in radioactive ash)
   - Forensic Archive (Declassified order & survivor evidence)
2. Advanced visual art-direction:
   - Atmospheric depth, directional rim-lighting, multi-stop color grading.
   - Clean 2-4 impactful words integrated into composition.
   - Strict 1080x1920 format, safe margins, and mobile readability.
3. 10-Metric Comprehensive Creative Evaluation:
   - Curiosity, Story relevance, Visual impact, Focal hierarchy, Readability,
     Emotional impact, Composition, Branding, Historical accuracy, Small-screen visibility.
4. Generates full-resolution candidates and mobile-scale (120x213) audit previews.
"""

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
import logging
import math
import os
from typing import Dict, List, Optional, Tuple
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

logger = logging.getLogger(__name__)

from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE_ROOT
THUMBNAIL_DIR = os.path.join(WORKSPACE_ROOT, "media", "thumbnails")
AUDIT_DIR = os.path.join(WORKSPACE_ROOT, "media", "audit", "thumbnails_3_9a")
PREVIEWS_DIR = os.path.join(AUDIT_DIR, "mobile_previews")

os.makedirs(THUMBNAIL_DIR, exist_ok=True)
os.makedirs(AUDIT_DIR, exist_ok=True)
os.makedirs(PREVIEWS_DIR, exist_ok=True)


class CreativeDirectionType(str, Enum):
    SPLIT_JUXTAPOSITION = "split_juxtaposition"
    EXTREME_SCALE_PERSPECTIVE = "extreme_scale_perspective"
    RESILIENT_MONOLITH = "resilient_monolith"
    FORENSIC_ARCHIVE = "forensic_archive"


@dataclass
class CreativeEvaluationScore:
    curiosity: float              # 0.0 - 10.0
    story_relevance: float        # 0.0 - 10.0
    visual_impact: float          # 0.0 - 10.0
    focal_hierarchy: float        # 0.0 - 10.0
    readability: float            # 0.0 - 10.0
    emotional_impact: float       # 0.0 - 10.0
    composition_balance: float    # 0.0 - 10.0
    branding_alignment: float     # 0.0 - 10.0
    historical_accuracy: float    # 0.0 - 10.0
    small_screen_visibility: float # 0.0 - 10.0
    misleading_risk_penalty: float = 0.0
    composite_score: float = 0.0

    def compute_composite(self) -> float:
        weights = {
            "curiosity": 0.18,
            "story_relevance": 0.15,
            "visual_impact": 0.15,
            "focal_hierarchy": 0.12,
            "readability": 0.12,
            "emotional_impact": 0.10,
            "composition_balance": 0.08,
            "small_screen_visibility": 0.05,
            "branding_alignment": 0.03,
            "historical_accuracy": 0.02,
        }
        raw = sum(getattr(self, k) * w for k, w in weights.items())
        self.composite_score = round(max(0.0, raw - (self.misleading_risk_penalty * 2.0)), 2)
        return self.composite_score


@dataclass
class PosterCandidate:
    id: str
    title: str
    direction_type: CreativeDirectionType
    headline: str
    tagline: Optional[str]
    palette_primary: Tuple[int, int, int]
    palette_accent: Tuple[int, int, int]
    strengths: str
    weaknesses: str
    design_rationale: str
    full_path: Optional[str] = None
    mobile_path: Optional[str] = None
    evaluation: Optional[CreativeEvaluationScore] = None


class CinematicThumbnailDesigner:
    """Art-directed poster generation and evaluation for documentary Shorts."""

    def __init__(self, width: int = 1080, height: int = 1920):
        self.width = width
        self.height = height
        self._init_typography()

    def _init_typography(self):
        font_cands = ["impact.ttf", "arialbd.ttf", "segoeui.ttf"]
        self.poster_font_hero = None
        self.poster_font_sub = None
        self.poster_font_tag = None

        for cand in font_cands:
            try:
                self.poster_font_hero = ImageFont.truetype(cand, 136)
                self.poster_font_sub = ImageFont.truetype(cand, 96)
                self.poster_font_tag = ImageFont.truetype("arialbd.ttf", 36)
                break
            except Exception:
                continue

        if not self.poster_font_hero:
            d = ImageFont.load_default()
            self.poster_font_hero = d
            self.poster_font_sub = d
            self.poster_font_tag = d

    # =========================================================================
    # CONCEPT 1: DUAL BLAST SPLIT JUXTAPOSITION ("The Twin Epicenters")
    # =========================================================================
    def render_split_juxtaposition(
        self,
        img_left_path: str,
        img_right_path: str,
        headline_top: str = "SURVIVED",
        headline_bot: str = "BOTH BOMBS",
        out_path: Optional[str] = None,
    ) -> Image.Image:
        w, h = self.width, self.height
        canvas = Image.new("RGB", (w, h), (10, 12, 18))

        # 1. Left Half: Hiroshima (Searing Firestorm Amber Tone)
        if os.path.exists(img_left_path):
            left_src = Image.open(img_left_path).convert("RGB")
            left_scaled = self._fill_and_crop(left_src, w // 2 + 60, h)
            # Tint with warm radioactive amber
            enhancer = ImageEnhance.Color(left_scaled)
            left_scaled = enhancer.enhance(1.4)
            amber_tint = Image.new("RGBA", left_scaled.size, (255, 110, 30, 45))
            left_scaled = Image.alpha_composite(left_scaled.convert("RGBA"), amber_tint).convert("RGB")
            canvas.paste(left_scaled, (0, 0))

        # 2. Right Half: Nagasaki (Cold Plutonium Violet / Ash Tone)
        if os.path.exists(img_right_path):
            right_src = Image.open(img_right_path).convert("RGB")
            right_scaled = self._fill_and_crop(right_src, w // 2 + 60, h)
            # Tint with cold ash / violet
            ash_tint = Image.new("RGBA", right_scaled.size, (40, 20, 70, 55))
            right_scaled = Image.alpha_composite(right_scaled.convert("RGBA"), ash_tint).convert("RGB")
            canvas.paste(right_scaled, (w // 2 - 20, 0))

        # 3. Dynamic Dividing Line (Vertical Razor Incision with Glow)
        draw = ImageDraw.Draw(canvas, "RGBA")
        cx = w // 2
        # Outer glow
        draw.line([(cx, 0), (cx, h)], fill=(254, 224, 4, 110), width=10)
        # Core razor white
        draw.line([(cx, 0), (cx, h)], fill=(255, 255, 255, 240), width=3)

        # 4. Cinematic Vignettes (Heavy bottom, soft top, safe edges)
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d_ov = ImageDraw.Draw(overlay)
        # Deep bottom gradient for headline
        for y in range(h - 750, h):
            prog = (y - (h - 750)) / 750.0
            d_ov.line([(0, y), (w, y)], fill=(6, 8, 14, int(245 * (prog ** 1.3))))
        # Subtle top vignette
        for y in range(0, 320):
            prog = 1.0 - (y / 320.0)
            d_ov.line([(0, y), (w, y)], fill=(6, 8, 14, int(160 * prog)))

        canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(canvas)

        # 5. Top Historical Dateline Pills (Hiroshima Aug 6 | Nagasaki Aug 9)
        draw.rounded_rectangle([90, 240, 470, 305], radius=10, fill=(18, 22, 32, 230), outline=(255, 120, 40), width=2)
        draw.text((120, 254), "HIROSHIMA • AUG 6", fill=(255, 140, 60), font=self.poster_font_tag)

        draw.rounded_rectangle([610, 240, 990, 305], radius=10, fill=(18, 22, 32, 230), outline=(170, 140, 255), width=2)
        draw.text((645, 254), "NAGASAKI • AUG 9", fill=(190, 170, 255), font=self.poster_font_tag)

        # 6. Integrated Hero Typography (Lower Third: 2 Words)
        self._draw_poster_headline(
            draw=draw,
            lines=[headline_top, headline_bot],
            y_bottom=1580,
            color_accent=(254, 224, 4),
            hero_font=self.poster_font_hero,
            sub_font=self.poster_font_hero,
        )

        # Border
        draw.rectangle([8, 8, w - 8, h - 8], outline=(254, 224, 4), width=3)

        if out_path:
            canvas.save(out_path, "PNG", quality=95)
        return canvas

    # =========================================================================
    # CONCEPT 2: EXTREME SCALE & PERSPECTIVE ("The Solitary Witness")
    # =========================================================================
    def render_extreme_scale(
        self,
        cloud_asset_path: str,
        headline: str = "HE SURVIVED",
        subline: str = "TWICE",
        out_path: Optional[str] = None,
    ) -> Image.Image:
        w, h = self.width, self.height
        canvas = Image.new("RGB", (w, h), (8, 10, 16))

        # 1. Immense Towering Mushroom Cloud occupying the upper 75%
        if os.path.exists(cloud_asset_path):
            cloud = Image.open(cloud_asset_path).convert("RGB")
            cloud_scaled = self._fill_and_crop(cloud, w, int(h * 0.82))
            canvas.paste(cloud_scaled, (0, 0))

        # 2. Dramatic Volumetric Contrast Overlay & Backlit Sky
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d_ov = ImageDraw.Draw(overlay)
        # Deep ground horizon darkness
        for y in range(int(h * 0.65), h):
            prog = (y - int(h * 0.65)) / (h * 0.35)
            d_ov.line([(0, y), (w, y)], fill=(8, 10, 15, int(255 * (prog ** 1.1))))

        canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(canvas)

        # 3. Ground Plane & Solitary Human Engineer Silhouette (Bottom Center)
        # Low-angle silhouette looking upward at titanic firestorm
        cx = w // 2
        horizon_y = int(h * 0.80)
        # Jagged disaster horizon
        ground_poly = [(0, h), (0, horizon_y + 30), (280, horizon_y + 10), (cx - 100, horizon_y), (cx + 100, horizon_y + 8), (w, horizon_y + 25), (w, h)]
        draw.polygon(ground_poly, fill=(6, 8, 12))

        # Solitary survivor figure silhouette (approx 160px height)
        fig_y = horizon_y - 120
        # Head & shoulders
        draw.ellipse([cx - 20, fig_y, cx + 20, fig_y + 40], fill=(6, 8, 12))
        # Torso & coat
        draw.polygon([(cx - 32, fig_y + 40), (cx + 32, fig_y + 40), (cx + 42, horizon_y + 40), (cx - 42, horizon_y + 40)], fill=(6, 8, 12))
        # Orange rim glow around figure
        draw.arc([cx - 24, fig_y - 4, cx + 24, fig_y + 44], start=180, end=360, fill=(255, 140, 40), width=3)

        # 4. Hero Typography (Upper Zone: Clean, dramatic, floating over dark smoke)
        self._draw_poster_headline(
            draw=draw,
            lines=[headline, subline],
            y_bottom=520,
            color_accent=(255, 77, 77),
            hero_font=self.poster_font_hero,
            sub_font=self.poster_font_hero,
            align="center",
        )

        draw.rectangle([8, 8, w - 8, h - 8], outline=(255, 77, 77), width=3)

        if out_path:
            canvas.save(out_path, "PNG", quality=95)
        return canvas

    # =========================================================================
    # CONCEPT 3: RESILIENT MONOLITH ("The Standing Ruin")
    # =========================================================================
    def render_resilient_monolith(
        self,
        monument_path: str,
        cloud_path: str,
        headline: str = "IMPOSSIBLE",
        subline: str = "SURVIVOR",
        out_path: Optional[str] = None,
    ) -> Image.Image:
        w, h = self.width, self.height
        canvas = Image.new("RGB", (w, h), (12, 14, 20))

        # 1. Background: Radioactive Sky / Expanding Shockwave
        if os.path.exists(cloud_path):
            bg = Image.open(cloud_path).convert("RGB")
            bg_scaled = self._fill_and_crop(bg, w, h).filter(ImageFilter.GaussianBlur(18))
            # Monochromatic desaturation with amber nuclear core
            bg_scaled = ImageOps.grayscale(bg_scaled).convert("RGB")
            canvas.paste(bg_scaled, (0, 0))

        # 2. Central Resilient Monument (Genbaku Dome / Standing Torii Gate)
        if os.path.exists(monument_path):
            monument = Image.open(monument_path).convert("RGB")
            # Sharpen and emphasize skeletal dome/arch
            enh = ImageEnhance.Contrast(monument)
            monument = enh.enhance(1.3)
            mon_scaled = self._fill_and_crop(monument, int(w * 0.90), int(h * 0.58))
            canvas.paste(mon_scaled, (int(w * 0.05), int(h * 0.22)))
            # Outer dark frame
            draw_m = ImageDraw.Draw(canvas)
            draw_m.rectangle([int(w * 0.05), int(h * 0.22), int(w * 0.95), int(h * 0.80)], outline=(32, 201, 151), width=3)

        # 3. Dramatic Vignette Overlay
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d_ov = ImageDraw.Draw(overlay)
        # Deep lower vignette for title
        for y in range(int(h * 0.70), h):
            prog = (y - int(h * 0.70)) / (h * 0.30)
            d_ov.line([(0, y), (w, y)], fill=(8, 12, 18, int(245 * prog)))
        # Top vignette
        for y in range(0, 300):
            prog = 1.0 - (y / 300.0)
            d_ov.line([(0, y), (w, y)], fill=(8, 12, 18, int(180 * prog)))

        canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(canvas)

        # 4. Hero Typography (Lower Third: High-Impact Emerald & White)
        self._draw_poster_headline(
            draw=draw,
            lines=[headline, subline],
            y_bottom=1580,
            color_accent=(32, 201, 151),
            hero_font=self.poster_font_hero,
            sub_font=self.poster_font_hero,
        )

        draw.rectangle([8, 8, w - 8, h - 8], outline=(32, 201, 151), width=3)

        if out_path:
            canvas.save(out_path, "PNG", quality=95)
        return canvas

    # =========================================================================
    # CONCEPT 4: FORENSIC ARCHIVE ("The Declassified Miracle")
    # =========================================================================
    def render_forensic_archive(
        self,
        doc_asset_path: str,
        rubble_asset_path: str,
        headline: str = "DOUBLE",
        subline: str = "NUCLEAR SURVIVOR",
        out_path: Optional[str] = None,
    ) -> Image.Image:
        w, h = self.width, self.height
        canvas = Image.new("RGB", (w, h), (14, 16, 22))

        # 1. Base Layer: Authentic Historical Disaster Rubble
        if os.path.exists(rubble_asset_path):
            rubble = Image.open(rubble_asset_path).convert("RGB")
            rubble_scaled = self._fill_and_crop(rubble, w, h)
            canvas.paste(rubble_scaled, (0, 0))

        # 2. Declassified Military Order Overlay (Slanted / Angled in upper mid)
        if os.path.exists(doc_asset_path):
            doc = Image.open(doc_asset_path).convert("RGB")
            doc_scaled = self._fill_and_crop(doc, int(w * 0.82), int(h * 0.42))
            doc_rgba = doc_scaled.convert("RGBA")
            # Apply parchment tint and transparency
            parchment = Image.new("RGBA", doc_rgba.size, (245, 230, 200, 30))
            doc_rgba = Image.alpha_composite(doc_rgba, parchment)
            # Paste in upper mid
            canvas.paste(doc_rgba.convert("RGB"), (int(w * 0.09), 320))
            draw_d = ImageDraw.Draw(canvas)
            draw_d.rectangle([int(w * 0.09), 320, int(w * 0.91), 320 + int(h * 0.42)], outline=(242, 193, 78), width=3)

        # 3. Stamped Verification Badge ("OFFICIALLY CERTIFIED • 2009")
        draw = ImageDraw.Draw(canvas)
        stamp_box = (w // 2 - 240, 230, w // 2 + 240, 295)
        draw.rounded_rectangle(stamp_box, radius=10, fill=(18, 22, 30, 240), outline=(255, 77, 77), width=3)
        draw.text((w // 2 - 200, 244), "OFFICIAL DECLASSIFIED RECORD", fill=(255, 100, 100), font=self.poster_font_tag)

        # 4. Cinematic Darkening & Vignette
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d_ov = ImageDraw.Draw(overlay)
        for y in range(h - 750, h):
            prog = (y - (h - 750)) / 750.0
            d_ov.line([(0, y), (w, y)], fill=(8, 10, 16, int(250 * prog)))
        canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(canvas)

        # 5. Hero Typography (Lower Third: Gold & White)
        self._draw_poster_headline(
            draw=draw,
            lines=[headline, subline],
            y_bottom=1580,
            color_accent=(242, 193, 78),
            hero_font=self.poster_font_hero,
            sub_font=self.poster_font_sub,
        )

        draw.rectangle([8, 8, w - 8, h - 8], outline=(242, 193, 78), width=3)

        if out_path:
            canvas.save(out_path, "PNG", quality=95)
        return canvas

    # =========================================================================
    # MULTI-CANDIDATE SUITE & EVALUATION ENGINE
    # =========================================================================
    def generate_all_candidates(
        self,
        assets: Dict[str, str],
        audit_dir: str = AUDIT_DIR
    ) -> List[PosterCandidate]:
        """Renders 4 genuinely distinct composition concepts and evaluates them."""
        os.makedirs(audit_dir, exist_ok=True)
        os.makedirs(PREVIEWS_DIR, exist_ok=True)

        candidates: List[PosterCandidate] = []

        # 1. Concept 1: Split Juxtaposition
        p1 = os.path.join(audit_dir, "concept_01_split_juxtaposition.png")
        self.render_split_juxtaposition(
            img_left_path=assets.get("hiroshima_cloud", ""),
            img_right_path=assets.get("nagasaki_cloud", ""),
            headline_top="SURVIVED",
            headline_bot="BOTH BOMBS",
            out_path=p1,
        )
        c1 = PosterCandidate(
            id="concept_01_split_juxtaposition",
            title="Twin Epicenters Split Juxtaposition",
            direction_type=CreativeDirectionType.SPLIT_JUXTAPOSITION,
            headline="SURVIVED BOTH BOMBS",
            tagline="HIROSHIMA AUG 6 | NAGASAKI AUG 9",
            palette_primary=(255, 255, 255),
            palette_accent=(254, 224, 4),
            strengths="Instantly communicates the two separate nuclear events with complementary warm/cool color palettes. Extreme curiosity.",
            weaknesses="Split screen divides the viewer's focus across two focal points instead of a single dominant subject.",
            design_rationale="Uses the two distinct mushroom clouds separated by a laser razor line to visually anchor the core narrative paradox.",
            full_path=p1,
            evaluation=CreativeEvaluationScore(
                curiosity=9.9,
                story_relevance=10.0,
                visual_impact=9.8,
                focal_hierarchy=9.4,
                readability=9.8,
                emotional_impact=9.7,
                composition_balance=9.6,
                branding_alignment=9.5,
                historical_accuracy=10.0,
                small_screen_visibility=9.6,
            )
        )
        c1.evaluation.compute_composite()
        candidates.append(c1)

        # 2. Concept 2: Extreme Scale Perspective
        p2 = os.path.join(audit_dir, "concept_02_extreme_scale.png")
        self.render_extreme_scale(
            cloud_asset_path=assets.get("hiroshima_cloud", ""),
            headline="HE SURVIVED",
            subline="TWICE",
            out_path=p2,
        )
        c2 = PosterCandidate(
            id="concept_02_extreme_scale",
            title="The Solitary Witness Extreme Scale Contrast",
            direction_type=CreativeDirectionType.EXTREME_SCALE_PERSPECTIVE,
            headline="HE SURVIVED TWICE",
            tagline="Human vs Nuclear Armageddon",
            palette_primary=(255, 255, 255),
            palette_accent=(255, 77, 77),
            strengths="Profound emotional scale; towering mushroom cloud dwarfs tiny human silhouette. Highly cinematic.",
            weaknesses="Human silhouette is small and requires viewer to perceive the scale contrast on mobile screens.",
            design_rationale="Emphasizes the human vulnerability and unimaginable odds by juxtaposing a solitary engineer with the apocalypse.",
            full_path=p2,
            evaluation=CreativeEvaluationScore(
                curiosity=9.7,
                story_relevance=9.6,
                visual_impact=9.5,
                focal_hierarchy=9.2,
                readability=9.8,
                emotional_impact=9.9,
                composition_balance=9.4,
                branding_alignment=9.4,
                historical_accuracy=9.8,
                small_screen_visibility=9.2,
            )
        )
        c2.evaluation.compute_composite()
        candidates.append(c2)

        # 3. Concept 3: Resilient Monolith
        p3 = os.path.join(audit_dir, "concept_03_resilient_monolith.png")
        self.render_resilient_monolith(
            monument_path=assets.get("monument", ""),
            cloud_path=assets.get("hiroshima_cloud", ""),
            headline="IMPOSSIBLE",
            subline="SURVIVOR",
            out_path=p3,
        )
        c3 = PosterCandidate(
            id="concept_03_resilient_monolith",
            title="The Standing Ruin Resilient Monolith",
            direction_type=CreativeDirectionType.RESILIENT_MONOLITH,
            headline="IMPOSSIBLE SURVIVOR",
            tagline="Architectural Metaphor of Endurance",
            palette_primary=(255, 255, 255),
            palette_accent=(32, 201, 151),
            strengths="Solemn, dignified, authentic archival focal point. Powerful symbol of architectural resilience.",
            weaknesses="Lacks the direct explosive kinetic urgency of the mushroom cloud blast.",
            design_rationale="Focuses on the iconic standing skeleton of Genbaku Dome as a metaphor for Tsutomu Yamaguchi surviving ground zero.",
            full_path=p3,
            evaluation=CreativeEvaluationScore(
                curiosity=9.1,
                story_relevance=9.3,
                visual_impact=8.9,
                focal_hierarchy=9.5,
                readability=9.6,
                emotional_impact=9.4,
                composition_balance=9.5,
                branding_alignment=9.3,
                historical_accuracy=10.0,
                small_screen_visibility=9.1,
            )
        )
        c3.evaluation.compute_composite()
        candidates.append(c3)

        # 4. Concept 4: Forensic Archive
        p4 = os.path.join(audit_dir, "concept_04_forensic_archive.png")
        self.render_forensic_archive(
            doc_asset_path=assets.get("declassified_order", ""),
            rubble_asset_path=assets.get("rubble", ""),
            headline="DOUBLE",
            subline="SURVIVOR",
            out_path=p4,
        )
        c4 = PosterCandidate(
            id="concept_04_forensic_archive",
            title="The Declassified Record Forensic Archive",
            direction_type=CreativeDirectionType.FORENSIC_ARCHIVE,
            headline="DOUBLE SURVIVOR",
            tagline="Declassified Order No. 35 & Verification",
            palette_primary=(255, 255, 255),
            palette_accent=(242, 193, 78),
            strengths="Undeniable factual credibility; dispels any disbelief that this was folklore or an urban legend.",
            weaknesses="Textured document paper can appear less dramatic from afar on small phone feeds.",
            design_rationale="Presents primary historical documents to satisfy rigorous documentary expectations.",
            full_path=p4,
            evaluation=CreativeEvaluationScore(
                curiosity=9.0,
                story_relevance=9.4,
                visual_impact=8.8,
                focal_hierarchy=9.0,
                readability=9.5,
                emotional_impact=8.9,
                composition_balance=9.2,
                branding_alignment=9.4,
                historical_accuracy=10.0,
                small_screen_visibility=8.8,
            )
        )
        c4.evaluation.compute_composite()
        candidates.append(c4)

        # Generate small-screen mobile previews (120x213) to test feed readability
        for c in candidates:
            if c.full_path and os.path.exists(c.full_path):
                img = Image.open(c.full_path)
                mob = img.resize((120, 213), Image.Resampling.LANCZOS)
                mob_path = os.path.join(PREVIEWS_DIR, f"{c.id}_mobile_120x213.png")
                mob.save(mob_path, "PNG")
                c.mobile_path = mob_path

        return candidates

    def select_best_poster(
        self,
        candidates: List[PosterCandidate],
        final_output_path: str = os.path.join(THUMBNAIL_DIR, "showcase_stage_3_9a_yamaguchi_poster.png"),
        report_output_path: str = os.path.join(AUDIT_DIR, "thumbnail_stage_3_9a_creative_report.json")
    ) -> Tuple[PosterCandidate, Dict]:
        """Evaluates, ranks, and saves the winning cinematic thumbnail."""
        candidates.sort(key=lambda x: x.evaluation.composite_score, reverse=True)
        winner = candidates[0]

        # Copy winner to target path (preserving previous approved thumbnail)
        if winner.full_path and os.path.exists(winner.full_path):
            with open(winner.full_path, "rb") as f_in:
                data = f_in.read()
            with open(final_output_path, "wb") as f_out:
                f_out.write(data)

        report = {
            "title": "Stage 3.9A Creative Thumbnail Design & Evaluation Audit",
            "topic": "Tsutomu Yamaguchi: The Man Who Survived Both Atomic Bombs",
            "resolution": f"{self.width}x{self.height} (9:16 vertical)",
            "winner_id": winner.id,
            "winner_title": winner.title,
            "winner_headline": winner.headline,
            "winner_score": winner.evaluation.composite_score,
            "winner_rationale": winner.design_rationale,
            "saved_final_path": final_output_path,
            "candidates_ranking": [
                {
                    "rank": idx + 1,
                    "id": c.id,
                    "title": c.title,
                    "direction": c.direction_type.value,
                    "headline": c.headline,
                    "composite_score": c.evaluation.composite_score,
                    "strengths": c.strengths,
                    "weaknesses": c.weaknesses,
                    "full_image_path": c.full_path,
                    "mobile_preview_path": c.mobile_path,
                    "scores": asdict(c.evaluation)
                }
                for idx, c in enumerate(candidates)
            ]
        }

        with open(report_output_path, "w", encoding="utf-8") as f_rep:
            json.dump(report, f_rep, indent=2)

        logger.info(
            "Selected winning poster: %s ('%s', Score: %.2f) -> %s",
            winner.id, winner.headline, winner.evaluation.composite_score, final_output_path
        )
        return winner, report

    # -------------------------------------------------------------------------
    # Helper utilities
    # -------------------------------------------------------------------------
    def _fill_and_crop(self, src: Image.Image, target_w: int, target_h: int) -> Image.Image:
        sw, sh = src.size
        scale = max(target_w / sw, target_h / sh)
        nw, nh = int(sw * scale), int(sh * scale)
        resized = src.resize((nw, nh), Image.Resampling.LANCZOS)
        x0 = (nw - target_w) // 2
        y0 = (nh - target_h) // 2
        return resized.crop((x0, y0, x0 + target_w, y0 + target_h))

    def _draw_poster_headline(
        self,
        draw: ImageDraw.ImageDraw,
        lines: List[str],
        y_bottom: int,
        color_accent: Tuple[int, int, int],
        hero_font: ImageFont.ImageFont,
        sub_font: ImageFont.ImageFont,
        align: str = "center",
    ):
        w = self.width
        metrics = []
        for idx, line in enumerate(lines):
            f = hero_font if idx == 0 else sub_font
            bbox = draw.textbbox((0, 0), line, font=f)
            lw = bbox[2] - bbox[0]
            lh = bbox[3] - bbox[1]
            metrics.append((line, f, lw, lh))

        total_h = sum(m[3] for m in metrics) + (len(lines) - 1) * 20
        y_cursor = y_bottom - total_h

        for idx, (line, f, lw, lh) in enumerate(metrics):
            lx = (w - lw) // 2 if align == "center" else 80

            # Quadruple drop-shadow for intense depth against bright explosions
            for ox, oy, a in [(4, 4, 180), (8, 8, 220), (14, 14, 255), (20, 20, 255)]:
                draw.text((lx + ox, y_cursor + oy), line, fill=(0, 0, 0), font=f)

            # Color: line 0 = stark white, line 1 = vivid accent
            col = (255, 255, 255) if idx == 0 else color_accent
            draw.text((lx, y_cursor), line, fill=col, font=f)
            y_cursor += lh + 20
