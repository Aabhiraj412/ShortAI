"""
Documentary Script, Media Acquisition & 20-Beat Visual Choreography Builder.
Phase 5, Stage 3.21I Standard.

Coordinates:
1. Structured 5-stage factual script composition (50-58s Shorts pacing) from verified research.
2. Expressive TTS synthesis via ExpressiveTTSEngine (Microsoft Edge Neural TTS) + FFprobe timing.
3. Workspace-isolated openly licensed media acquisition (Wikimedia Commons / NASA / Openverse).
4. High-craft 20-beat vertical 1080x1920 documentary storyboard + synchronized ASS subtitles.
"""

import asyncio
from dataclasses import asdict
import json
import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from backend.app.content.creative_production_policy import (
    CategorySpec,
    get_creative_production_policy,
)
from backend.app.content.curiosity_topic_engine import ScoredTopic
from backend.app.content.visual_diversity_policy import (
    MAX_IMAGE_REUSE,
    MIN_DISTINCT_ASSETS_FULL_SHORT,
    VisualDiversityValidator,
    compute_asset_sha256,
)
from backend.app.core.execution_context import ExecutionContext
from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE_ROOT
from backend.app.media.acquisition_manager import MediaAcquisitionEngine
from backend.app.media.attribution_generator import AttributionGenerator
from backend.app.media.downloader import AssetDownloader
from backend.app.media.image_processor import AdaptationMode, ImageProcessor
from backend.app.media.models import AttributionManifest, MediaAsset
from backend.app.orchestration.production_workspace import ProductionWorkspace, compute_file_sha256
from backend.app.rendering.engines.vesna_fast_renderer import VisualBeatConfig
from backend.app.rendering.explainer_animation_engine import ExplainerAnimationEngine
from backend.app.rendering.types import SceneBeat, StoryboardPlan, TransitionType, VisualModality
from backend.app.services.expressive_tts_engine import ExpressiveTTSEngine, NarrationSegment

logger = logging.getLogger(__name__)

DEFAULT_MUSIC_PATH = os.path.join(WORKSPACE_ROOT, "assets", "audio", "music", "ambient_tech_theme.wav")
DEFAULT_SFX_CHIME = os.path.join(WORKSPACE_ROOT, "assets", "audio", "sfx", "chime_pop.wav")
DEFAULT_SFX_WHOOSH = os.path.join(WORKSPACE_ROOT, "assets", "audio", "sfx", "whoosh_transition.wav")


class _IsolatedAssetRegistry:
    """No-op SQLite asset registry for non-PRODUCTION runs so studio.db stays untouched."""

    def __init__(self):
        self.recorded: List[Dict[str, Any]] = []

    def register_asset(self, asset: MediaAsset, job_id: Optional[str] = None) -> int:
        self.recorded.append({"asset_id": asset.asset_id, "job_id": job_id, "local_path": asset.local_path})
        return len(self.recorded)


def _load_font(candidates: List[str], size: int) -> ImageFont.FreeTypeFont:
    for name in candidates:
        win_path = os.path.join("C:/Windows/Fonts", name)
        for p in (name, win_path):
            if os.path.isfile(p):
                try:
                    return ImageFont.truetype(p, size)
                except Exception:
                    continue
    return ImageFont.load_default()


def _wrap_text_lines(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> List[str]:
    words = (text or "").split()
    lines: List[str] = []
    cur = ""
    for w in words:
        cand = f"{cur} {w}".strip()
        bbox = draw.textbbox((0, 0), cand, font=font)
        if (bbox[2] - bbox[0]) <= max_width or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def compose_structured_script(
    topic: ScoredTopic,
    hook_text: str,
    execution_context: ExecutionContext = ExecutionContext.VALIDATION,
) -> Dict[str, Any]:
    """
    Composes a tight, factual 5-section documentary narration script (~104-124 words, ~48-56s spoken)
    strictly grounded in the topic's verified research extract and shaped by its category's
    narrative arc from the shared permanent CreativeProductionPolicy.
    """
    policy = get_creative_production_policy(execution_context)
    cat_spec: CategorySpec = policy.get_category(getattr(topic, "category", "science"))
    arc = cat_spec.narrative_arc or ["question", "mechanism", "evidence", "revelation"]

    s1 = (hook_text or "").strip()
    if s1 and not s1.endswith((".", "!", "?")):
        s1 += "."

    raw_pool: List[str] = []
    if getattr(topic, "factual_bullets", None):
        raw_pool.extend(topic.factual_bullets)
    if getattr(topic, "extended_extract", None):
        raw_pool.extend(re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", topic.extended_extract))
    raw_pool.extend(re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", topic.summary or ""))

    s1_norm = re.sub(r"\W+", " ", s1.lower()).strip()
    seen_norms = {s1_norm}
    facts: List[str] = []
    for sent in raw_pool:
        clean = re.sub(r"\s+", " ", (sent or "")).strip()
        if not clean or clean.startswith("=="):
            continue
        if not clean.endswith((".", "!", "?")):
            clean += "."
        wc = len(clean.split())
        if wc < 6 or wc > 38:
            continue
        norm = re.sub(r"\W+", " ", clean.lower()).strip()
        if norm in seen_norms or any(norm[:40] == sn[:40] for sn in seen_norms):
            continue
        seen_norms.add(norm)
        facts.append(clean)

    title_anchor = topic.title.split("(")[0].strip()

    def _pop_next_fact(used: set, fallback: str) -> str:
        for cand in facts:
            cn = re.sub(r"\W+", " ", cand.lower()).strip()
            if cn not in used and not any(cn[:35] == u[:35] for u in used):
                used.add(cn)
                return cand
        return fallback

    used_norms = {s1_norm}
    sec_hook = s1 or _pop_next_fact(used_norms, f"Why does {title_anchor} still astonish experts today?")
    f1 = _pop_next_fact(used_norms, topic.summary)
    sec_context = (
        f1
        if title_anchor.lower() in f"{sec_hook} {f1}".lower()
        else f"At the core of {title_anchor} is this finding: {f1}"
    )

    arc_step2 = arc[1].replace("_", " ") if len(arc) > 1 else "mechanism"
    arc_step3 = arc[2].replace("_", " ") if len(arc) > 2 else "evidence"
    sec_escalation = _pop_next_fact(
        used_norms,
        f"Examining the {arc_step2} behind {title_anchor} uncovered an unexpected structural dynamic.",
    )
    sec_revelation = _pop_next_fact(
        used_norms,
        f"Verified {arc_step3} confirmed the real impact of {title_anchor}.",
    )
    extra_fact = _pop_next_fact(used_norms, "")

    closing = f"And that is how {title_anchor} reshaped our understanding. Subscribe to CrazzyDocs for more real-world stories."
    sec_payoff = f"{extra_fact} {closing}".strip()

    stage_tags = [
        arc[0].upper()[:16] if len(arc) > 0 else "HOOK",
        "CONTEXT",
        arc[1].upper()[:16] if len(arc) > 1 else "ESCALATION",
        arc[2].upper()[:16] if len(arc) > 2 else "REVELATION",
        arc[3].upper()[:16] if len(arc) > 3 else "PAYOFF",
    ]
    sections = [
        {"id": "seg_01_hook", "stage": "HOOK", "arc_phase": stage_tags[0], "emotion": "hook", "text": sec_hook},
        {"id": "seg_02_context", "stage": "CONTEXT", "arc_phase": stage_tags[1], "emotion": "curiosity", "text": sec_context},
        {"id": "seg_03_escalation", "stage": "ESCALATION", "arc_phase": stage_tags[2], "emotion": "tension", "text": sec_escalation},
        {"id": "seg_04_revelation", "stage": "REVELATION", "arc_phase": stage_tags[3], "emotion": "revelation", "text": sec_revelation},
        {"id": "seg_05_payoff", "stage": "PAYOFF", "arc_phase": stage_tags[4], "emotion": "closing", "text": sec_payoff},
    ]

    # Trim gently if total word count > 124 so spoken audio + 2.0s outro never crosses 58.0s
    total_words = sum(len(s["text"].split()) for s in sections)
    if total_words > 124 and extra_fact:
        sections[4]["text"] = closing
        total_words = sum(len(s["text"].split()) for s in sections)

    # Pad cleanly from extra verified facts if too short (< 104 words)
    fact_idx = 4
    while total_words < 104 and fact_idx < len(facts):
        candidate_sent = facts[fact_idx]
        fact_idx += 1
        target_sec = sections[2] if (fact_idx % 2 == 1) else sections[3]
        target_sec["text"] = f"{target_sec['text']} {candidate_sent}".strip()
        total_words = sum(len(s["text"].split()) for s in sections)

    full_script = " ".join(s["text"] for s in sections).strip()
    return {
        "full_script": full_script,
        "sections": sections,
        "word_count": len(full_script.split()),
        "category": cat_spec.display_name,
        "narrative_arc": arc,
    }


def synthesize_and_probe_narration(
    tts_engine: ExpressiveTTSEngine,
    sections: List[Dict[str, Any]],
    prod_ws: ProductionWorkspace,
) -> Tuple[str, List[Dict[str, Any]], float]:
    """
    Synthesizes each narrative section with real Edge Neural TTS and probes exact durations.
    Returns: (combined_voiceover_mp3_path, section_timings, total_voice_duration_sec)
    """
    seg_dir = os.path.join(prod_ws.narration_dir, "segments")
    os.makedirs(seg_dir, exist_ok=True)

    narration_segs: List[NarrationSegment] = []
    emotion_prosody = {
        "hook": ("shocked", "+4%", "+2Hz"),
        "curiosity": ("curious", "+0%", "+0Hz"),
        "tension": ("suspenseful", "-2%", "-2Hz"),
        "revelation": ("dramatic", "+2%", "+1Hz"),
        "closing": ("emphatic", "+0%", "-2Hz"),
    }
    for idx, sec in enumerate(sections):
        post_ms = 120 if idx < len(sections) - 1 else 250
        emo, rate, pitch = emotion_prosody.get(sec["emotion"], ("curious", "+0%", "+0Hz"))
        narration_segs.append(
            NarrationSegment(
                id=sec["id"],
                emotion=emo,
                text=sec["text"],
                rate=rate,
                pitch=pitch,
                pre_pause_ms=40 if idx == 0 else 60,
                post_pause_ms=post_ms,
                delivery_note=sec["stage"],
            )
        )

    async def _run_all() -> List[NarrationSegment]:
        out: List[NarrationSegment] = []
        for seg in narration_segs:
            done = await tts_engine.synthesize_segment(seg, seg_dir, force_regenerate=True)
            out.append(done)
        return out

    synthesized = asyncio.run(_run_all())

    os.makedirs(prod_ws.audio_dir, exist_ok=True)
    combined_mp3 = os.path.join(prod_ws.audio_dir, f"voiceover_{prod_ws.production_id}.mp3")
    tts_engine.concatenate_segments(synthesized, combined_mp3)

    # Probe cumulative timings cleanly
    total_voice_dur = tts_engine.probe_duration(combined_mp3)
    # Keep narrative window comfortably inside 48.8s - 55.6s so +2.0s outro hits 50.8s - 57.6s
    if 42.0 <= total_voice_dur < 48.8:
        pad_sec = round(49.2 - total_voice_dur, 3)
        padded_mp3 = os.path.join(prod_ws.audio_dir, f"voiceover_{prod_ws.production_id}_padded.mp3")
        from backend.app.core.paths import FFMPEG_DEFAULT_PATH
        import subprocess
        res = subprocess.run(
            [
                str(FFMPEG_DEFAULT_PATH), "-y", "-i", combined_mp3,
                "-af", f"apad=pad_dur={pad_sec:.3f}",
                "-c:a", "libmp3lame", "-b:a", "192k", padded_mp3,
            ],
            capture_output=True, text=True, check=False,
        )
        if res.returncode == 0 and os.path.isfile(padded_mp3):
            os.replace(padded_mp3, combined_mp3)
            total_voice_dur = tts_engine.probe_duration(combined_mp3)
    elif total_voice_dur > 55.6:
        tempo = min(1.28, max(1.01, round(total_voice_dur / 54.4, 4)))
        tempo_mp3 = os.path.join(prod_ws.audio_dir, f"voiceover_{prod_ws.production_id}_tempo.mp3")
        from backend.app.core.paths import FFMPEG_DEFAULT_PATH
        import subprocess
        res = subprocess.run(
            [
                str(FFMPEG_DEFAULT_PATH), "-y", "-i", combined_mp3,
                "-af", f"atempo={tempo:.4f}",
                "-c:a", "libmp3lame", "-b:a", "192k", tempo_mp3,
            ],
            capture_output=True, text=True, check=False,
        )
        if res.returncode == 0 and os.path.isfile(tempo_mp3):
            os.replace(tempo_mp3, combined_mp3)
            total_voice_dur = tts_engine.probe_duration(combined_mp3)

    raw_sum = sum(max(0.5, s.duration_sec) for s in synthesized)
    scale = (total_voice_dur / raw_sum) if raw_sum > 0 and total_voice_dur > 0 else 1.0

    cursor = 0.0
    timings: List[Dict[str, Any]] = []
    for i, (sec, seg) in enumerate(zip(sections, synthesized)):
        seg_dur = round(max(1.0, seg.duration_sec * scale), 3)
        start_s = round(cursor, 3)
        end_s = round(total_voice_dur if i == len(synthesized) - 1 else cursor + seg_dur, 3)
        timings.append({
            "id": sec["id"],
            "stage": sec["stage"],
            "text": sec["text"],
            "start_sec": start_s,
            "end_sec": end_s,
            "duration_sec": round(end_s - start_s, 3),
            "audio_path": seg.audio_path,
        })
        cursor = end_s

    return combined_mp3, timings, round(total_voice_dur, 3)


def acquire_isolated_topic_assets(
    topic: ScoredTopic,
    prod_ws: ProductionWorkspace,
    execution_context: ExecutionContext,
    max_assets: int = 10,
    stats_out: Optional[Dict[str, Any]] = None,
) -> Tuple[List[MediaAsset], Optional[AttributionManifest], List[Dict[str, Any]]]:
    """
    Acquires openly licensed topic visuals (Priority A authentic images & maps, Priority B open-source
    animations) directly into prod_ws.assets_acquired_dir using the shared permanent
    CreativeProductionPolicy (identical across VALIDATION and PRODUCTION).
    """
    _ = get_creative_production_policy(execution_context)
    raw_dir = getattr(prod_ws, "assets_acquired_dir", os.path.join(prod_ws.assets_dir, "acquired"))
    portrait_dir = os.path.join(prod_ws.assets_dir, "portrait")
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(portrait_dir, exist_ok=True)

    registry = None if execution_context == ExecutionContext.PRODUCTION else _IsolatedAssetRegistry()
    downloader = AssetDownloader(storage_dir=raw_dir)
    processor = ImageProcessor(output_dir=portrait_dir)
    attrib_gen = AttributionGenerator(manifests_dir=prod_ws.metadata_dir)

    from backend.app.media.license_verifier import LicenseVerifier
    from backend.app.media.models import LicenseStatus
    from backend.app.media.providers.internet_archive_provider import InternetArchiveProvider
    from backend.app.media.providers.nasa_provider import NASAProvider
    from backend.app.media.providers.openverse_provider import OpenverseProvider
    from backend.app.media.providers.stock_provider import PexelsProvider, PixabayProvider
    from backend.app.media.providers.wikimedia_provider import WikimediaProvider

    verifier = LicenseVerifier()
    wiki_provider = WikimediaProvider(license_verifier=verifier)
    ia_provider = InternetArchiveProvider(license_verifier=verifier)

    topic_blob = f"{topic.title} {topic.category} {topic.summary}".lower()
    is_space_or_geo = any(
        k in topic_blob
        for k in (
            "space", "nasa", "quasar", "galaxy", "black hole", "planet", "orbit", "telescope",
            "moon", "mars", "rocket", "apollo", "voyager", "satellite", "observatory",
            "earth", "atmosphere", "climate", "glacier", "tectonic", "geography",
        )
    )
    providers = [wiki_provider, OpenverseProvider(license_verifier=verifier), ia_provider]
    if is_space_or_geo:
        providers.append(NASAProvider(license_verifier=verifier))
    # Optional commercial stock providers when configured with env keys
    pexels = PexelsProvider(license_verifier=verifier)
    if pexels.is_configured():
        providers.append(pexels)
    pixabay = PixabayProvider(license_verifier=verifier)
    if pixabay.is_configured():
        providers.append(pixabay)

    # 1. Directly fetch openly licensed Wikimedia Commons files linked to the topic's Wikipedia article
    acquired: List[MediaAsset] = []
    seen_hashes = set()
    failed_downloads = 0
    article_file_titles = [u for u in (getattr(topic, "image_urls", []) or []) if isinstance(u, str) and u.startswith("File:")]
    if article_file_titles:
        direct_candidates = wiki_provider.fetch_files_by_titles(article_file_titles, topic_title=topic.title)
        for cand in direct_candidates:
            if cand.verification_status != LicenseStatus.APPROVED:
                continue
            if cand.width and cand.width < 180 and getattr(cand, "asset_type", "") != "existing_animation":
                continue
            ok, local_path, sha_h, _ = downloader.download_asset(cand)
            if ok and local_path and os.path.isfile(local_path):
                file_sha = sha_h or compute_asset_sha256(local_path)
                if file_sha in seen_hashes:
                    continue
                try:
                    cand.asset_hash = file_sha
                    if getattr(cand, "asset_type", "") == "existing_animation" or local_path.lower().endswith((".gif", ".webm", ".mp4")):
                        cand.asset_type = "existing_animation"
                        cand.portrait_path = local_path
                    else:
                        cand.portrait_path = processor.adapt_image_to_portrait(
                            input_path=local_path, mode=AdaptationMode.FIT_BLURRED_BACKGROUND
                        )
                    seen_hashes.add(file_sha)
                    acquired.append(cand)
                except Exception:
                    failed_downloads += 1
                    continue
            else:
                failed_downloads += 1
            if len(acquired) >= max_assets:
                break

    acq_engine = MediaAcquisitionEngine(
        providers=providers,
        downloader=downloader,
        processor=processor,
        verifier=verifier,
        registry=registry,
        attribution_gen=attrib_gen,
    )

    clean_title = topic.title.split(":")[0].split("(")[0].strip()
    queries = [clean_title]
    generic_words = {"scientists", "researchers", "history", "study", "years", "earliest", "known", "record", "theory"}
    for ent in (topic.key_entities or []):
        if ent.lower() != clean_title.lower() and ent.lower() not in generic_words and len(ent) >= 4:
            queries.append(ent)
        if len(queries) >= 4:
            break
    if len(queries) < 3:
        queries.append(f"{clean_title} {topic.category}")

    engine_stats: Dict[str, Any] = {}
    if len(acquired) < max_assets:
        extra_acquired, _ = asyncio.run(
            acq_engine.acquire_scene_assets(
                topic_title=topic.title,
                category=topic.category,
                scene_queries=queries[:4],
                job_id=prod_ws.production_id,
                max_assets=max_assets - len(acquired),
                adaptation_mode=AdaptationMode.FIT_BLURRED_BACKGROUND,
            )
        )
        engine_stats = dict(getattr(acq_engine, "last_acquisition_stats", {}) or {})
        failed_downloads += int(engine_stats.get("failed", 0))
        for ea in extra_acquired:
            file_sha = ea.asset_hash or compute_asset_sha256(ea.local_path)
            if file_sha and file_sha in seen_hashes:
                continue
            ea.asset_hash = file_sha
            acquired.append(ea)
            if file_sha:
                seen_hashes.add(file_sha)

    existing_anims_count = sum(
        1 for a in acquired
        if getattr(a, "asset_type", "") == "existing_animation"
        or (a.local_path or "").lower().endswith((".gif", ".webm", ".mp4"))
    )
    authentic_imgs_count = len(acquired) - existing_anims_count
    rejected_licenses = int(engine_stats.get("rejected_license_count", 0))

    if stats_out is not None:
        stats_out.update({
            "unique_images": authentic_imgs_count,
            "authentic_images_acquired": authentic_imgs_count,
            "existing_animations_acquired": existing_anims_count,
            "rejected_license_count": rejected_licenses,
            "failed_acquisitions": failed_downloads,
            "failed_providers": list(getattr(acq_engine, "last_failed_providers", []) or []),
        })

    attrib_manifest = attrib_gen.generate_manifest(video_id=prod_ws.production_id, assets=acquired) if acquired else None

    manifest_assets: List[Dict[str, Any]] = []
    for asset in acquired:
        manifest_assets.append({
            "asset_id": asset.asset_id,
            "filename": os.path.basename(asset.local_path or ""),
            "original_filename": getattr(asset, "original_filename", "") or os.path.basename(asset.local_path or ""),
            "path": asset.local_path,
            "portrait_path": asset.portrait_path,
            "asset_type": getattr(asset, "asset_type", "authentic_image"),
            "title": asset.title,
            "creator": asset.creator or "Public Archive Contributor",
            "provider": getattr(asset, "source_repository", "") or "Open Archive",
            "source_repository": asset.source_repository,
            "source_url": asset.source_url,
            "license": asset.license_type.value,
            "license_url": asset.license_url,
            "download_timestamp": getattr(asset, "download_timestamp", ""),
            "usage_restrictions": getattr(asset, "usage_restrictions", "None"),
            "attribution_requirements": getattr(asset, "attribution_requirements", "Credit author & license"),
            "modification_permitted": getattr(asset, "modification_permitted", True),
            "width": asset.width,
            "height": asset.height,
            "sha256": asset.asset_hash or compute_file_sha256(asset.local_path or ""),
        })

    return acquired, attrib_manifest, manifest_assets


def _extract_short_callout(sentence: str, max_words: int = 5) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9\s\-']", "", sentence or "").strip()
    words = [w for w in cleaned.split() if len(w) > 1]
    if not words:
        return "VERIFIED RECORD"
    return " ".join(words[:max_words]).upper()


def _extract_stat_highlight(sentence: str) -> str:
    m = re.search(r"\b(\d[\d,\.]*(?:\s*(?:%|km|miles|meters|feet|years|million|billion))?)\b", sentence or "", re.IGNORECASE)
    if m:
        return m.group(1).upper()
    m_yr = re.search(r"\b(1[0-9]{3}|20[0-2][0-9])\b", sentence or "")
    if m_yr:
        return m_yr.group(1)
    return "DOCUMENTED FACT"


def _render_documentary_plate(
    out_path: str,
    width: int,
    height: int,
    beat_idx: int,
    total_beats: int,
    stage_label: str,
    callout_text: str,
    stat_text: str,
    topic_title: str,
    accent_color: Tuple[int, int, int],
    layout_mode: str,
    primary_img_path: Optional[str],
    secondary_img_path: Optional[str] = None,
) -> str:
    """
    Renders a sharp 1080x1920 vertical documentary composition plate for a single visual beat.
    Respects 9:16 YouTube Shorts safe zones (top >= 220px, bottom subtitle clear >= 340px).
    """
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    # 1. Backdrop
    if primary_img_path and os.path.isfile(primary_img_path):
        src = Image.open(primary_img_path).convert("RGB")
        bg = src.resize((width, height), Image.Resampling.BILINEAR).filter(ImageFilter.GaussianBlur(28))
        tint = Image.new("RGBA", (width, height), (10, 14, 24, 175))
        canvas = Image.alpha_composite(bg.convert("RGBA"), tint).convert("RGB")
    else:
        src = None
        canvas = Image.new("RGB", (width, height), (14, 20, 34))
        bg_draw = ImageDraw.Draw(canvas)
        for y in range(0, height, 40):
            shade = int(14 + (y / height) * 18)
            bg_draw.line([(0, y), (width, y)], fill=(shade, shade + 8, shade + 24), width=40)
        # Subtle grid lines for analytical card aesthetic
        for x in range(0, width, 90):
            bg_draw.line([(x, 0), (x, height)], fill=(26, 36, 56), width=1)
        for y in range(0, height, 90):
            bg_draw.line([(0, y), (width, y)], fill=(26, 36, 56), width=1)

    draw = ImageDraw.Draw(canvas)
    font_badge = _load_font(["arialbd.ttf", "seguisb.ttf"], 28)
    font_head = _load_font(["impact.ttf", "arialbd.ttf"], 54)
    font_stat = _load_font(["impact.ttf", "arialbd.ttf"], 84)
    font_sub = _load_font(["seguisb.ttf", "arialbd.ttf"], 30)

    # Safe inner margin = 140px horizontally and y>=280 vertically so PhotoSceneRenderer 1.15x Ken Burns
    # zoom/pan NEVER clips progress ticks, pills, photo borders, or callouts at the viewport edges.
    pad_x = 140
    bar_x0, bar_x1, bar_y = pad_x, width - pad_x, 282
    seg_gap = 5
    seg_w = max(10, int((bar_x1 - bar_x0 - (total_beats - 1) * seg_gap) / total_beats))
    for i in range(total_beats):
        sx = bar_x0 + i * (seg_w + seg_gap)
        color = accent_color if i <= beat_idx else (52, 65, 88)
        draw.rounded_rectangle([sx, bar_y, sx + seg_w, bar_y + 8], radius=4, fill=color)

    # 3. Top stage pill (y=308)
    pill_txt = f"{stage_label}  •  BEAT {beat_idx + 1}/{total_beats}"
    pb = draw.textbbox((0, 0), pill_txt, font=font_badge)
    pw = min(width - 2 * pad_x, (pb[2] - pb[0]) + 44)
    draw.rounded_rectangle([pad_x, 308, pad_x + pw, 360], radius=22, fill=(18, 26, 42), outline=accent_color, width=3)
    draw.text((pad_x + pw // 2, 334), pill_txt, font=font_badge, fill=(245, 248, 255), anchor="mm")

    # 4. Main visual focal frame (y=386..1205, strictly inside x=140..940)
    frame_box = (pad_x, 386, width - pad_x, 1205)
    fw = frame_box[2] - frame_box[0]
    fh = frame_box[3] - frame_box[1]

    if layout_mode == "split_dual" and src is not None and secondary_img_path and os.path.isfile(secondary_img_path):
        src2 = Image.open(secondary_img_path).convert("RGB")
        half_h = (fh - 24) // 2
        for panel_idx, p_img in enumerate((src, src2)):
            py0 = frame_box[1] + panel_idx * (half_h + 24)
            scale = max(fw / p_img.width, half_h / p_img.height)
            resized = p_img.resize((int(p_img.width * scale), int(p_img.height * scale)), Image.Resampling.LANCZOS)
            cx = (resized.width - fw) // 2
            cy = (resized.height - half_h) // 2
            cropped = resized.crop((cx, cy, cx + fw, cy + half_h))
            canvas.paste(cropped, (frame_box[0], py0))
            draw.rectangle([frame_box[0], py0, frame_box[2], py0 + half_h], outline=accent_color, width=4)
    elif layout_mode == "stat_card":
        if src is not None:
            photo_h = 470
            scale = max(fw / src.width, photo_h / src.height)
            resized = src.resize((int(src.width * scale), int(src.height * scale)), Image.Resampling.LANCZOS)
            cx = (resized.width - fw) // 2
            cy = (resized.height - photo_h) // 2
            cropped = resized.crop((cx, cy, cx + fw, cy + photo_h))
            canvas.paste(cropped, (frame_box[0], frame_box[1]))
            draw.rectangle([frame_box[0], frame_box[1], frame_box[2], frame_box[1] + photo_h], outline=accent_color, width=4)
            stat_top = frame_box[1] + photo_h + 22
        else:
            stat_top = frame_box[1] + 50
        draw.rounded_rectangle([frame_box[0], stat_top, frame_box[2], frame_box[3]], radius=22, fill=(18, 25, 40), outline=accent_color, width=4)
        draw.text((width // 2, stat_top + 110), stat_text[:18], font=font_stat, fill=accent_color, anchor="mm")
        draw.text((width // 2, stat_top + 220), topic_title[:34].upper(), font=font_sub, fill=(225, 232, 245), anchor="mm")
    else:
        if src is not None:
            scale = max(fw / src.width, fh / src.height)
            resized = src.resize((int(src.width * scale), int(src.height * scale)), Image.Resampling.LANCZOS)
            cx = (resized.width - fw) // 2
            cy = (resized.height - fh) // 2
            cropped = resized.crop((cx, cy, cx + fw, cy + fh))
            canvas.paste(cropped, (frame_box[0], frame_box[1]))
            draw.rectangle(frame_box, outline=accent_color, width=5)
        else:
            draw.rounded_rectangle(frame_box, radius=24, fill=(20, 29, 46), outline=accent_color, width=4)
            draw.text((width // 2, (frame_box[1] + frame_box[3]) // 2 - 40), stat_text[:18], font=font_stat, fill=accent_color, anchor="mm")
            draw.text((width // 2, (frame_box[1] + frame_box[3]) // 2 + 75), topic_title[:34], font=font_sub, fill=(235, 240, 248), anchor="mm")

    # 5. Safe-zone Lower Documentary Callout Card (y=1230..1405, x=140..940)
    card_box = [pad_x, 1230, width - pad_x, 1405]
    draw.rounded_rectangle(card_box, radius=18, fill=(14, 19, 31), outline=(65, 82, 112), width=2)
    draw.rectangle([pad_x, 1230, pad_x + 14, 1405], fill=accent_color)

    lines = _wrap_text_lines(draw, callout_text, font_head, width - 2 * pad_x - 60)[:2]
    y_cursor = 1285 if len(lines) == 2 else 1318
    for line in lines:
        draw.text((pad_x + 42, y_cursor), line, font=font_head, fill=(250, 252, 255), anchor="lm")
        y_cursor += 62

    canvas.save(out_path, format="PNG")
    return out_path


def _write_aligned_ass_subtitles(
    timings: List[Dict[str, Any]],
    out_ass_path: str,
    max_end_sec: float,
) -> str:
    """
    Writes synchronized chunked ASS captions ending strictly before max_end_sec
    to ensure zero subtitle leakage onto the 2.0s branded CrazzyDocs outro.
    """
    os.makedirs(os.path.dirname(out_ass_path), exist_ok=True)

    def _fmt_ass_time(sec: float) -> str:
        sec = max(0.0, sec)
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = int(sec % 60)
        cs = int(round((sec - int(sec)) * 100))
        if cs >= 100:
            cs = 99
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,58,&H00FFFFFF,&H0000FFFF,&H00121622,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,70,70,340,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines: List[str] = [header.rstrip()]
    safe_cap = max(1.0, max_end_sec - 0.12)

    for sec in timings:
        s_start = float(sec["start_sec"])
        s_end = min(safe_cap, float(sec["end_sec"]))
        if s_end <= s_start + 0.1:
            continue
        words = (sec["text"] or "").split()
        if not words:
            continue
        chunk_size = 5
        chunks = [" ".join(words[i:i + chunk_size]) for i in range(0, len(words), chunk_size)]
        total_chars = sum(max(1, len(c)) for c in chunks)
        t_cur = s_start
        span = s_end - s_start
        for idx, chunk in enumerate(chunks):
            frac = max(1, len(chunk)) / total_chars
            c_end = s_end if idx == len(chunks) - 1 else min(s_end, t_cur + span * frac)
            safe_txt = chunk.replace("{", "(").replace("}", ")")
            lines.append(
                f"Dialogue: 0,{_fmt_ass_time(t_cur)},{_fmt_ass_time(c_end)},Default,,0,0,0,,{safe_txt}"
            )
            t_cur = c_end

    with open(out_ass_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return out_ass_path


def _select_explanatory_visual_type(
    beat_idx: int,
    stage: str,
    segment_text: str,
    full_script: str,
    has_coords: bool,
    used_counts: Dict[str, int],
) -> str:
    """Chooses the most script-grounded Stage 3.21J explanatory animation modality."""
    lower = (segment_text or "").lower()
    geo_words = (
        "island", "coast", "ocean", "sea", "city", "town", "country", "border",
        "region", "km", "miles", "latitude", "longitude", "mountain", "valley",
        "river", "north", "south", "east", "west", "location", "site", "park",
    )
    if (has_coords or any(w in lower for w in geo_words)) and used_counts.get("animated_map", 0) < 2:
        return "animated_map"
    if re.search(r"\b(1[0-9]{3}|20[0-2][0-9]|century|year|years|era|decades|before|after)\b", lower) and used_counts.get("animated_timeline", 0) < 2:
        return "animated_timeline"
    if re.search(r"\b\d+(?:\.\d+)?\s*(%|percent|km|m|ft|feet|million|billion|thousand|hours|days|years|kg|tons|mph)\b", lower) and used_counts.get("data_visualization", 0) < 2:
        return "data_visualization"

    cycle = [
        "animated_diagram",
        "animated_infographic",
        "motion_graphic",
        "typography_sequence",
        "animated_timeline",
        "data_visualization",
    ]
    if stage.upper() == "HOOK":
        cycle = ["typography_sequence", "motion_graphic", "animated_map", "animated_diagram"]
    elif stage.upper() in ("CLIMAX", "CLOSING"):
        cycle = ["motion_graphic", "animated_infographic", "typography_sequence", "data_visualization"]

    # Pick least-used option from candidate cycle for high visual variety
    return min(cycle, key=lambda vt: (used_counts.get(vt, 0), cycle.index(vt)))


def _spread_photo_indices(total_beats: int, photo_count: int) -> List[int]:
    """Spreads acquired-photo beats evenly across the storyboard with no adjacent pairs when avoidable."""
    if photo_count <= 0:
        return []
    if photo_count >= total_beats:
        return list(range(total_beats))
    if photo_count == 1:
        return [0]
    chosen: List[int] = []
    step = total_beats / float(photo_count)
    for k in range(photo_count):
        idx = min(total_beats - 1, int(round(k * step)))
        if chosen and idx <= chosen[-1]:
            idx = min(total_beats - 1, chosen[-1] + 1)
        chosen.append(idx)
    return chosen


def build_20_beat_storyboard(
    topic: ScoredTopic,
    timings: List[Dict[str, Any]],
    total_voice_dur: float,
    voiceover_path: str,
    acquired_assets: List[MediaAsset],
    prod_ws: ProductionWorkspace,
    *,
    script_full_text: str = "",
    progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
    max_image_reuse: int = MAX_IMAGE_REUSE,
    execution_context: ExecutionContext = ExecutionContext.VALIDATION,
) -> Tuple[StoryboardPlan, List[VisualBeatConfig], str]:
    """
    Generates a 20-beat multi-modal 1080x1920 documentary storyboard synced to voiceover timings.

    Stage 3.21M Visual Director Hierarchy (shared identically across VALIDATION & PRODUCTION):
    - Priority A: Authentic existing photographs, archival documents & scientific diagrams (<= max_image_reuse).
    - Priority B: Existing open-source GIF/WebM/MP4 animations adapted via FFmpeg into documentary 30 FPS portrait clips.
    - Priority C: Animated authentic composites (route lines, pulsing beacons, timelines, metrics over authentic assets).
    - Priority D: Selective procedural/conceptual illustrations (explicitly tagged illustrative_reconstruction=True).
    - Fails closed via VisualDiversityValidator before returning.
    """
    policy = get_creative_production_policy(execution_context)
    cat_spec = policy.get_category(getattr(topic, "category", "science"))
    effective_max_reuse = min(max_image_reuse, policy.visual_policy.max_image_reuse)

    plates_dir = os.path.join(prod_ws.assets_dir, "plates")
    os.makedirs(plates_dir, exist_ok=True)
    os.makedirs(prod_ws.assets_generated_dir, exist_ok=True)
    os.makedirs(prod_ws.assets_animations_dir, exist_ok=True)
    os.makedirs(prod_ws.storyboards_dir, exist_ok=True)

    # Partition acquired assets into Priority A (photos/diagrams) and Priority B (open animations)
    unique_photos: List[Dict[str, Any]] = []
    unique_animations: List[Dict[str, Any]] = []
    seen_hashes: set = set()
    for asset in acquired_assets:
        if not asset.local_path or not os.path.isfile(asset.local_path):
            continue
        sha = compute_asset_sha256(asset.local_path)
        if not sha or sha in seen_hashes:
            continue
        seen_hashes.add(sha)
        entry = {
            "asset": asset,
            "path": asset.local_path,
            "sha256": sha,
            "canonical_id": getattr(asset, "asset_id", None) or getattr(asset, "id", None) or sha[:16],
        }
        is_anim = getattr(asset, "asset_type", "") == "existing_animation" or asset.local_path.lower().endswith(
            (".gif", ".webm", ".mp4")
        )
        if is_anim:
            unique_animations.append(entry)
        else:
            unique_photos.append(entry)

    # Pre-flatten the 20 timing subdivisions across the 5 script sections
    sub_count = 4
    flat_beats: List[Dict[str, Any]] = []
    full_script_parts: List[str] = []
    g_idx = 0
    arc_names = cat_spec.narrative_arc or ["hook", "context", "mechanism", "revelation"]
    for sec_idx, sec in enumerate(timings):
        sec_start = float(sec["start_sec"])
        sec_end = float(sec["end_sec"])
        sec_dur = max(1.0, sec_end - sec_start)
        sec_text = (sec.get("text") or "").strip()
        full_script_parts.append(sec_text)
        words = sec_text.split()
        chunk_len = max(2, len(words) // sub_count)
        narrative_purpose = sec.get("arc_phase") or arc_names[min(sec_idx, len(arc_names) - 1)].upper()
        for sub_i in range(sub_count):
            beat_start = round(sec_start + (sub_i * sec_dur / sub_count), 3)
            beat_end = round(sec_start + ((sub_i + 1) * sec_dur / sub_count), 3)
            beat_dur = round(max(1.2, beat_end - beat_start), 3)
            w_slice = words[sub_i * chunk_len:(sub_i + 1) * chunk_len] or words
            seg_text = " ".join(w_slice)
            flat_beats.append({
                "idx": g_idx,
                "sec_idx": sec_idx,
                "stage": sec["stage"],
                "narrative_purpose": narrative_purpose,
                "sec_text": sec_text,
                "seg_text": seg_text,
                "beat_start": beat_start,
                "beat_end": beat_end,
                "beat_dur": beat_dur,
                "callout": _extract_short_callout(seg_text, max_words=5),
                "stat_badge": _extract_stat_highlight(sec_text),
            })
            g_idx += 1

    actual_total_beats = len(flat_beats)
    combined_script = script_full_text or " ".join(full_script_parts)

    # Plan slots following Priority A -> Priority B -> Priority C -> Priority D
    # Use at most 1 static slot per unique photo initially so every photo retains reuse budget for Priority C composites
    photo_target = min(len(unique_photos), 8) if unique_photos else 0
    photo_slot_set = set(_spread_photo_indices(actual_total_beats, photo_target))

    open_slots_after_a = [i for i in range(actual_total_beats) if i not in photo_slot_set]
    anim_b_target = min(len(unique_animations), len(open_slots_after_a), 4)
    anim_b_step = max(1, len(open_slots_after_a) // max(1, anim_b_target)) if anim_b_target > 0 else 1
    anim_b_slots = list(open_slots_after_a[::anim_b_step][:anim_b_target]) if anim_b_target > 0 else []
    anim_b_slot_set = set(anim_b_slots)

    open_slots_after_b = [i for i in open_slots_after_a if i not in anim_b_slot_set]
    # Priority C (animated overlays composited onto authentic acquired images within max reuse budget)
    remaining_photo_budget = max(0, len(unique_photos) * effective_max_reuse - len(photo_slot_set))
    priority_c_target = min(remaining_photo_budget, len(open_slots_after_b), 4 if len(unique_photos) >= 3 else 2)
    priority_c_step = max(1, len(open_slots_after_b) // max(1, priority_c_target)) if priority_c_target > 0 else 1
    priority_c_slot_set = set(open_slots_after_b[::priority_c_step][:priority_c_target]) if priority_c_target > 0 else set()

    remaining_for_d = [i for i in open_slots_after_b if i not in priority_c_slot_set]
    # Selective Priority D generated illustrations: use only sparingly when authentic assets aren't saturated
    gen_cap = 3 if len(unique_photos) >= 5 else 5
    gen_target = min(len(remaining_for_d), max(2, min(gen_cap, len(remaining_for_d) // 2)))
    gen_step = max(1, len(remaining_for_d) // max(1, gen_target)) if gen_target > 0 else 1
    gen_slot_set = set(remaining_for_d[::gen_step][:gen_target]) if gen_target > 0 else set()

    photo_use_counts: Dict[str, int] = {item["sha256"]: 0 for item in unique_photos}
    last_photo_sha: Optional[str] = None
    anim_type_counts: Dict[str, int] = {}

    accents = [
        cat_spec.accent_rgb,
        (56, 189, 248),
        (245, 194, 66),
        (34, 211, 153),
        (168, 139, 250),
    ]
    motions = [
        (1.00, 1.04, 0.008, -0.006),
        (1.04, 1.00, -0.008, 0.006),
        (1.00, 1.03, 0.010, 0.0),
        (1.03, 1.00, -0.010, 0.006),
    ]

    anim_engine = ExplainerAnimationEngine()
    coords = getattr(topic, "coordinates", None)

    scene_beats: List[SceneBeat] = []
    gate_beats: List[VisualBeatConfig] = []
    storyboard_beat_records: List[Dict[str, Any]] = []
    asset_reuse_tracker: Dict[str, int] = {}
    generated_count = 0
    animated_count = 0
    priority_a_beats = 0
    priority_b_beats = 0
    priority_c_beats = 0
    priority_d_bespoke_beats = 0
    b_cursor = 0

    for beat_info in flat_beats:
        idx = beat_info["idx"]
        beat_id = f"{topic.id}_b{idx + 1:02d}"
        sec_idx = beat_info["sec_idx"]
        stage_label = beat_info["stage"]
        narrative_purpose = beat_info["narrative_purpose"]
        seg_text = beat_info["seg_text"]
        callout = beat_info["callout"]
        stat_badge = beat_info["stat_badge"]
        beat_start = beat_info["beat_start"]
        beat_end = beat_info["beat_end"]
        beat_dur = beat_info["beat_dur"]
        accent = accents[sec_idx % len(accents)]
        z0, z1, pdx, pdy = motions[idx % len(motions)]

        slug = re.sub(r"[^a-z0-9]+", "_", callout.lower()).strip("_")[:24] or f"beat_{idx + 1}"
        plate_path = os.path.join(plates_dir, f"plate_{idx + 1:02d}_{stage_label.lower()}.png")
        search_kws = [w for w in re.findall(r"[A-Za-z0-9]{4,}", seg_text)][:4] or [topic.title.split()[0]]

        if idx in photo_slot_set and unique_photos:
            candidates = [item for item in unique_photos if photo_use_counts[item["sha256"]] < effective_max_reuse]
            non_consec = [c for c in candidates if c["sha256"] != last_photo_sha] or candidates
            chosen = min(non_consec, key=lambda c: photo_use_counts[c["sha256"]])
            photo_sha = chosen["sha256"]
            photo_use_counts[photo_sha] += 1
            last_photo_sha = photo_sha
            chosen_asset = chosen["asset"]

            img1 = chosen["path"]
            img2 = None
            layout_mode = "focal_photo"
            if len(unique_photos) >= 2 and idx % 4 == 3:
                other = next((x for x in unique_photos if x["sha256"] != photo_sha), None)
                if other:
                    img2 = other["path"]
                    layout_mode = "split_dual"

            _render_documentary_plate(
                out_path=plate_path,
                width=1080,
                height=1920,
                beat_idx=idx,
                total_beats=actual_total_beats,
                stage_label=stage_label,
                callout_text=callout,
                stat_text=stat_badge,
                topic_title=topic.title,
                accent_color=accent,
                layout_mode=layout_mode,
                primary_img_path=img1,
                secondary_img_path=img2,
            )

            visual_type = "documentary_image"
            canonical_path = img1
            canonical_hash = photo_sha
            canonical_id = chosen["canonical_id"]
            visual_desc = f"Authentic {cat_spec.display_name} visual depicting {topic.title}: {callout}"
            gen_instr = f"Display acquired image {os.path.basename(img1)} in {layout_mode} documentary frame"
            anim_mp4_path = None
            modality = VisualModality.REAL_PHOTO
            sourcing_priority = "A_authentic_existing"
            req_asset_type = "authentic_photo"
            anim_instr = f"Gentle Ken Burns motion ({z0:.2f}->{z1:.2f})"
            beat_license = chosen_asset.license_type.value
            beat_license_url = chosen_asset.license_url
            beat_creator = chosen_asset.creator
            beat_provider = chosen_asset.source_repository
            source_img_sha = photo_sha
            illustrative_flag = False
            asset_key = f"sha256:{canonical_hash}"
            asset_reuse_tracker[asset_key] = asset_reuse_tracker.get(asset_key, 0) + 1
            reuse_num = asset_reuse_tracker[asset_key]
            priority_a_beats += 1

        elif idx in anim_b_slot_set and b_cursor < len(unique_animations):
            last_photo_sha = None
            anim_item = unique_animations[b_cursor]
            b_cursor += 1
            anim_asset = anim_item["asset"]
            visual_type = "motion_graphic"
            anim_mp4_path = os.path.join(prod_ws.assets_animations_dir, f"anim_{beat_id}_priorityB.mp4")
            b_meta = anim_engine.adapt_existing_animation_to_portrait(
                input_anim_path=anim_item["path"],
                out_mp4_path=anim_mp4_path,
                duration_sec=beat_dur,
                beat_idx=idx,
                total_beats=actual_total_beats,
                stage_label=stage_label,
                topic_title=topic.title,
                segment_text=seg_text,
                accent_color=accent,
                source_credit=f"{anim_asset.source_repository} / {anim_asset.creator}",
                fps=30.0,
            )
            animated_count += 1
            priority_b_beats += 1
            prod_ws.register_artifact(
                "STAGE_7_ANIMATION",
                "animation",
                anim_mp4_path,
                asset_id=f"anim_{beat_id}",
                source_url=anim_asset.source_url,
                creator=anim_asset.creator,
                license=anim_asset.license_type.value,
                generation_provider=b_meta["generation_provider"],
                prompt_metadata=b_meta["prompt_metadata"],
                width=b_meta["width"],
                height=b_meta["height"],
                format="mp4",
            )
            _render_documentary_plate(
                out_path=plate_path,
                width=1080,
                height=1920,
                beat_idx=idx,
                total_beats=actual_total_beats,
                stage_label=stage_label,
                callout_text=callout,
                stat_text=stat_badge,
                topic_title=topic.title,
                accent_color=accent,
                layout_mode="stat_card",
                primary_img_path=None,
                secondary_img_path=None,
            )
            canonical_path = anim_mp4_path
            canonical_hash = compute_asset_sha256(anim_mp4_path)
            canonical_id = f"anim_{beat_id}"
            visual_desc = f"Open-source archival animation for '{topic.title}': {seg_text[:90]}"
            gen_instr = b_meta["prompt_metadata"]["prompt"]
            modality = VisualModality.MAP_TIMELINE
            sourcing_priority = "B_open_source_animation"
            req_asset_type = "existing_animation"
            anim_instr = "Open-source GIF/WebM loop framed at 30 FPS with documentary stage card"
            beat_license = anim_asset.license_type.value
            beat_license_url = anim_asset.license_url
            beat_creator = anim_asset.creator
            beat_provider = anim_asset.source_repository
            source_img_sha = anim_item["sha256"]
            illustrative_flag = False
            asset_key = f"sha256:{canonical_hash}"
            asset_reuse_tracker[asset_key] = asset_reuse_tracker.get(asset_key, 0) + 1
            reuse_num = asset_reuse_tracker[asset_key]

        elif idx in priority_c_slot_set and any(photo_use_counts[p["sha256"]] < effective_max_reuse for p in unique_photos):
            eligible = [p for p in unique_photos if photo_use_counts[p["sha256"]] < effective_max_reuse]
            non_consec = [p for p in eligible if p["sha256"] != last_photo_sha] or eligible
            chosen = min(non_consec, key=lambda c: photo_use_counts[c["sha256"]])
            photo_sha = chosen["sha256"]
            photo_use_counts[photo_sha] += 1
            last_photo_sha = photo_sha
            chosen_asset = chosen["asset"]

            visual_type = _select_explanatory_visual_type(
                idx, stage_label, seg_text, combined_script, bool(coords), anim_type_counts
            )
            anim_type_counts[visual_type] = anim_type_counts.get(visual_type, 0) + 1
            anim_mp4_path = os.path.join(prod_ws.assets_animations_dir, f"anim_{beat_id}_pC_{visual_type}.mp4")
            c_meta = anim_engine.render_explainer_animation_mp4(
                visual_type=visual_type,
                out_mp4_path=anim_mp4_path,
                duration_sec=beat_dur,
                beat_idx=idx,
                total_beats=actual_total_beats,
                stage_label=stage_label,
                topic_title=topic.title,
                segment_text=seg_text,
                full_script=combined_script,
                accent_color=accent,
                coordinates=coords,
                fps=30.0,
                background_image_path=chosen["path"],
            )
            animated_count += 1
            priority_c_beats += 1
            prod_ws.register_artifact(
                "STAGE_7_ANIMATION",
                "animation",
                anim_mp4_path,
                asset_id=f"anim_{beat_id}",
                source_url=chosen_asset.source_url,
                creator=f"{chosen_asset.creator} + CrazzyDocs Overlay",
                license=chosen_asset.license_type.value,
                generation_provider=c_meta["generation_provider"],
                prompt_metadata=c_meta["prompt_metadata"],
                width=c_meta["width"],
                height=c_meta["height"],
                format="mp4",
            )
            _render_documentary_plate(
                out_path=plate_path,
                width=1080,
                height=1920,
                beat_idx=idx,
                total_beats=actual_total_beats,
                stage_label=stage_label,
                callout_text=callout,
                stat_text=stat_badge,
                topic_title=topic.title,
                accent_color=accent,
                layout_mode="focal_photo",
                primary_img_path=chosen["path"],
                secondary_img_path=None,
            )
            canonical_path = anim_mp4_path
            canonical_hash = compute_asset_sha256(anim_mp4_path)
            canonical_id = f"anim_{beat_id}"
            visual_desc = f"Priority C animated {visual_type} composited over authentic visual for '{topic.title}': {seg_text[:80]}"
            gen_instr = c_meta["prompt_metadata"]["prompt"]
            modality = VisualModality.MAP_TIMELINE
            sourcing_priority = "C_animated_authentic"
            req_asset_type = "animated_authentic"
            anim_instr = f"Animated {visual_type} markers, highlight brackets, and callout overlay over {os.path.basename(chosen['path'])}"
            beat_license = chosen_asset.license_type.value
            beat_license_url = chosen_asset.license_url
            beat_creator = chosen_asset.creator
            beat_provider = f"{chosen_asset.source_repository} + Overlay"
            source_img_sha = photo_sha
            illustrative_flag = False
            asset_key = f"sha256:{canonical_hash}"
            asset_reuse_tracker[asset_key] = asset_reuse_tracker.get(asset_key, 0) + 1
            reuse_num = asset_reuse_tracker[asset_key]

        elif idx in gen_slot_set:
            last_photo_sha = None
            gen_path = os.path.join(prod_ws.assets_generated_dir, f"gen_{beat_id}_{slug}.png")
            gen_meta = anim_engine.generate_topic_illustration_png(
                out_path=gen_path,
                beat_idx=idx,
                total_beats=actual_total_beats,
                stage_label=stage_label,
                visual_type_label="generated_image",
                topic_title=topic.title,
                segment_text=seg_text,
                full_script=combined_script,
                accent_color=accent,
                coordinates=coords,
                variant=generated_count,
                category=cat_spec.display_name,
                illustrative_label="ILLUSTRATIVE RECONSTRUCTION",
            )
            generated_count += 1
            prod_ws.register_artifact(
                "STAGE_6_VISUAL_ASSETS",
                "generated_asset",
                gen_path,
                asset_id=f"gen_{beat_id}",
                source_url="local://generated",
                creator="CrazzyDocs Analytical Illustration Generator",
                license="CC0 / Original Procedural Work",
                generation_provider=gen_meta["generation_provider"],
                prompt_metadata=gen_meta["prompt_metadata"],
                width=gen_meta["width"],
                height=gen_meta["height"],
                format="png",
            )
            plate_path = gen_path
            visual_type = "generated_image"
            canonical_path = gen_path
            canonical_hash = compute_asset_sha256(gen_path)
            canonical_id = f"gen_{beat_id}"
            visual_desc = f"Selective illustrative schematic reconstruction for '{topic.title}': {seg_text[:90]}"
            gen_instr = gen_meta["prompt_metadata"]["prompt"]
            anim_mp4_path = None
            modality = VisualModality.CINEMATIC_STILL
            sourcing_priority = "D_selective_generated"
            req_asset_type = "illustrative_reconstruction"
            anim_instr = "Static conceptual schematic labeled ILLUSTRATIVE RECONSTRUCTION"
            beat_license = "CC0 / Original Work"
            beat_license_url = "local://generated"
            beat_creator = "CrazzyDocs Analytical Illustration Generator"
            beat_provider = "Local Conceptual Synthesizer"
            source_img_sha = None
            illustrative_flag = True
            asset_key = f"sha256:{canonical_hash}"
            asset_reuse_tracker[asset_key] = asset_reuse_tracker.get(asset_key, 0) + 1
            reuse_num = asset_reuse_tracker[asset_key]

            if progress_cb:
                progress_cb({
                    "phase": "image_generation",
                    "generated_images": generated_count,
                    "beat_index": idx + 1,
                    "total_beats": actual_total_beats,
                    "asset_path": gen_path,
                })

        else:
            last_photo_sha = None
            visual_type = _select_explanatory_visual_type(
                idx, stage_label, seg_text, combined_script, bool(coords), anim_type_counts
            )
            anim_type_counts[visual_type] = anim_type_counts.get(visual_type, 0) + 1
            anim_mp4_path = os.path.join(prod_ws.assets_animations_dir, f"anim_{beat_id}_{visual_type}.mp4")

            anim_meta = anim_engine.render_explainer_animation_mp4(
                visual_type=visual_type,
                out_mp4_path=anim_mp4_path,
                duration_sec=beat_dur,
                beat_idx=idx,
                total_beats=actual_total_beats,
                stage_label=stage_label,
                topic_title=topic.title,
                segment_text=seg_text,
                full_script=combined_script,
                accent_color=accent,
                coordinates=coords,
                fps=30.0,
            )
            animated_count += 1
            priority_d_bespoke_beats += 1
            prod_ws.register_artifact(
                "STAGE_7_ANIMATION",
                "animation",
                anim_mp4_path,
                asset_id=f"anim_{beat_id}",
                source_url="local://animation",
                creator="CrazzyDocs Explainer Animation Engine",
                license="CC0 / Original Procedural Animation",
                generation_provider=anim_meta["generation_provider"],
                prompt_metadata=anim_meta["prompt_metadata"],
                width=anim_meta["width"],
                height=anim_meta["height"],
                format="mp4",
            )
            _render_documentary_plate(
                out_path=plate_path,
                width=1080,
                height=1920,
                beat_idx=idx,
                total_beats=actual_total_beats,
                stage_label=stage_label,
                callout_text=callout,
                stat_text=stat_badge,
                topic_title=topic.title,
                accent_color=accent,
                layout_mode="stat_card",
                primary_img_path=None,
                secondary_img_path=None,
            )
            canonical_path = anim_mp4_path
            canonical_hash = compute_asset_sha256(anim_mp4_path)
            canonical_id = f"anim_{beat_id}"
            visual_desc = f"Script-grounded {visual_type} animation for '{topic.title}': {seg_text[:90]}"
            gen_instr = anim_meta["prompt_metadata"]["prompt"]
            modality = VisualModality.MAP_TIMELINE
            sourcing_priority = "D_procedural_animation"
            req_asset_type = visual_type
            anim_instr = f"Procedural 30 FPS {visual_type} driven by script facts"
            beat_license = "CC0 / Original Work"
            beat_license_url = "local://animation"
            beat_creator = "CrazzyDocs Explainer Animation Engine"
            beat_provider = "Local Explainer Engine"
            source_img_sha = None
            illustrative_flag = False
            asset_key = f"sha256:{canonical_hash}"
            asset_reuse_tracker[asset_key] = asset_reuse_tracker.get(asset_key, 0) + 1
            reuse_num = asset_reuse_tracker[asset_key]

            if progress_cb:
                progress_cb({
                    "phase": "animation_generation",
                    "animations_generated": animated_count,
                    "beat_index": idx + 1,
                    "total_beats": actual_total_beats,
                    "visual_type": visual_type,
                    "asset_path": anim_mp4_path,
                })

        beat_params: Dict[str, Any] = {
            "image_path": plate_path,
            "visual_type": visual_type,
            "sourcing_priority": sourcing_priority,
            "asset_path": canonical_path,
            "asset_hash": canonical_hash,
            "asset_id": canonical_id,
            "zoom_start": z0,
            "zoom_end": z1,
            "pan_dx": pdx,
            "pan_dy": pdy,
        }
        if anim_mp4_path:
            beat_params["animation_path"] = anim_mp4_path

        scene_beats.append(
            SceneBeat(
                id=beat_id,
                modality=modality,
                duration=beat_dur,
                narration_text=seg_text,
                start_time=beat_start,
                transition_in=TransitionType.CUT,
                params=beat_params,
            )
        )
        gate_beats.append(
            VisualBeatConfig(
                id=beat_id,
                image_filename=os.path.basename(canonical_path),
                start_sec=beat_start,
                end_sec=beat_end,
                top_tag=cat_spec.category_code[:16].upper(),
                editorial_text=callout,
                accent_color=accent,
                motion_type="zoom_in" if z1 > z0 else "zoom_out",
                sub_label=stage_label,
            )
        )
        storyboard_beat_records.append({
            "beat_id": beat_id,
            "id": beat_id,
            "stage": stage_label,
            "narrative_purpose": narrative_purpose,
            "start_sec": beat_start,
            "end_sec": beat_end,
            "duration_sec": beat_dur,
            "script_segment": seg_text,
            "callout": callout,
            "visual_type": visual_type,
            "required_asset_type": req_asset_type,
            "search_keywords": search_kws,
            "preferred_acquisition_method": sourcing_priority,
            "sourcing_priority": sourcing_priority,
            "license": beat_license,
            "license_url": beat_license_url,
            "creator": beat_creator,
            "source_provider": beat_provider,
            "source_image_sha256": source_img_sha,
            "illustrative_reconstruction": illustrative_flag,
            "animation_instructions": anim_instr,
            "visual_description": visual_desc,
            "asset_path": canonical_path,
            "asset_hash": canonical_hash,
            "canonical_asset_id": canonical_id,
            "plate_path": plate_path,
            "animation_path": anim_mp4_path,
            "visual_generation_instructions": gen_instr,
            "transition": "cut",
            "relevance_score": 0.92 if anim_mp4_path or visual_type == "generated_image" else 0.89,
            "reuse_count": reuse_num,
            "source_image": os.path.basename(canonical_path),
        })

    # Fail-closed Stage 3.21J visual diversity + base-photo reuse verification
    if photo_use_counts and max(photo_use_counts.values()) > effective_max_reuse:
        offending = {k[:12]: v for k, v in photo_use_counts.items() if v > effective_max_reuse}
        raise ValueError(f"Base authentic image reuse limit ({effective_max_reuse}) exceeded across Priority A+C: {offending}")

    audit_res = VisualDiversityValidator.validate_storyboard(
        storyboard_beat_records,
        topic_title=topic.title,
        max_image_reuse=effective_max_reuse,
        check_files_on_disk=True,
        min_distinct_assets=MIN_DISTINCT_ASSETS_FULL_SHORT if actual_total_beats >= 18 else None,
        workspace_root=prod_ws.prod_dir,
    )
    if not audit_res.is_valid:
        raise ValueError(
            "Stage 3.21J VisualDiversityValidator rejected storyboard: "
            + " | ".join(audit_res.violations)
        )

    ass_path = _write_aligned_ass_subtitles(
        timings=timings,
        out_ass_path=os.path.join(prod_ws.scripts_dir, f"subtitles_{prod_ws.production_id}.ass"),
        max_end_sec=total_voice_dur,
    )

    music_path = DEFAULT_MUSIC_PATH if os.path.isfile(DEFAULT_MUSIC_PATH) else None
    outro_media = os.path.join(WORKSPACE_ROOT, "media", "source", "branding", "crazzydocs_outro_1080x1920.mp4")
    outro_assets = os.path.join(WORKSPACE_ROOT, "assets", "branding", "crazzydocs", "crazzydocs_outro_1080x1920.mp4")
    outro_path = outro_media if os.path.isfile(outro_media) else outro_assets

    sourcing_summary = {
        "authentic_images_acquired": len(unique_photos),
        "existing_animations_acquired": len(unique_animations),
        "priority_a_photo_beats": priority_a_beats,
        "priority_b_existing_anim_beats": priority_b_beats,
        "priority_c_animated_authentic_beats": priority_c_beats,
        "priority_d_generated_image_beats": generated_count,
        "priority_d_bespoke_anim_beats": priority_d_bespoke_beats,
        "max_source_photo_reuse": max(photo_use_counts.values()) if photo_use_counts else 0,
    }

    storyboard_payload = {
        "schema_version": "3.21M",
        "topic_id": topic.id,
        "topic_title": topic.title,
        "category": cat_spec.display_name,
        "narrative_arc": arc_names,
        "beat_count": len(scene_beats),
        "narration_duration_sec": total_voice_dur,
        "outro_path": outro_path,
        "visual_sourcing_summary": sourcing_summary,
        "visual_diversity_audit": {
            "is_valid": audit_res.is_valid,
            "distinct_assets": audit_res.distinct_assets,
            "unique_images": audit_res.unique_images,
            "unique_animations": audit_res.unique_animations,
            "max_reuse_observed": audit_res.max_reuse_observed,
            "reuse_counts": audit_res.reuse_counts,
        },
        "beats": storyboard_beat_records,
    }

    plan = StoryboardPlan(
        title=topic.title,
        total_duration=total_voice_dur,
        scenes=scene_beats,
        voiceover_path=voiceover_path,
        music_track=music_path,
        ducking_db=-14.0,
        subtitles_ass_path=ass_path,
        metadata={
            "topic_id": topic.id,
            "category": cat_spec.display_name,
            "beat_count": len(scene_beats),
            "distinct_visual_assets": audit_res.distinct_assets,
            "unique_images": audit_res.unique_images,
            "unique_animations": audit_res.unique_animations,
            "max_image_reuse_observed": audit_res.max_reuse_observed,
            "visual_sourcing_summary": sourcing_summary,
            "storyboard_beats": storyboard_beat_records,
        },
        include_crazzydocs_outro=True,
        crazzydocs_outro_path=outro_path,
    )

    timeline_json = os.path.join(prod_ws.visual_timeline_dir, f"storyboard_{prod_ws.production_id}.json")
    canonical_sb_json = os.path.join(prod_ws.storyboards_dir, f"storyboard_{prod_ws.production_id}.json")
    for dest_json in (timeline_json, canonical_sb_json):
        with open(dest_json, "w", encoding="utf-8") as f:
            json.dump(storyboard_payload, f, indent=2)

    prod_ws.register_artifact(
        "STAGE_5_TIMELINE",
        "storyboard",
        canonical_sb_json,
        asset_id=f"storyboard_{prod_ws.production_id}",
        format="json",
    )

    return plan, gate_beats, ass_path
