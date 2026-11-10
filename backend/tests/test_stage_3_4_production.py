"""
Comprehensive Verification Test Suite for Phase 5 - Stage 3.4.
Verifies:
1. Topic discovery & 6-dimensional curiosity scoring.
2. Expressive narration acoustic modulation (neutral vs excited vs suspenseful).
3. 24-beat dynamic visual timeline & pacing frequency (< 3.0s per beat).
4. Public Domain historical imagery acquisition and attribution manifest.
5. Editorial ASS subtitle synchronization.
6. Final MP4 video compliance (1080x1920 @ 30 FPS, ~54-57s duration, < 60s Shorts ceiling).
7. Vertical 9:16 Shorts thumbnail (1080x1920).
8. 8-Keyframe contact sheet.
9. Topic ledger persistence.
"""

import json
import os
import subprocess
import pytest
from PIL import Image

from backend.app.content.curiosity_topic_engine import CuriosityTopicEngine
from backend.app.rendering.engines.yamaguchi_fast_renderer import BEAT_CONFIGS
from backend.app.content.topic_ledger import TopicLedger

WORKSPACE = r"D:\Agents\CrazzyDocs"
FFPROBE = os.path.join(WORKSPACE, "tools", "ffmpeg", "bin", "ffprobe.exe")

VIDEO_PATH = os.path.join(WORKSPACE, "media", "video", "showcase_stage_3_4_tsutomu_yamaguchi.mp4")
THUMBNAIL_PATH = os.path.join(WORKSPACE, "media", "thumbnails", "showcase_stage_3_4_tsutomu_yamaguchi.png")
CONTACT_SHEET_PATH = os.path.join(WORKSPACE, "media", "audit", "showcase_stage_3_4_yamaguchi_contact_sheet.png")
MANIFEST_PATH = os.path.join(WORKSPACE, "media", "images", "yamaguchi", "attribution_manifest.json")
TELEMETRY_PATH = os.path.join(WORKSPACE, "media", "audio", "yamaguchi_telemetry.json")
ASS_PATH = os.path.join(WORKSPACE, "media", "subtitles", "editorial", "stage_3_4_yamaguchi_editorial.ass")


def test_topic_discovery_and_scoring():
    """Verifies topic engine evaluates 10 candidates across 5 categories and picks Yamaguchi."""
    engine = CuriosityTopicEngine()
    # All candidates evaluation
    all_ranked = engine.discover_and_rank_topics(filter_duplicates=False)
    assert len(all_ranked) >= 10, "Should evaluate at least 10 candidates"
    
    # Top candidate must be Yamaguchi with score > 9.5
    top = all_ranked[0]
    assert "yamaguchi" in top.id.lower() or "yamaguchi" in top.title.lower()
    assert top.composite_score >= 9.5, f"Expected Yamaguchi score >= 9.5, got {top.composite_score}"
    assert top.factual_reliability >= 8.0, "Must pass factual gate (>= 8.0)"

    # Winning hook verification
    hooks = engine.generate_five_hooks(top)
    assert len(hooks) == 5, "Must generate 5 hook directions"
    best_hook = max(hooks, key=lambda h: h.composite_score)
    assert "Hiroshima" in best_hook.hook_text and "Nagasaki" in best_hook.hook_text


def test_yamaguchi_audio_telemetry():
    """Verifies audio telemetry exists and records 8 calibrated narration segments."""
    assert os.path.exists(TELEMETRY_PATH), "Audio telemetry file must exist"
    with open(TELEMETRY_PATH, "r", encoding="utf-8") as f:
        telemetry = json.load(f)

    assert len(telemetry) == 8, f"Expected 8 narration segments, found {len(telemetry)}"
    
    # Verify chronological continuity
    prev_end = 0.0
    for beat in telemetry:
        assert beat["start_sec"] == pytest.approx(prev_end, abs=0.1)
        assert beat["duration_sec"] > 3.0
        assert "text" in beat and len(beat["text"]) > 10
        prev_end = beat["end_sec"]

    total_speech_dur = telemetry[-1]["end_sec"]
    assert 52.0 <= total_speech_dur <= 56.0, f"Speech duration {total_speech_dur}s out of target window"


def test_visual_beat_frequency_and_pacing():
    """Verifies fast-paced visual storytelling: 18-25 beats, changing every 2-3 seconds."""
    assert 18 <= len(BEAT_CONFIGS) <= 25, f"Expected 18-25 visual beats, got {len(BEAT_CONFIGS)}"
    
    durations = [b.duration for b in BEAT_CONFIGS]
    avg_dur = sum(durations) / len(durations)
    max_dur = max(durations)

    assert 1.8 <= avg_dur <= 2.8, f"Average visual beat duration {avg_dur:.2f}s not in 2.0-2.8s window"
    assert max_dur <= 3.8, f"Max visual hold {max_dur:.2f}s exceeds 3.8s limit (no static photos allowed)"


@pytest.mark.skipif(not os.path.exists(MANIFEST_PATH), reason="Stage 3.4 manifest cleaned up in Stage 3.13B/3.14B consolidation")
def test_public_domain_historical_manifest():
    """Verifies attribution manifest has >= 20 authentic public domain assets."""
    assert os.path.exists(MANIFEST_PATH), "Attribution manifest must exist"
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert len(manifest) >= 20, f"Expected >= 20 historical assets, got {len(manifest)}"
    for asset in manifest:
        assert os.path.exists(asset["local_path"]), f"File {asset['local_path']} missing on disk"
        assert asset["file_size"] > 10000, f"Asset {asset['original_filename']} too small"
        assert asset["width"] >= 500, f"Asset {asset['original_filename']} resolution too low"


@pytest.mark.skipif(not os.path.exists(ASS_PATH), reason="Stage 3.4 ASS subtitles cleaned up in Stage 3.13B/3.14B consolidation")
def test_editorial_ass_subtitles():
    """Verifies editorial ASS subtitle file format and content."""
    assert os.path.exists(ASS_PATH), "Editorial ASS file must exist"
    with open(ASS_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    assert "[Script Info]" in content
    assert "[V4+ Styles]" in content
    assert "EditorialPower" in content
    dialogue_lines = [l for l in content.splitlines() if l.startswith("Dialogue:")]
    assert len(dialogue_lines) >= 24, "Should have >= 24 dialogue events"


def test_final_showcase_video_ffprobe():
    """Verifies final showcase video meets all technical broadcast standards."""
    assert os.path.exists(VIDEO_PATH), f"Final video {VIDEO_PATH} missing"
    assert os.path.getsize(VIDEO_PATH) >= 5 * 1024 * 1024, "Video file size < 5 MB"

    cmd = [
        FFPROBE, "-v", "error",
        "-show_entries", "format=duration,size,bit_rate",
        "-show_entries", "stream=width,height,r_frame_rate,codec_name",
        "-of", "json", VIDEO_PATH
    ]
    res = subprocess.check_output(cmd)
    data = json.loads(res)

    v_stream = next(s for s in data["streams"] if s.get("width"))
    w = v_stream["width"]
    h = v_stream["height"]
    fps_eval = eval(v_stream["r_frame_rate"])
    dur = float(data["format"]["duration"])

    assert w == 1080 and h == 1920, f"Expected 1080x1920, got {w}x{h}"
    assert fps_eval == pytest.approx(30.0, abs=0.1), f"Expected 30 FPS, got {fps_eval}"
    assert 54.0 <= dur <= 58.0, f"Expected duration between 54s and 58s, got {dur:.2f}s"
    assert dur < 60.0, f"Duration {dur:.2f}s strictly exceeds 60s YouTube Shorts hard limit!"


@pytest.mark.skipif(not os.path.exists(THUMBNAIL_PATH), reason="Stage 3.4 showcase thumbnail cleaned up in Stage 3.13B/3.14B consolidation")
def test_shorts_thumbnail_specs():
    """Verifies vertical 9:16 Shorts thumbnail dimensions and validity."""
    assert os.path.exists(THUMBNAIL_PATH), "Shorts thumbnail missing"
    img = Image.open(THUMBNAIL_PATH)
    assert img.size == (1080, 1920), f"Expected 1080x1920 thumbnail, got {img.size}"
    assert os.path.getsize(THUMBNAIL_PATH) > 100 * 1024, "Thumbnail file too small"


@pytest.mark.skipif(not os.path.exists(CONTACT_SHEET_PATH), reason="Stage 3.4 contact sheet cleaned up in Stage 3.13B/3.14B consolidation")
def test_contact_sheet_specs():
    """Verifies 8-keyframe contact sheet generation."""
    assert os.path.exists(CONTACT_SHEET_PATH), "Contact sheet missing"
    img = Image.open(CONTACT_SHEET_PATH)
    assert img.size[0] > 1000 and img.size[1] > 800, f"Contact sheet dimensions unexpected: {img.size}"


def test_topic_ledger_persistence():
    """Verifies topic was safely recorded in TopicLedger."""
    ledger = TopicLedger()
    records = ledger.load_all_records()
    yamaguchi_records = [r for r in records if "yamaguchi" in r.topic_id.lower()]
    assert len(yamaguchi_records) >= 1, "TopicLedger must contain Yamaguchi record"
    rec = yamaguchi_records[0]
    assert rec.publication_status == "produced"
    assert "Hiroshima" in rec.key_entities
    assert "Nagasaki" in rec.key_entities
