"""
Lemo-Opuscar Film Builder for CrazzyDocs.

Generates a deterministic, self-contained HTML5/Canvas/DOM film package
(`index.html` + local `assets/` + `film_manifest.json`) compatible with
`tools/dependencies/lemo-opuscar/core/render/video.mjs` (`window.DUR` +
`window.render(t)`) and `still.mjs` (`?thumbnail=1`).
"""
from __future__ import annotations

import html
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.app.rendering.lemo_opuscar_adapter import PINNED_COMMIT


class LemoOpuscarFilmBuilder:
    """Builds a deterministic 1080x1920 Lemo-Opuscar film package from storyboard & assets."""

    @staticmethod
    def build_film_package(
        *,
        prod_dir: Path,
        job_id: str,
        topic: str,
        script_data: Dict[str, Any],
        storyboard: List[Dict[str, Any]],
        section_timings: Optional[Dict[str, Any]] = None,
        duration_sec: float = 45.0,
        category: str = "Documentary",
    ) -> Dict[str, Any]:
        """Creates `<prod_dir>/lemo_film/index.html` and returns provenance metadata."""
        film_dir = Path(prod_dir) / "lemo_film"
        assets_dir = film_dir / "assets"
        film_dir.mkdir(parents=True, exist_ok=True)
        assets_dir.mkdir(parents=True, exist_ok=True)

        total_dur = max(15.0, float(duration_sec or 45.0))
        beats_in = list(storyboard or [])
        if not beats_in:
            beats_in = [
                {
                    "beat_number": i + 1,
                    "visual_prompt": topic,
                    "spoken_text": script_data.get("hook") if i == 0 else "",
                    "section": "BODY",
                }
                for i in range(8)
            ]

        beat_count = len(beats_in)
        default_beat_dur = total_dur / max(1, beat_count)
        cursor_t = 0.0
        normalized_beats: List[Dict[str, Any]] = []
        copied_assets: Dict[str, str] = {}
        unique_asset_sources: List[str] = []

        for idx, raw_beat in enumerate(beats_in):
            b = dict(raw_beat or {})
            b_dur = float(b.get("duration") or b.get("duration_sec") or default_beat_dur)
            if idx == beat_count - 1:
                end_t = total_dur
            else:
                end_t = min(total_dur, cursor_t + max(1.2, b_dur))

            src_candidate = (
                b.get("image_path")
                or b.get("asset_path")
                or b.get("local_path")
                or b.get("media_path")
                or ""
            )
            rel_asset = ""
            if src_candidate:
                src_path = Path(str(src_candidate))
                if src_path.exists() and src_path.is_file():
                    key = str(src_path.resolve())
                    if key not in copied_assets:
                        ext = src_path.suffix.lower() or ".jpg"
                        dest_name = f"beat_{idx + 1:02d}_{src_path.stem[:24]}{ext}"
                        dest_name = "".join(
                            c if (c.isalnum() or c in "._-") else "_" for c in dest_name
                        )
                        dest_path = assets_dir / dest_name
                        if src_path.resolve() != dest_path.resolve():
                            shutil.copy2(src_path, dest_path)
                        copied_assets[key] = f"assets/{dest_name}"
                        unique_asset_sources.append(key)
                    rel_asset = copied_assets[key]

            caption_text = str(
                b.get("spoken_text")
                or b.get("narration")
                or b.get("subtitle")
                or b.get("caption")
                or ""
            ).strip()
            visual_cue = str(
                b.get("visual_prompt")
                or b.get("visual_description")
                or b.get("scene_title")
                or topic
            ).strip()
            section_name = str(b.get("section") or b.get("phase") or "STORY").upper()

            normalized_beats.append(
                {
                    "index": idx,
                    "beat_number": int(b.get("beat_number") or (idx + 1)),
                    "start": round(cursor_t, 3),
                    "end": round(end_t, 3),
                    "section": section_name,
                    "caption": caption_text,
                    "visual_cue": visual_cue[:140],
                    "asset": rel_asset,
                    "motion": ["zoom_in", "pan_right", "zoom_out", "pan_left"][idx % 4],
                }
            )
            cursor_t = end_t

        title_text = str(
            script_data.get("title")
            or script_data.get("topic")
            or topic
        ).strip()
        hook_text = str(script_data.get("hook") or "").strip()
        thumb_headline = str(
            script_data.get("thumbnail_headline")
            or (title_text.split(":")[0] if ":" in title_text else title_text)
        ).strip()[:64]

        payload = {
            "job_id": job_id,
            "topic": topic,
            "title": title_text,
            "hook": hook_text,
            "thumb_headline": thumb_headline,
            "category": category,
            "duration": round(total_dur, 3),
            "beats": normalized_beats,
            "section_timings": section_timings or {},
            "renderer": "lemo-opuscar",
            "pinned_commit": PINNED_COMMIT,
        }

        html_content = _render_film_html(payload)
        index_path = film_dir / "index.html"
        index_path.write_text(html_content, encoding="utf-8")

        manifest = {
            "renderer": "lemo-opuscar",
            "engine": f"Lemo-Opuscar ({PINNED_COMMIT[:10]})",
            "pinned_commit": PINNED_COMMIT,
            "job_id": job_id,
            "topic": topic,
            "category": category,
            "duration_sec": round(total_dur, 3),
            "beat_count": len(normalized_beats),
            "unique_asset_count": len(unique_asset_sources),
            "asset_paths": list(copied_assets.values()),
            "demo_dir": str(film_dir),
            "index_html": str(index_path),
        }
        (film_dir / "film_manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        return manifest


def _render_film_html(payload: Dict[str, Any]) -> str:
    data_json = json.dumps(payload, ensure_ascii=False)
    safe_title = html.escape(str(payload.get("title") or "CrazzyDocs"))
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=1080, height=1920, initial-scale=1.0" />
<title>{safe_title}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{
    width: 1080px;
    height: 1920px;
    overflow: hidden;
    background: #070b14;
    color: #f8fafc;
    font-family: "Segoe UI", "Inter", system-ui, -apple-system, sans-serif;
  }}
  #stage {{
    position: relative;
    width: 1080px;
    height: 1920px;
    overflow: hidden;
    background: radial-gradient(circle at 50% 25%, #162238 0%, #090e1a 60%, #04060c 100%);
  }}
  .media-layer {{
    position: absolute;
    inset: 0;
    overflow: hidden;
  }}
  .beat-img {{
    position: absolute;
    inset: -6%;
    width: 112%;
    height: 112%;
    object-fit: cover;
    filter: contrast(1.06) saturate(1.08) brightness(0.86);
    transform-origin: center center;
    will-change: transform, opacity;
  }}
  .procedural-bg {{
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 90px;
    text-align: center;
    background:
      radial-gradient(circle at 25% 25%, rgba(56, 189, 248, 0.22), transparent 55%),
      radial-gradient(circle at 75% 75%, rgba(245, 158, 11, 0.18), transparent 55%),
      linear-gradient(160deg, #0b1324 0%, #101d35 50%, #070c17 100%);
  }}
  .procedural-card {{
    width: 860px;
    padding: 54px 48px;
    border-radius: 28px;
    border: 2px solid rgba(148, 163, 184, 0.28);
    background: rgba(15, 23, 42, 0.72);
    box-shadow: 0 32px 80px rgba(0, 0, 0, 0.65);
  }}
  .procedural-kicker {{
    font-size: 26px;
    font-weight: 800;
    letter-spacing: 0.18em;
    color: #38bdf8;
    text-transform: uppercase;
    margin-bottom: 20px;
  }}
  .procedural-cue {{
    font-size: 46px;
    font-weight: 800;
    line-height: 1.24;
    color: #f8fafc;
  }}
  .vignette {{
    position: absolute;
    inset: 0;
    pointer-events: none;
    background:
      linear-gradient(180deg, rgba(4, 7, 15, 0.78) 0%, rgba(4, 7, 15, 0.12) 24%, rgba(4, 7, 15, 0.14) 62%, rgba(4, 7, 15, 0.90) 100%);
  }}
  .topbar {{
    position: absolute;
    top: 56px;
    left: 56px;
    right: 56px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    z-index: 10;
  }}
  .brand-pill {{
    display: inline-flex;
    align-items: center;
    gap: 14px;
    padding: 14px 26px;
    border-radius: 999px;
    background: rgba(15, 23, 42, 0.78);
    border: 1.5px solid rgba(56, 189, 248, 0.45);
    font-size: 24px;
    font-weight: 800;
    letter-spacing: 0.12em;
    color: #e2e8f0;
  }}
  .brand-dot {{
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: #38bdf8;
    box-shadow: 0 0 18px #38bdf8;
  }}
  .section-pill {{
    padding: 12px 24px;
    border-radius: 999px;
    background: rgba(245, 158, 11, 0.18);
    border: 1.5px solid rgba(245, 158, 11, 0.55);
    color: #fcd34d;
    font-size: 22px;
    font-weight: 800;
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }}
  .progress-track {{
    position: absolute;
    top: 24px;
    left: 56px;
    right: 56px;
    height: 8px;
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.16);
    overflow: hidden;
    z-index: 12;
  }}
  .progress-fill {{
    height: 100%;
    width: 0%;
    background: linear-gradient(90deg, #38bdf8 0%, #f59e0b 100%);
  }}
  .hero-title-wrap {{
    position: absolute;
    top: 200px;
    left: 64px;
    right: 64px;
    z-index: 9;
    padding: 36px 40px;
    border-radius: 26px;
    background: rgba(7, 11, 20, 0.78);
    border-left: 8px solid #f59e0b;
    box-shadow: 0 24px 60px rgba(0, 0, 0, 0.55);
  }}
  .hero-kicker {{
    font-size: 24px;
    font-weight: 800;
    letter-spacing: 0.16em;
    color: #38bdf8;
    text-transform: uppercase;
    margin-bottom: 14px;
  }}
  .hero-title {{
    font-size: 56px;
    font-weight: 900;
    line-height: 1.14;
    color: #ffffff;
    text-shadow: 0 4px 18px rgba(0, 0, 0, 0.65);
  }}
  .caption-wrap {{
    position: absolute;
    left: 64px;
    right: 64px;
    bottom: 185px;
    z-index: 10;
    text-align: center;
  }}
  .caption-box {{
    display: inline-block;
    max-width: 940px;
    padding: 26px 36px;
    border-radius: 24px;
    background: rgba(7, 11, 20, 0.84);
    border: 1.5px solid rgba(148, 163, 184, 0.30);
    box-shadow: 0 20px 50px rgba(0, 0, 0, 0.65);
    font-size: 44px;
    font-weight: 800;
    line-height: 1.28;
    color: #ffffff;
  }}
  .footer-bar {{
    position: absolute;
    left: 64px;
    right: 64px;
    bottom: 72px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    z-index: 10;
    font-size: 24px;
    font-weight: 700;
    color: rgba(226, 232, 240, 0.85);
    letter-spacing: 0.06em;
  }}
  .thumb-overlay {{
    position: absolute;
    inset: 0;
    z-index: 20;
    display: none;
    flex-direction: column;
    justify-content: flex-end;
    padding: 84px 68px 130px;
    background: linear-gradient(180deg, rgba(4, 7, 15, 0.20) 0%, rgba(4, 7, 15, 0.35) 45%, rgba(4, 7, 15, 0.94) 100%);
  }}
  .thumb-badge {{
    align-self: flex-start;
    padding: 14px 28px;
    border-radius: 999px;
    background: #f59e0b;
    color: #090e1a;
    font-size: 28px;
    font-weight: 900;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    margin-bottom: 28px;
  }}
  .thumb-headline {{
    font-size: 78px;
    font-weight: 950;
    line-height: 1.08;
    color: #ffffff;
    text-shadow: 0 8px 32px rgba(0, 0, 0, 0.85);
  }}
</style>
</head>
<body>
<div id="stage">
  <div class="media-layer" id="mediaLayer"></div>
  <div class="vignette"></div>
  <div class="progress-track"><div class="progress-fill" id="progressFill"></div></div>
  <div class="topbar">
    <div class="brand-pill"><span class="brand-dot"></span><span>CRAZZYDOCS</span></div>
    <div class="section-pill" id="sectionPill">DOCUMENTARY</div>
  </div>
  <div class="hero-title-wrap" id="heroWrap">
    <div class="hero-kicker" id="heroKicker">DOCUMENTARY BRIEF</div>
    <div class="hero-title" id="heroTitle">{safe_title}</div>
  </div>
  <div class="caption-wrap" id="captionWrap">
    <div class="caption-box" id="captionBox"></div>
  </div>
  <div class="footer-bar">
    <span id="beatCounter">BEAT 01</span>
    <span>LEMO-OPUSCAR STUDIO</span>
  </div>
  <div class="thumb-overlay" id="thumbOverlay">
    <div class="thumb-badge" id="thumbBadge">CRAZZYDOCS</div>
    <div class="thumb-headline" id="thumbHeadline">{safe_title}</div>
  </div>
</div>

<script>
const FILM = {data_json};
window.DUR = Number(FILM.duration || 45);

const params = new URLSearchParams(window.location.search);
const IS_THUMB = params.get("thumbnail") === "1";

const mediaLayer = document.getElementById("mediaLayer");
const progressFill = document.getElementById("progressFill");
const sectionPill = document.getElementById("sectionPill");
const heroWrap = document.getElementById("heroWrap");
const heroKicker = document.getElementById("heroKicker");
const heroTitle = document.getElementById("heroTitle");
const captionWrap = document.getElementById("captionWrap");
const captionBox = document.getElementById("captionBox");
const beatCounter = document.getElementById("beatCounter");
const thumbOverlay = document.getElementById("thumbOverlay");
const thumbBadge = document.getElementById("thumbBadge");
const thumbHeadline = document.getElementById("thumbHeadline");

const beatNodes = FILM.beats.map((beat, idx) => {{
  const container = document.createElement("div");
  container.style.position = "absolute";
  container.style.inset = "0";
  container.style.opacity = "0";

  if (beat.asset) {{
    const img = document.createElement("img");
    img.className = "beat-img";
    img.src = beat.asset;
    img.alt = beat.visual_cue || FILM.title;
    container.appendChild(img);
    container._img = img;
  }} else {{
    const proc = document.createElement("div");
    proc.className = "procedural-bg";
    const card = document.createElement("div");
    card.className = "procedural-card";
    const kicker = document.createElement("div");
    kicker.className = "procedural-kicker";
    kicker.textContent = `${{FILM.category || "DOCUMENTARY"}} · BEAT ${{idx + 1}}`;
    const cue = document.createElement("div");
    cue.className = "procedural-cue";
    cue.textContent = beat.visual_cue || FILM.title;
    card.appendChild(kicker);
    card.appendChild(cue);
    proc.appendChild(card);
    container.appendChild(proc);
  }}

  mediaLayer.appendChild(container);
  return container;
}});

function clamp01(v) {{
  return Math.max(0, Math.min(1, v));
}}

function applyMotion(img, motion, p) {{
  if (!img) return;
  let scale = 1.04;
  let tx = 0;
  let ty = 0;
  if (motion === "zoom_in") {{
    scale = 1.02 + 0.11 * p;
    ty = -14 * p;
  }} else if (motion === "zoom_out") {{
    scale = 1.13 - 0.10 * p;
    ty = 12 * (1 - p);
  }} else if (motion === "pan_right") {{
    scale = 1.09;
    tx = -28 + 56 * p;
  }} else {{
    scale = 1.09;
    tx = 28 - 56 * p;
  }}
  img.style.transform = `translate3d(${{tx.toFixed(2)}}px, ${{ty.toFixed(2)}}px, 0) scale(${{scale.toFixed(4)}})`;
}}

window.render = function(t) {{
  const dur = Math.max(1, window.DUR);
  const clampedT = Math.max(0, Math.min(dur, Number(t || 0)));
  progressFill.style.width = `${{(clamp01(clampedT / dur) * 100).toFixed(2)}}%`;

  let activeIdx = 0;
  for (let i = 0; i < FILM.beats.length; i++) {{
    const b = FILM.beats[i];
    if (clampedT >= b.start && clampedT <= b.end) {{
      activeIdx = i;
      break;
    }}
    if (clampedT > b.end) activeIdx = i;
  }}

  const activeBeat = FILM.beats[activeIdx] || FILM.beats[0];
  const span = Math.max(0.5, (activeBeat.end - activeBeat.start));
  const localP = clamp01((clampedT - activeBeat.start) / span);

  for (let i = 0; i < beatNodes.length; i++) {{
    const node = beatNodes[i];
    if (i === activeIdx) {{
      const fadeIn = clamp01((clampedT - activeBeat.start) / 0.25);
      node.style.opacity = fadeIn.toFixed(3);
      applyMotion(node._img, FILM.beats[i].motion, localP);
    }} else {{
      node.style.opacity = "0";
    }}
  }}

  sectionPill.textContent = (activeBeat.section || FILM.category || "DOCUMENTARY").slice(0, 22);
  beatCounter.textContent = `BEAT ${{String(activeBeat.beat_number || activeIdx + 1).padStart(2, "0")}} / ${{String(FILM.beats.length).padStart(2, "0")}}`;

  if (IS_THUMB) {{
    heroWrap.style.display = "none";
    captionWrap.style.display = "none";
    thumbOverlay.style.display = "flex";
    thumbBadge.textContent = (FILM.category || "CRAZZYDOCS").toUpperCase();
    thumbHeadline.textContent = FILM.thumb_headline || FILM.title;
    return;
  }}

  thumbOverlay.style.display = "none";
  if (clampedT < 3.4) {{
    const titleAlpha = clampedT < 2.8 ? 1 : clamp01((3.4 - clampedT) / 0.6);
    heroWrap.style.display = "block";
    heroWrap.style.opacity = titleAlpha.toFixed(3);
    heroKicker.textContent = (FILM.category || "DOCUMENTARY").toUpperCase();
    heroTitle.textContent = FILM.title;
  }} else {{
    heroWrap.style.display = "none";
  }}

  const cap = (activeBeat.caption || "").trim();
  if (cap) {{
    captionWrap.style.display = "block";
    captionBox.textContent = cap;
  }} else {{
    captionWrap.style.display = "none";
  }}
}};

window.render(0);
</script>
</body>
</html>
"""
