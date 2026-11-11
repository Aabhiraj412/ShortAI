"""
Stage 3.21K Verification Suite:
- Isolated Validation Topic History & Semantic Collision Prevention
- Multi-Source Live Topic Discovery Diversity & Fail-Closed Semantics
- Thumbnail Headline Non-Repetition
- Dashboard 4-State Reconciliation (Scheduler / Production / Validation / Overall)
- Canonical Deliverable Manifest & Streaming Endpoints
- Structured Rotating Logging & Polling Flood Controls
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from unittest.mock import patch

BACKEND_DIR = str(Path(__file__).resolve().parent.parent)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from backend.app.content.curiosity_topic_engine import CuriosityTopicEngine
from backend.app.content.topic_ledger import ContentRecord, TopicLedger
from backend.app.content.validation_topic_history import (
    ValidationTopicHistory,
    compute_research_fingerprint,
    compute_semantic_similarity,
    compute_topic_fingerprint,
)
from backend.app.main import app
from backend.app.orchestration.production_workspace import ProductionWorkspace
from backend.app.rendering.engines.creative_thumbnail_engine import CreativeThumbnailEngine
from backend.app.services.production_health_dashboard import ProductionHealthDashboard
from backend.app.services.production_observability import ProductionObservabilityStore
from backend.scripts.run_dashboard import RoutinePollingAccessFilter, build_uvicorn_log_config


def _init_temp_ledger(tmp_path: Path):
    db_path = str(tmp_path / "test_studio.db")
    json_path = str(tmp_path / "test_topic_history.json")
    val_path = str(tmp_path / "test_validation_history.json")
    vh = ValidationTopicHistory(history_path=val_path, default_cooldown_days=30)
    ledger = TopicLedger(db_path=db_path, json_path=json_path, validation_history=vh)
    return ledger, vh


def test_validation_history_isolation_and_semantic_evasion(tmp_path):
    """Validation topics stay isolated from production ledger and block headline swaps/paraphrases."""
    ledger, vh = _init_temp_ledger(tmp_path)

    # Record a genuine production topic separately
    ledger.record_content(
        ContentRecord(
            topic_id="prod_01",
            topic_title="The Demon Core: The 14-Pound Plutonium Sphere",
            opening_hook="A flathead screwdriver slipped.",
            main_subject="Demon Core",
            key_entities=["Louis Slotin", "Los Alamos", "Plutonium"],
            script_fingerprint="fp_prod_01",
            category="Science & Technology",
            visual_style="historical_documentary",
            created_at=datetime.utcnow().isoformat() + "Z",
        ),
        execution_context="PRODUCTION",
        allow_override=True,
    )
    assert len(ledger.load_all_records()) == 1

    # Record validation topic via ValidationTopicHistory; genuine ledger must remain 1
    vh.record_selection(
        run_id="val_run_001",
        topic_id="live_falkland",
        topic_title="Land mines in the Falkland Islands",
        execution_context="VALIDATION",
        outcome="completed",
        key_entities=["Falkland Islands", "Land mine", "Stanley", "Royal Engineers"],
        research_fingerprint="res_fp_falkland_1",
        script_fingerprint="scr_fp_falkland_1",
        thumbnail_headline="LAND MINES FALKLAND",
        discovery_source="wikipedia_onthisday",
    )
    assert len(ledger.load_all_records()) == 1
    assert len(vh.load_records()) == 1

    # Attempt NON-PRODUCTION write via TopicLedger.record_content -> must NOT touch genuine ledger
    ledger.record_content(
        ContentRecord(
            topic_id="val_run_001_leak_check",
            topic_title="Fake Validation Leak Check",
            opening_hook="Hook",
            main_subject="Leak",
            key_entities=["Test"],
            script_fingerprint="fp_leak",
            category="Science & Technology",
            visual_style="historical_documentary",
            created_at=datetime.utcnow().isoformat() + "Z",
        ),
        execution_context="VALIDATION",
    )
    assert len(ledger.load_all_records()) == 1

    # Collision checks: exact, headline word-order swap, and inflected paraphrase must ALL block
    for variant in (
        "Land mines in the Falkland Islands",
        "Falkland Islands Land Mines",
        "Mines in the Falklands",
    ):
        dup, score, reason = ledger.check_content_collision(
            candidate_title=variant,
            candidate_hook="Thousands of unexploded mines remained.",
            candidate_entities=["Falkland Islands"],
            cooldown_days=30,
        )
        assert dup is True, f"Variant '{variant}' unexpectedly bypassed validation collision"
        assert score >= 0.70
        assert "Validation" in reason

    # Same research fingerprint under completely rewritten title must ALSO block
    dup_rf, _, _ = ledger.check_content_collision(
        candidate_title="Unrelated Rewritten Documentary Headline",
        candidate_hook="Completely new phrasing",
        candidate_entities=["South Atlantic"],
        research_fingerprint="res_fp_falkland_1",
    )
    assert dup_rf is True

    # Genuinely unrelated fresh topic passes cleanly
    dup_fresh, _, _ = ledger.check_content_collision(
        candidate_title="Mount Tambora 1815 Volcanic Winter",
        candidate_hook="The largest recorded eruption plunged Europe into darkness.",
        candidate_entities=["Mount Tambora", "Indonesia", "Year Without a Summer"],
        research_fingerprint="res_fp_tambora_1",
    )
    assert dup_fresh is False


def test_validation_history_rejection_and_cooldown_expiry(tmp_path):
    """Rejected candidates get immediate cooldown; expired (>30d) validation entries unlock cleanly."""
    ledger, vh = _init_temp_ledger(tmp_path)

    vh.record_selection(
        run_id="val_rej_01",
        topic_id="live_rej",
        topic_title="Tacoma Narrows Bridge Collapse",
        outcome="rejected",
        rejection_reason="Audio check failed",
        key_entities=["Tacoma Narrows Bridge", "Galloping Gertie"],
    )
    dup_rej, _, reason_rej = ledger.check_content_collision(
        "Tacoma Narrows Bridge Collapse", "", ["Tacoma Narrows Bridge"]
    )
    assert dup_rej is True
    assert "rejected" in reason_rej.lower()

    # Old (>35 days) completed topic expires past 30-day cooldown
    old_ts = (datetime.now(timezone.utc) - timedelta(days=35)).isoformat()
    vh.record_selection(
        run_id="val_old_01",
        topic_id="live_old",
        topic_title="Vasa Warship Sinking 1628",
        outcome="completed",
        key_entities=["Vasa", "Stockholm Harbor"],
        selected_at=old_ts,
        cooldown_days=30,
    )
    dup_old, _, _ = ledger.check_content_collision(
        "Vasa Warship Sinking 1628", "", ["Vasa", "Stockholm Harbor"], cooldown_days=30
    )
    assert dup_old is False


def test_consecutive_live_discovery_selects_distinct_topics_and_fails_closed(tmp_path):
    """Test A records candidate 1; Test B excludes candidate 1 and picks candidate 2 with full audit metadata."""
    ledger, vh = _init_temp_ledger(tmp_path)
    engine = CuriosityTopicEngine(ledger=ledger, cooldown_days=30)

    mock_cands = [
        {
            "id": "live_cand_1",
            "title": "Land mines in the Falkland Islands",
            "category": "Strange but True History",
            "category_code": "A",
            "summary": "After the 1982 conflict, minefields remained fenced off across the archipelago for 38 years.",
            "extended_extract": "Full historical factual extract...",
            "factual_bullets": ["In 1982 minefields were laid.", "Clearance completed in 2020."],
            "curiosity": 9.4,
            "surprise": 9.2,
            "storytelling": 9.3,
            "visual_potential": 9.4,
            "emotional_connection": 8.9,
            "factual_reliability": 9.6,
            "sources": ["https://en.wikipedia.org/wiki/Land_mines_in_the_Falkland_Islands"],
            "key_entities": ["Falkland Islands", "Land mine"],
            "image_urls": ["https://upload.wikimedia.org/a.jpg"],
            "discovery_source": "wikipedia_onthisday",
            "discovery_timestamp": datetime.now(timezone.utc).isoformat(),
            "publication_timestamp": "2026-11-10T01:00:00Z",
        },
        {
            "id": "live_cand_2",
            "title": "Voyager 1 Golden Record Interstellar Message",
            "category": "Science & Technology",
            "category_code": "C",
            "summary": "Launched in 1977, Voyager 1 carries a gold-plated copper phonograph record beyond the heliosphere.",
            "extended_extract": "Full NASA Voyager factual extract...",
            "factual_bullets": ["Launched September 5 1977.", "Designed by Carl Sagan committee."],
            "curiosity": 9.3,
            "surprise": 9.0,
            "storytelling": 9.1,
            "visual_potential": 9.3,
            "emotional_connection": 8.8,
            "factual_reliability": 9.6,
            "sources": ["https://phys.org/news/voyager-golden-record.html"],
            "key_entities": ["Voyager 1", "Golden Record", "Carl Sagan"],
            "image_urls": ["https://upload.wikimedia.org/v.jpg"],
            "discovery_source": "physorg_space_rss",
            "discovery_timestamp": datetime.now(timezone.utc).isoformat(),
            "publication_timestamp": "2026-11-11T04:00:00Z",
        },
    ]

    with patch.object(engine, "discover_live_candidates", return_value=mock_cands):
        # Selection A
        topic_a, hook_a = engine.select_best_live_topic()
        assert topic_a.title == "Land mines in the Falkland Islands"
        assert topic_a.discovery_metadata["final_rank"] == 1
        assert "novelty_score" in topic_a.discovery_metadata
        assert "semantic_similarity" in topic_a.discovery_metadata

        # Record Selection A in isolated validation history
        vh.record_selection(
            run_id="val_test_a",
            topic_id=topic_a.id,
            topic_title=topic_a.title,
            outcome="completed",
            key_entities=topic_a.key_entities,
            research_fingerprint=topic_a.research_fingerprint,
            discovery_source=topic_a.discovery_source,
            metadata={"category": topic_a.category},
        )

        # Selection B must exclude Selection A and select candidate 2
        topic_b, _ = engine.select_best_live_topic()
        assert topic_b.title == "Voyager 1 Golden Record Interstellar Message"
        assert topic_b.title != topic_a.title
        assert topic_b.discovery_source != topic_a.discovery_source

        vh.record_selection(
            run_id="val_test_b",
            topic_id=topic_b.id,
            topic_title=topic_b.title,
            outcome="completed",
            key_entities=topic_b.key_entities,
            research_fingerprint=topic_b.research_fingerprint,
            discovery_source=topic_b.discovery_source,
            metadata={"category": topic_b.category},
        )

        # Selection C: all candidates exhausted -> MUST fail closed with RuntimeError (no curated fallback)
        with pytest.raises(RuntimeError, match="no eligible verified topics|failed"):
            engine.select_best_live_topic()

    assert len(ledger.load_all_records()) == 0


def test_thumbnail_engine_avoids_excluded_headline(tmp_path):
    """Excluded repeated headline receives penalty so an alternate topic headline wins."""
    img_path = tmp_path / "sample.png"
    Image.new("RGB", (600, 800), (30, 80, 140)).save(img_path)

    engine = CreativeThumbnailEngine()
    out_png = str(tmp_path / "thumb.png")
    rep_json = str(tmp_path / "report.json")
    winner, report = engine.select_and_render_best_thumbnail(
        topic_title="Land mines in the Falkland Islands",
        topic_summary="Over 20000 land mines were cleared across the islands by 2020.",
        category="Strange but True History",
        available_assets=[str(img_path)],
        final_output_path=out_png,
        report_output_path=rep_json,
        candidate_dir=str(tmp_path / "cands"),
        excluded_headlines=["LAND MINES FALKLAND"],
    )
    assert os.path.isfile(out_png)
    assert winner.headline_text != "LAND MINES FALKLAND"
    assert report["winner_headline"] != "LAND MINES FALKLAND"


def test_dashboard_four_state_reconciliation_during_validation(tmp_path):
    """During an active VALIDATION run, overall_operational_state is VALIDATION ACTIVE, never IDLE."""
    store_file = tmp_path / "obs_store.json"
    store = ProductionObservabilityStore(store_path=str(store_file))
    store.start_production("val_active_sim_1", topic="Voyager Golden Record", execution_context="VALIDATION")
    store.start_stage("val_active_sim_1", "video_rendering", "Rendering 20 beats...", progress=50.0)

    dash = ProductionHealthDashboard(db_path=str(tmp_path / "missing.db"))
    fake_tasks = {
        "scheduler_task": {"status": "Disabled", "task_state": "Disabled", "is_registered": True},
        "dashboard_task": {"status": "Disabled", "task_state": "Disabled", "is_registered": True},
    }
    fake_services = {
        "scheduler_daemon": {"state": "STOPPED", "is_running": False, "pid": None},
        "dashboard_service": {"state": "RUNNING", "is_running": True, "pid": 1234, "port": 8000},
    }

    with patch("backend.app.services.production_observability.production_observability", store), \
         patch.object(dash, "get_windows_task_status", return_value=fake_tasks), \
         patch.object(dash, "get_services_status", return_value=fake_services), \
         patch.object(dash, "_get_network_status", return_value="CONNECTED"):
        metrics = dash.get_health_metrics()
        states = dash.get_reconciled_operational_states(
            metrics=metrics, services=fake_services, windows_tasks=fake_tasks
        )

    assert metrics.pipeline_stage.startswith("VALIDATION: VIDEO_RENDERING")
    assert states["scheduler_state"] == "DISABLED"
    assert states["production_worker_state"] == "IDLE"
    assert states["validation_worker_state"] == "RUNNING"
    assert states["overall_operational_state"] == "VALIDATION ACTIVE"


def test_manifest_deliverables_and_dashboard_storage_endpoints(tmp_path):
    """ProductionWorkspace manifest includes top-level deliverables & dashboard streams verified assets."""
    ws = ProductionWorkspace(
        production_id="val_unit_manifest",
        topic_id="t1",
        topic_title="Deep Sea Hydrothermal Vents",
        category="Science & Technology",
        base_dir=str(tmp_path / "val_unit_manifest"),
    )
    ws.initialize_workspace()
    v_file = Path(ws.get_canonical_video_path())
    t_file = Path(ws.get_canonical_thumbnail_path())
    v_file.write_bytes(b"ftypmp42" + b"0" * 2048)
    Image.new("RGB", (1080, 1920), (20, 40, 80)).save(t_file)

    ws.register_artifact("STAGE_10_RENDER", "video", str(v_file), copy_into_workspace=False)
    ws.register_artifact("STAGE_09_THUMB", "thumbnail", str(t_file), copy_into_workspace=False)
    manifest = ws.load_manifest()
    assert manifest["schema_revision"] == "3.21K"
    assert manifest["deliverables"]["final_video"]["exists"] is True
    assert manifest["deliverables"]["thumbnail"]["exists"] is True

    # Test real FastAPI routes against persisted val_3_21j workspace & Overview 4-state keys
    client = TestClient(app)
    ov = client.get("/api/dashboard/overview")
    assert ov.status_code == 200
    ov_json = ov.json()
    for key in ("scheduler_state", "production_worker_state", "validation_worker_state", "overall_operational_state"):
        assert key in ov_json

    val_id = "val_3_21j_20261111_120906"
    man_resp = client.get(f"/api/dashboard/productions/{val_id}/manifest")
    assert man_resp.status_code == 200
    man_data = man_resp.json()
    assert man_data["storage_verification"]["all_verified"] is True
    assert man_data["resolved_storage"]["video_exists"] is True
    assert man_data["resolved_storage"]["thumbnail_exists"] is True

    art_resp = client.get(f"/api/dashboard/productions/{val_id}/artifacts")
    assert art_resp.status_code == 200
    assert art_resp.json()["video_url"] == f"/api/dashboard/productions/{val_id}/video"

    # Bad production_id rejected safely
    bad = client.get("/api/dashboard/productions/..%2Fsecret/artifacts")
    assert bad.status_code in (400, 404)


def test_logging_rotation_and_polling_noise_controls(tmp_path):
    """Access filter suppresses routine 200 GET polling noise while preserving errors & mutations."""
    flt = RoutinePollingAccessFilter()
    rec_poll = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d', ("127.0.0.1:5000", "GET", "/api/dashboard/progress", "1.1", 200), None)
    rec_err = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d', ("127.0.0.1:5000", "GET", "/api/dashboard/progress", "1.1", 500), None)
    rec_post = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d', ("127.0.0.1:5000", "POST", "/api/pipeline/run", "1.1", 200), None)

    assert flt.filter(rec_poll) is False
    assert flt.filter(rec_err) is True
    assert flt.filter(rec_post) is True

    cfg = build_uvicorn_log_config(tmp_path)
    assert cfg["handlers"]["access_file"]["class"] == "logging.handlers.RotatingFileHandler"
    assert cfg["handlers"]["error_file"]["level"] == "WARNING"
    assert cfg["handlers"]["app_file"]["maxBytes"] == 5 * 1024 * 1024

    html_src = Path(r"D:\Agents\CrazzyDocs\backend\app\templates\dashboard.html").read_text(encoding="utf-8")
    assert "IDLE_POLL_MS = 20000" in html_src
    assert "HEAVY_POLL_MS = 60000" in html_src
    assert "document.hidden" in html_src
    assert "visibilitychange" in html_src
