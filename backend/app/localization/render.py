"""Stage 4.02: Hindi film from the English film's final plan, rendered by the existing Lemo-Opuscar / Pop Info path.

* Reused from the English job: the scene structure, templates, procedural art, licensed photos and footage
  frames (``pop_info_film/assets``), the transitions and the sound design.
* Never reused: any English text. Narration, headline, highlight, labels, units and the category come from the
  Hindi adaptation; scenes are retimed to the Hindi voice; SFX cues move with their scene.
* Devanagari: the canvas is shaped by Chromium (HarfBuzz), so conjuncts and vowel signs render correctly once the
  font stack has a Devanagari face. The Hindi copy of engine.js puts the system "Nirmala UI" first (English is
  untouched). Subtitles are an ASS file rendered by libass + HarfBuzz (validated by rendering one frame).
"""
from __future__ import annotations

import copy
import html
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

HINDI_FONT = "Nirmala UI"
FONT_LINE = re.compile(r"const FONT = \{[^}]*\};")
HINDI_FONT_LINE = ("const FONT = { head: '\"Nirmala UI\", \"Fredoka\", \"Segoe UI\", sans-serif', "
                   "num: '\"Lilita One\", \"Nirmala UI\", \"Fredoka\", Impact, sans-serif' };")
OUTRO_SEC = 0.6


def build_hindi_plan(english_plan: Dict[str, Any], hindi: Dict[str, Any], timings: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    plan = copy.deepcopy(english_plan)
    by_id = {s["scene_id"]: s for s in hindi["scenes"]}
    timing = {t["scene_id"]: t for t in timings}
    for sc in plan["scenes"]:
        hi = by_id[sc["scene_id"]]
        t = timing[sc["scene_id"]]
        old_start = float(sc["start_sec"])
        new_start = float(t["start"])
        new_end = round(new_start + float(t["duration"]) + 0.12, 3)
        sc["narration"] = hi["narration_hi"]
        sc["headline"] = hi["headline_hi"]
        sc["highlight"] = hi.get("highlight_hi") or ""
        labels = list(hi.get("labels_hi") or [])
        if labels and len(labels) == len(sc.get("labels") or []):
            sc["labels"] = labels
        units = list(hi.get("units_hi") or [])
        for n, num in enumerate([x for x in (sc.get("numbers") or []) if x.get("unit")]):
            if n < len(units) and units[n]:
                num["unit"] = units[n]
        if sc.get("stage_label"):
            sc["stage_label"] = {"HOOK": "सवाल", "CONTEXT": "समझिए", "ESCALATION": "और भी", "REVELATION": "असली बात",
                                 "PAYOFF": "नतीजा"}.get(str(sc.get("stage") or "").upper(), sc["stage_label"])
        for cue in sc.get("sfx") or []:
            cue["t"] = round(min(new_end, max(new_start, float(cue["t"]) - old_start + new_start)), 3)
        sc["start_sec"], sc["end_sec"] = round(new_start, 3), new_end
    plan["duration_sec"] = round(float(plan["scenes"][-1]["end_sec"]) + OUTRO_SEC, 3)
    plan["category"] = hindi.get("category_hi") or plan.get("category")
    plan["topic"] = hindi.get("title_hi") or plan.get("topic")
    plan["language"] = "hi"
    plan["localized_from"] = {"job_id": english_plan.get("job_id"), "engine_revision": english_plan.get("engine_revision")}
    return plan


def package_film(english_film_dir: Path, out_dir: Path, plan: Dict[str, Any], *, size=(1080, 1920)) -> Path:
    from backend.app.rendering.pop_info.film_builder import ENGINE_JS, ENVIRONMENTS_JS, PAGE
    film = Path(out_dir)
    film.mkdir(parents=True, exist_ok=True)
    src_assets = Path(english_film_dir) / "assets"
    if src_assets.is_dir() and not (film / "assets").exists():
        shutil.copytree(src_assets, film / "assets")
    src_fonts = Path(english_film_dir) / "fonts"
    if src_fonts.is_dir() and not (film / "fonts").exists():
        shutil.copytree(src_fonts, film / "fonts")
    js = Path(ENGINE_JS).read_text(encoding="utf-8")
    if not FONT_LINE.search(js):
        raise RuntimeError("engine.js font table not found; cannot set the Devanagari font")
    (film / "engine.js").write_text(FONT_LINE.sub(lambda _m: HINDI_FONT_LINE, js, count=1), encoding="utf-8")
    shutil.copyfile(ENVIRONMENTS_JS, film / "environments.js")
    (film / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    page = PAGE.format(title=html.escape(str(plan.get("topic") or "")), w=size[0], h=size[1],
                       plan=json.dumps(plan, ensure_ascii=False).replace("</", "<\\/"))
    page = page.replace('<html>', '<html lang="hi">', 1)
    (film / "index.html").write_text(page, encoding="utf-8")
    return film


def _ass_time(t: float) -> str:
    h, rem = divmod(max(0.0, t), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def wrap_hindi(text: str, max_chars: int = 26) -> List[str]:
    lines, cur = [], ""
    for w in text.split():
        if cur and len(cur) + 1 + len(w) > max_chars:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def write_ass(plan: Dict[str, Any], path: Path) -> Dict[str, Any]:
    """Hindi subtitle file (one event per scene, at most two lines on screen, wrapped on word boundaries)."""
    events, longest = [], 0
    for sc in plan["scenes"]:
        lines = wrap_hindi(sc["narration"])
        longest = max(longest, max((len(l) for l in lines), default=0))
        chunks = [lines[i:i + 2] for i in range(0, len(lines), 2)] or [[""]]
        span = (float(sc["end_sec"]) - float(sc["start_sec"])) / len(chunks)
        for k, ch in enumerate(chunks):
            a = float(sc["start_sec"]) + k * span
            text = "\\N".join(ch)          # ASS hard line break
            events.append(f"Dialogue: 0,{_ass_time(a)},{_ass_time(a + span - 0.02)},HI,,0,0,0,,{text}")
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
            "MarginR, MarginV, Encoding\n"
            f"Style: HI,{HINDI_FONT},58,&H00FFFFFF,&H00FFFFFF,&H00202020,&H80000000,1,0,0,0,100,100,0,0,1,4,1,2,90,90,"
            "260,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    path.write_text(head + "\n".join(events) + "\n", encoding="utf-8-sig")
    return {"path": str(path), "events": len(events), "longest_line_chars": longest}


def render_check_ass(ffmpeg: str, ass_path: Path, at: float, out_png: Path) -> Dict[str, Any]:
    """Renders one frame of the subtitles over a flat background (libass + HarfBuzz) and measures inked pixels."""
    esc = str(ass_path).replace("\\", "/").replace(":", "\\:")
    r = subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "color=c=0x3a5a40:s=1080x1920:d=60", "-ss", f"{at:.2f}", "-vf", f"subtitles='{esc}'",
                        "-frames:v", "1", str(out_png)], capture_output=True, text=True, timeout=120)
    if r.returncode != 0 or not out_png.is_file():
        return {"ok": False, "error": (r.stderr or "")[-300:]}
    from PIL import Image
    img = Image.open(out_png).convert("RGB")
    w, h = img.size
    band = img.crop((0, int(h * 0.70), w, h))
    px = band.getdata()
    ink = sum(1 for p in px if p[0] > 200 and p[1] > 200 and p[2] > 200)
    rows = [y for y in range(band.size[1]) if any(band.getpixel((x, y))[0] > 200 for x in range(0, w, 6))]
    clipped = bool(rows) and (min(rows) <= 1 or max(rows) >= band.size[1] - 2)
    return {"ok": ink > 1500 and not clipped, "inked_pixels": ink, "clipped": clipped, "frame": str(out_png)}


def render_film(film_dir: Path, workspace_root: Path, silent_out: Path) -> None:
    from backend.app.rendering.pop_info.pipeline import lemo_adapter
    lemo_adapter().render_video(workspace=workspace_root, demo_dir=film_dir, output_path=silent_out, fps=30,
                                size=(1080, 1920))


def mux(ffmpeg: str, silent: Path, voice_wav: Path, plan: Dict[str, Any], work_dir: Path, out_mp4: Path) -> Dict[str, Any]:
    from backend.app.rendering.ffmpeg_geometry import DELIVERY_COLOR_VF
    from backend.app.rendering.pop_info.sound_design import mix_audio
    audio = mix_audio(ffmpeg=ffmpeg, voice_path=str(voice_wav), plan=plan, work_dir=work_dir, out_path=work_dir / "mix.wav")
    r = subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(silent), "-i", audio["mix_path"],
                        "-map", "0:v:0", "-map", "1:a:0", "-vf", DELIVERY_COLOR_VF, "-c:v", "libx264", "-r", "30",
                        "-crf", "18", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
                        str(out_mp4)], capture_output=True, text=True, timeout=900)
    if r.returncode != 0 or not out_mp4.is_file():
        raise RuntimeError(f"Hindi mux failed: {(r.stderr or '')[-400:]}")
    return {"mix": audio}
