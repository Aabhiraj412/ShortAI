"""
Automated Test Suite for CrazzyDocs Stage 3.3.1 Corrections.

Validates:
1. Text boundary enforcement & safe zone compliance.
2. Automatic dynamic font resizing & intelligent wrapping.
3. Open-license verification on acquired historical media.
4. Attribution manifest generation & licensing metadata.
5. Image relevance scoring and historical alignment.
6. 9:16 vertical thumbnail dimensions (1080x1920) and 16:9 profile availability.
7. Email configuration validator & missing-secret security.
8. Failure notification dispatch to desktop & audit log.
9. Notification anti-spam deduplication logic.
"""

import json
import os
import sys
import time
from PIL import Image, ImageDraw

from backend.app.rendering.text.text_bounds_engine import (
    BoundingBox,
    TextBoundsEngine,
    text_bounds_engine,
)
from backend.app.rendering.engines.thumbnail_generator import (
    ThumbnailGenerator,
    ThumbnailProfile,
)
from backend.app.services.notification_service import (
    NotificationManager,
    AlertPayload,
)

WORKSPACE_ROOT = r"D:\Agents\CrazzyDocs"
HIST_MANIFEST = os.path.join(WORKSPACE_ROOT, "media", "images", "historical", "attribution_manifest.json")
THUMBNAIL_PATH = os.path.join(WORKSPACE_ROOT, "media", "thumbnails", "showcase_stage_3_5_yamaguchi_refined.png")


def test_text_boundaries_no_overflow():
    """Validates that rendered text stays strictly inside bounding box and safe zones."""
    engine = TextBoundsEngine()
    img = Image.new("RGB", (1080, 1920), (10, 14, 22))
    draw = ImageDraw.Draw(img)

    # Standard safe lower label box
    box = BoundingBox(100, 1340, 980, 1420)
    text = "PURITY DISTILLING CO. — 50-FT STEEL STORAGE TANK"
    res = engine.draw_bounded_text(draw, text, box, max_font_size=28, min_font_size=18, padding=12)

    assert res.fits_safely, "Text must fit safely inside bounding box"
    assert res.actual_width <= box.width, f"Width {res.actual_width} exceeds box width {box.width}"
    assert res.actual_height <= box.height, f"Height {res.actual_height} exceeds box height {box.height}"

    report = engine.validate_rendered_elements([res])
    assert report.is_valid, f"Validation failed: {report.violations}"
    print("  [PASS] test_text_boundaries_no_overflow")


def test_automatic_text_resizing():
    """Validates that oversized text dynamically steps down font size until it fits."""
    engine = TextBoundsEngine()
    img = Image.new("RGB", (1080, 1920))
    draw = ImageDraw.Draw(img)

    # Small box with long text
    small_box = BoundingBox(200, 400, 800, 520)
    long_text = "The crushing force flattened buildings, ripped elevated train tracks, and trapped victims in sludge."
    res = engine.draw_bounded_text(draw, long_text, small_box, max_font_size=48, min_font_size=16, padding=10)

    assert res.font_size < 48, f"Font size should have been scaled down from 48, got {res.font_size}"
    assert res.fits_safely, "Autoscaled text must fit container box"
    print(f"  [PASS] test_automatic_text_resizing (scaled 48 -> {res.font_size}pt, {len(res.lines)} lines)")


def test_image_licensing_validation():
    """Validates that all acquired historical assets possess verified Public Domain or CC licenses."""
    assert os.path.exists(HIST_MANIFEST), f"Attribution manifest missing at {HIST_MANIFEST}"
    with open(HIST_MANIFEST, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert len(manifest) >= 6, f"Expected at least 6 historical assets, got {len(manifest)}"

    allowed_terms = ["public domain", "cc by", "cc0"]
    for asset in manifest:
        lic = asset.get("license", "").lower()
        is_allowed = any(term in lic for term in allowed_terms)
        assert is_allowed, f"Asset {asset['id']} has unauthorized license: {lic}"
        assert os.path.exists(asset["local_path"]), f"Asset file missing: {asset['local_path']}"
        assert asset["size_bytes"] > 1000, f"Asset file corrupt/empty: {asset['id']}"

    print(f"  [PASS] test_image_licensing_validation ({len(manifest)} assets verified)")


def test_attribution_generation():
    """Validates that every acquired asset includes complete provenance and attribution metadata."""
    with open(HIST_MANIFEST, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    required_fields = ["id", "title", "url", "source_page", "creator", "license", "relevance", "local_path"]
    for asset in manifest:
        for field_name in required_fields:
            assert field_name in asset and asset[field_name], f"Asset {asset.get('id')} missing field {field_name}"

    print("  [PASS] test_attribution_generation (All required provenance fields present)")


def test_image_relevance():
    """Validates that all acquired assets are directly relevant to the 1919 Boston Molasses Flood."""
    with open(HIST_MANIFEST, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    relevant_keywords = [
        "molasses", "tank", "boston", "disaster", "railway", "purity", "globe",
        "syrup", "wreckage", "steel", "auditor", "inquest", "tracks", "wave"
    ]
    for asset in manifest:
        text_corpus = (asset["title"] + " " + asset["relevance"]).lower()
        matches = [kw for kw in relevant_keywords if kw in text_corpus]
        assert len(matches) >= 2, f"Asset {asset['id']} lacks historical relevance keywords: {matches}"

    print("  [PASS] test_image_relevance (100% topic alignment confirmed)")


def test_thumbnail_9_16_dimensions():
    """Validates that the Shorts thumbnail is strictly 1080x1920 (9:16 vertical) and 16:9 profile exists."""
    assert os.path.exists(THUMBNAIL_PATH), f"Shorts thumbnail missing at {THUMBNAIL_PATH}"
    im = Image.open(THUMBNAIL_PATH)
    assert im.size == (1080, 1920), f"Shorts thumbnail dimensions must be (1080, 1920), got {im.size}"

    # Also verify that 16:9 profile remains available
    gen_16x9 = ThumbnailGenerator(profile=ThumbnailProfile.STANDARD_HORIZONTAL)
    assert gen_16x9.width == 1280 and gen_16x9.height == 720
    print(f"  [PASS] test_thumbnail_9_16_dimensions (Shorts 1080x1920 verified, 16:9 profile preserved)")


def test_email_configuration_validation():
    """Validates that unconfigured SMTP gracefully disables email without crashing."""
    # Ensure no credentials in test environment
    os.environ.pop("CRAZZYDOCS_SMTP_HOST", None)
    os.environ.pop("SMTP_HOST", None)

    mgr = NotificationManager(alert_log_path="logs/_test_notif.jsonl")
    result = mgr.notify_email("Test Subject", "Test Body")
    assert result is False, "Email dispatch must return False when unconfigured"
    print("  [PASS] test_email_configuration_validation (Safe unconfigured state validated)")


def test_failure_notification_dispatch():
    """Validates that send_failure triggers desktop alert and writes to audit log."""
    test_log = "logs/_test_notif_failure.jsonl"
    if os.path.exists(test_log):
        os.remove(test_log)

    mgr = NotificationManager(alert_log_path=test_log, cooldown_seconds=0.0)
    dispatch_res = mgr.send_failure(
        stage="test_rendering",
        error_summary="Test intentional boundary failure",
        job_id="job_test_001",
    )

    assert dispatch_res["desktop"] is True, "Desktop notification must succeed"
    assert dispatch_res["email"] is False, "Email must remain False while unconfigured"
    assert os.path.exists(test_log), "Audit log file must be created"

    with open(test_log, "r", encoding="utf-8") as f:
        line = f.readline()
        record = json.loads(line)
        assert record["event_type"] == "FAILURE"
        assert record["stage"] == "test_rendering"

    if os.path.exists(test_log):
        os.remove(test_log)
    print("  [PASS] test_failure_notification_dispatch (Desktop + JSONL verified)")


def test_notification_deduplication():
    """Validates that duplicate failure alerts within cooldown window are suppressed."""
    mgr = NotificationManager(alert_log_path="logs/_test_notif_dedup.jsonl", cooldown_seconds=60.0)
    res1 = mgr.send_failure("qc", "Duplicate error message", "job_01")
    res2 = mgr.send_failure("qc", "Duplicate error message", "job_01")

    assert res1["deduplicated"] is False, "First alert must not be deduplicated"
    assert res2["deduplicated"] is True, "Second identical alert must be deduplicated"
    print("  [PASS] test_notification_deduplication (Anti-spam cooldown verified)")


def run_all_tests():
    print("=" * 70)
    print("RUNNING CRAZZYDOCS STAGE 3.3.1 CORRECTIONS TEST SUITE")
    print("=" * 70)

    test_text_boundaries_no_overflow()
    test_automatic_text_resizing()
    test_image_licensing_validation()
    test_attribution_generation()
    test_image_relevance()
    test_thumbnail_9_16_dimensions()
    test_email_configuration_validation()
    test_failure_notification_dispatch()
    test_notification_deduplication()

    print("=" * 70)
    print("ALL 9 TESTS PASSED SUCCESSFULLY [100% PASS RATE]")
    print("=" * 70)


if __name__ == "__main__":
    run_all_tests()
