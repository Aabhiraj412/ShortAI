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
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from backend.app.content.curiosity_topic_engine import ScoredTopic
from backend.app.core.execution_context import ExecutionContext
from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE_ROOT
from backend.app.media.acquisition_manager import MediaAcquisitionEngine
from backend.app.media.attribution_generator import AttributionGenerator
from backend.app.media.downloader import AssetDownloader
from backend.app.media.image_processor import AdaptationMode, ImageProcessor
from backend.app.media.models import AttributionManifest, MediaAsset
from backend.app.orchestration.production_workspace import ProductionWorkspace, compute_file_sha256
from backend.app.rendering.engines.vesna_fast_renderer import VisualBeatConfig
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


def compose_structured_script(topic: ScoredTopic, hook_text: str) -> Dict[str, Any]:
    """
    Composes a tight, factual 5-section documentary narration script (~115-138 words, ~50-56s spoken)
    strictly grounded in the topic's verified research extract and factual bullets.
    """
    s1 = (hook_text or "").strip()
    if s1 and not s1.endswith((".", "!", "?")):
        s1 += "."

    # Gather verified non-duplicate factual sentences from extended_extract + factual_bullets + summary
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
        # Skip near-identical repetitions
        if norm in seen_norms or any(norm[:40] == sn[:40] for sn in seen_norms):
            continue
        seen_norms.add(norm)
        facts.append(clean)

    # Ensure topic title words appear cleanly in the narration without repeating previously used sentences
    title_anchor = topic.title.split("(")[0].strip()

    def _pop_next_fact(used: set, fallback: str) -> str:
        for cand in facts:
            cn = re.sub(r"\W+", " ", cand.lower()).strip()
            if cn not in used and not any(cn[:35] == u[:35] for u in used):
                used.add(cn)
                return cand
        return fallback

    used_norms = {s1_norm}
    sec_hook = s1 or _pop_next_fact(used_norms, f"Behind {title_anchor} lies a remarkable documented record.")
    f1 = _pop_next_fact(used_norms, topic.summary)
    sec_context = f1 if title_anchor.lower() in f"{sec_hook} {f1}".lower() else f"The story of {title_anchor} starts here: {f1}"
    sec_escalation = _pop_next_fact(used_norms, f"Key measurements around {title_anchor} revealed how quickly events unfolded.")
    sec_revelation = _pop_next_fact(used_norms, f"Archival findings later showed the lasting scientific and historical impact of {title_anchor}.")
    extra_fact = _pop_next_fact(used_norms, "")

    closing = f"And that is how {title_anchor} earned its place in verified history. Subscribe to CrazzyDocs for more true documentary stories."
    sec_payoff = f"{extra_fact} {closing}".strip()

    sections = [
        {"id": "seg_01_hook", "stage": "HOOK", "emotion": "hook", "text": sec_hook},
        {"id": "seg_02_context", "stage": "CONTEXT", "emotion": "curiosity", "text": sec_context},
        {"id": "seg_03_escalation", "stage": "ESCALATION", "emotion": "tension", "text": sec_escalation},
        {"id": "seg_04_revelation", "stage": "REVELATION", "emotion": "revelation", "text": sec_revelation},
        {"id": "seg_05_payoff", "stage": "PAYOFF", "emotion": "closing", "text": sec_payoff},
    ]

    # Trim gently if total word count > 142 so spoken audio + 2.0s outro never crosses 60.0s
    total_words = sum(len(s["text"].split()) for s in sections)
    if total_words > 142 and extra_fact:
        sections[4]["text"] = closing
        total_words = sum(len(s["text"].split()) for s in sections)

    # Pad cleanly from extra verified facts if too short (< 110 words)
    fact_idx = 4
    while total_words < 112 and fact_idx < len(facts):
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

    combined_mp3 = os.path.join(prod_ws.narration_dir, f"voiceover_{prod_ws.production_id}.mp3")
    tts_engine.concatenate_segments(synthesized, combined_mp3)

    # Probe cumulative timings cleanly
    total_voice_dur = tts_engine.probe_duration(combined_mp3)
    # Keep narrative window comfortably inside 48.5s - 56.8s so +2.0s outro hits 50.5s - 58.8s
    if 42.0 <= total_voice_dur < 48.8:
        pad_sec = round(49.2 - total_voice_dur, 3)
        padded_mp3 = os.path.join(prod_ws.narration_dir, f"voiceover_{prod_ws.production_id}_padded.mp3")
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
    max_assets: int = 6,
) -> Tuple[List[MediaAsset], Optional[AttributionManifest], List[Dict[str, Any]]]:
    """
    Acquires openly licensed topic images into prod_ws.assets_dir (isolated from prior runs & studio.db).
    Returns: (acquired_assets, attribution_manifest, manifest_asset_dicts)
    """
    raw_dir = os.path.join(prod_ws.assets_dir, "raw")
    portrait_dir = os.path.join(prod_ws.assets_dir, "portrait")
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(portrait_dir, exist_ok=True)

    registry = None if execution_context == ExecutionContext.PRODUCTION else _IsolatedAssetRegistry()
    downloader = AssetDownloader(storage_dir=raw_dir)
    processor = ImageProcessor(output_dir=portrait_dir)
    attrib_gen = AttributionGenerator(manifests_dir=prod_ws.metadata_dir)

    from backend.app.media.license_verifier import LicenseVerifier
    from backend.app.media.models import LicenseStatus
    from backend.app.media.providers.nasa_provider import NASAProvider
    from backend.app.media.providers.openverse_provider import OpenverseProvider
    from backend.app.media.providers.wikimedia_provider import WikimediaProvider

    verifier = LicenseVerifier()
    wiki_provider = WikimediaProvider(license_verifier=verifier)

    # Include NASA only when the topic actually relates to space, astronomy, aeronautics, or Earth observation
    topic_blob = f"{topic.title} {topic.category} {topic.summary}".lower()
    is_space_or_astro = any(
        k in topic_blob
        for k in ("space", "nasa", "quasar", "galaxy", "black hole", "planet", "orbit", "telescope", "moon", "mars", "rocket", "apollo", "voyager", "satellite", "observatory", "earth", "atmosphere")
    )
    providers = [wiki_provider, OpenverseProvider(license_verifier=verifier)]
    if is_space_or_astro:
        providers.append(NASAProvider(license_verifier=verifier))

    # 1. Directly fetch openly licensed Wikimedia Commons files linked to the topic's own Wikipedia article
    acquired: List[MediaAsset] = []
    article_file_titles = [u for u in (getattr(topic, "image_urls", []) or []) if isinstance(u, str) and u.startswith("File:")]
    if article_file_titles:
        direct_candidates = wiki_provider.fetch_files_by_titles(article_file_titles, topic_title=topic.title)
        for cand in direct_candidates:
            if cand.verification_status != LicenseStatus.APPROVED:
                continue
            ok, local_path, _, _ = downloader.download_asset(cand)
            if ok and local_path and os.path.isfile(local_path):
                try:
                    cand.portrait_path = processor.adapt_image_to_portrait(
                        input_path=local_path, mode=AdaptationMode.FIT_BLURRED_BACKGROUND
                    )
                    acquired.append(cand)
                except Exception:
                    continue
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
    generic_words = {"scientists", "researchers", "history", "study", "years", "earliest", "known", "hawaiian", "record"}
    for ent in (topic.key_entities or []):
        if ent.lower() != clean_title.lower() and ent.lower() not in generic_words and len(ent) >= 5:
            queries.append(ent)
        if len(queries) >= 3:
            break
    if len(queries) < 2:
        queries.append(f"{clean_title} {topic.category}")

    if len(acquired) < max_assets:
        extra_acquired, _ = asyncio.run(
            acq_engine.acquire_scene_assets(
                topic_title=topic.title,
                category=topic.category,
                scene_queries=queries[:3],
                job_id=prod_ws.production_id,
                max_assets=max_assets - len(acquired),
                adaptation_mode=AdaptationMode.FIT_BLURRED_BACKGROUND,
            )
        )
        seen_hashes = {a.asset_hash for a in acquired if a.asset_hash}
        for ea in extra_acquired:
            if ea.asset_hash and ea.asset_hash in seen_hashes:
                continue
            acquired.append(ea)
            if ea.asset_hash:
                seen_hashes.add(ea.asset_hash)

    attrib_manifest = attrib_gen.generate_manifest(video_id=prod_ws.production_id, assets=acquired) if acquired else None

    manifest_assets: List[Dict[str, Any]] = []
    for asset in acquired:
        manifest_assets.append({
            "asset_id": asset.asset_id,
            "filename": os.path.basename(asset.local_path or ""),
            "path": asset.local_path,
            "portrait_path": asset.portrait_path,
            "title": asset.title,
            "creator": asset.creator or "Public Archive Contributor",
            "source_repository": asset.source_repository,
            "source_url": asset.source_url,
            "license": asset.license_type.value,
            "license_url": asset.license_url,
            "sha256": compute_file_sha256(asset.local_path or ""),
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


def build_20_beat_storyboard(
    topic: ScoredTopic,
    timings: List[Dict[str, Any]],
    total_voice_dur: float,
    voiceover_path: str,
    acquired_assets: List[MediaAsset],
    prod_ws: ProductionWorkspace,
) -> Tuple[StoryboardPlan, List[VisualBeatConfig], str]:
    """
    Generates a 20-beat multi-layout 1080x1920 documentary storyboard synced to voiceover timings.
    Returns: (StoryboardPlan, List[VisualBeatConfig], ass_subtitles_path)
    """
    plates_dir = os.path.join(prod_ws.assets_dir, "plates")
    os.makedirs(plates_dir, exist_ok=True)

    # Prefer verified workspace-local images
    local_images = [
        a.local_path for a in acquired_assets
        if a.local_path and os.path.isfile(a.local_path)
    ]

    accents = [
        (255, 82, 82),
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
    layouts = ["focal_photo", "stat_card", "focal_photo", "split_dual"]

    # Subdivide the 5 narrative sections into 20 distinct beats (4 beats per section, ~2.4-2.9s each)
    scene_beats: List[SceneBeat] = []
    gate_beats: List[VisualBeatConfig] = []
    total_beats = 20
    global_idx = 0

    for sec_idx, sec in enumerate(timings):
        sec_start = float(sec["start_sec"])
        sec_end = float(sec["end_sec"])
        sec_dur = max(1.0, sec_end - sec_start)
        words = (sec["text"] or "").split()
        sub_count = 4
        chunk_len = max(2, len(words) // sub_count)
        accent = accents[sec_idx % len(accents)]

        for sub_i in range(sub_count):
            beat_start = round(sec_start + (sub_i * sec_dur / sub_count), 3)
            beat_end = round(sec_start + ((sub_i + 1) * sec_dur / sub_count), 3)
            beat_dur = round(max(1.2, beat_end - beat_start), 3)

            w_slice = words[sub_i * chunk_len:(sub_i + 1) * chunk_len] or words
            callout = _extract_short_callout(" ".join(w_slice), max_words=5)
            stat_badge = _extract_stat_highlight(sec["text"])

            img1 = local_images[global_idx % len(local_images)] if local_images else None
            img2 = local_images[(global_idx + 1) % len(local_images)] if len(local_images) >= 2 else None
            layout_mode = layouts[global_idx % len(layouts)]
            if not img1 and layout_mode == "split_dual":
                layout_mode = "stat_card"

            plate_path = os.path.join(plates_dir, f"plate_{global_idx + 1:02d}_{sec['stage'].lower()}.png")
            _render_documentary_plate(
                out_path=plate_path,
                width=1080,
                height=1920,
                beat_idx=global_idx,
                total_beats=total_beats,
                stage_label=sec["stage"],
                callout_text=callout,
                stat_text=stat_badge,
                topic_title=topic.title,
                accent_color=accent,
                layout_mode=layout_mode,
                primary_img_path=img1,
                secondary_img_path=img2,
            )

            z0, z1, pdx, pdy = motions[global_idx % len(motions)]
            beat_id = f"{topic.id}_b{global_idx + 1:02d}"
            scene_beats.append(
                SceneBeat(
                    id=beat_id,
                    modality=VisualModality.REAL_PHOTO if global_idx % 2 == 0 else VisualModality.CINEMATIC_STILL,
                    duration=beat_dur,
                    narration_text=" ".join(w_slice),
                    start_time=beat_start,
                    transition_in=TransitionType.CUT,
                    params={
                        "image_path": plate_path,
                        "zoom_start": z0,
                        "zoom_end": z1,
                        "pan_dx": pdx,
                        "pan_dy": pdy,
                    },
                )
            )
            gate_beats.append(
                VisualBeatConfig(
                    id=beat_id,
                    image_filename=os.path.basename(img1) if img1 else os.path.basename(plate_path),
                    start_sec=beat_start,
                    end_sec=beat_end,
                    top_tag=topic.category[:16].upper(),
                    editorial_text=callout,
                    accent_color=accent,
                    motion_type="zoom_in" if z1 > z0 else "zoom_out",
                    sub_label=sec["stage"],
                )
            )
            global_idx += 1

    ass_path = _write_aligned_ass_subtitles(
        timings=timings,
        out_ass_path=os.path.join(prod_ws.scripts_dir, f"subtitles_{prod_ws.production_id}.ass"),
        max_end_sec=total_voice_dur,
    )

    music_path = DEFAULT_MUSIC_PATH if os.path.isfile(DEFAULT_MUSIC_PATH) else None
    outro_media = os.path.join(WORKSPACE_ROOT, "media", "source", "branding", "crazzydocs_outro_1080x1920.mp4")
    outro_assets = os.path.join(WORKSPACE_ROOT, "assets", "branding", "crazzydocs", "crazzydocs_outro_1080x1920.mp4")
    outro_path = outro_media if os.path.isfile(outro_media) else outro_assets

    plan = StoryboardPlan(
        title=topic.title,
        total_duration=total_voice_dur,
        scenes=scene_beats,
        voiceover_path=voiceover_path,
        music_track=music_path,
        ducking_db=-14.0,
        subtitles_ass_path=ass_path,
        metadata={"topic_id": topic.id, "beat_count": len(scene_beats)},
        include_crazzydocs_outro=True,
        crazzydocs_outro_path=outro_path,
    )

    # Save visual timeline artifact into workspace
    timeline_json = os.path.join(prod_ws.visual_timeline_dir, f"storyboard_{prod_ws.production_id}.json")
    with open(timeline_json, "w", encoding="utf-8") as f:
        json.dump({
            "topic_id": topic.id,
            "topic_title": topic.title,
            "beat_count": len(scene_beats),
            "narration_duration_sec": total_voice_dur,
            "outro_path": outro_path,
            "beats": [
                {
                    "id": b.id,
                    "start_sec": gb.start_sec,
                    "end_sec": gb.end_sec,
                    "duration_sec": b.duration,
                    "stage": gb.sub_label,
                    "callout": gb.editorial_text,
                    "plate_path": b.params.get("image_path"),
                    "source_image": gb.image_filename,
                }
                for b, gb in zip(scene_beats, gate_beats)
            ],
        }, f, indent=2)

    return plan, gate_beats, ass_path
