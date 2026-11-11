"""
Automated Validation Test Suite for Stage 3.21I — Controlled Pipeline Readiness & Isolation.

Verifies:
1. ExecutionContext.VALIDATION blocks publishing queue insertion, live notifications, and content_ledger pollution.
2. Live topic discovery fails closed when upstream live sources return empty (no old-topic fallback).
3. force_topic_id cannot bypass duplicate/cooldown checks in PRODUCTION or VALIDATION contexts.
4. Structured 5-stage documentary script composition stays grounded and free of cross-contamination.
5. 20-beat documentary storyboard builder generates 20 valid 1080x1920 plates and non-leaking ASS subtitles.
"""

from datetime import datetime
import os
from unittest.mock import patch

import pytest

from backend.app.content.curiosity_topic_engine import CuriosityTopicEngine, ScoredTopic
from backend.app.content.topic_ledger import ContentRecord, TopicLedger
from backend.app.core.execution_context import ExecutionContext, require_production_context
from backend.app.orchestration.crazzydocs_master_orchestrator import CrazzyDocsMasterOrchestrator
from backend.app.orchestration.production_workspace import ProductionWorkspace
from backend.app.rendering.documentary_storyboard_builder import (
    build_20_beat_storyboard,
    compose_structured_script,
)
from backend.app.services.notification_service import NotificationManager
from backend.app.services.youtube_publishing_engine import PublicationRecord, PublishingDatabase


def test_validation_context_isolation_blocks_all_side_effects(tmp_path):
    assert ExecutionContext.from_string("validation") == ExecutionContext.VALIDATION

    with pytest.raises(PermissionError):
        require_production_context(ExecutionContext.VALIDATION, "Live publishing upload")

    # Publishing DB must reject VALIDATION context
    pdb = PublishingDatabase(db_path=str(tmp_path / "pub.db"))
    pub_rec = PublicationRecord(
        id=None,
        topic_title="Validation Topic",
        video_path="v.mp4",
        thumbnail_path="t.png",
        metadata_json="{}",
        youtube_video_id=None,
        upload_status="pending_queue",
        visibility="private",
        video_hash="abc123",
        upload_timestamp=None,
    )
    with pytest.raises(PermissionError):
        pdb.record_publication(pub_rec, execution_context=ExecutionContext.VALIDATION)

    # Notification manager must suppress emails/desktop popups in VALIDATION context
    notifier = NotificationManager()
    res = notifier.send_success(
        video_title="Val Title",
        topic="Val Topic",
        storytelling_style="documentary",
        duration_sec=54.0,
        video_path="v.mp4",
        thumbnail_path="t.png",
        job_id="val_3_21i_test",
        execution_context=ExecutionContext.VALIDATION,
    )
    assert res["email"] is False
    assert res["desktop"] is False
    assert res.get("suppressed_test_mode") is True

    # TopicLedger must not write VALIDATION runs to persistent content_ledger / JSON
    ledger = TopicLedger(db_path=str(tmp_path / "ledger.db"), json_path=str(tmp_path / "ledger.json"))
    rec = ContentRecord(
        topic_id="val_topic_1",
        topic_title="Val Topic",
        opening_hook="Val Hook",
        main_subject="Val Topic",
        key_entities=["Val"],
        script_fingerprint="fp_val",
        category="Science & Technology",
        visual_style="documentary",
        created_at=datetime.utcnow().isoformat() + "Z",
    )
    assert ledger.record_content(rec, allow_override=False, execution_context=ExecutionContext.VALIDATION.value) is True
    assert ledger.load_all_records() == []


def test_live_discovery_fails_closed_without_curated_fallback(tmp_path):
    ledger = TopicLedger(db_path=str(tmp_path / "l.db"), json_path=str(tmp_path / "l.json"))
    engine = CuriosityTopicEngine(ledger=ledger)
    with patch.object(engine, "discover_live_candidates", return_value=[]):
        with pytest.raises(RuntimeError) as exc:
            engine.select_best_live_topic()
        assert "without old-topic fallback" in str(exc.value)


def test_force_topic_id_cannot_bypass_duplicate_check_in_production_or_validation(tmp_path):
    ledger = TopicLedger(db_path=str(tmp_path / "dup.db"), json_path=str(tmp_path / "dup.json"))
    ledger.record_content(
        ContentRecord(
            topic_id="cand_02_pepsi_navy",
            topic_title="The Day Pepsi Commanded the World's 6th Largest Submarine Fleet",
            opening_hook="Soviet Union traded warships to Pepsi.",
            main_subject="PepsiCo Submarine Fleet",
            key_entities=["PepsiCo", "Soviet Union", "Submarine Fleet"],
            script_fingerprint="fp_pepsi",
            category="Corporate & Financial Stories",
            visual_style="historical_documentary",
            created_at=datetime.utcnow().isoformat() + "Z",
        ),
        allow_override=True,
        execution_context="production",
    )
    engine = CuriosityTopicEngine(ledger=ledger, cooldown_days=30)
    orch = CrazzyDocsMasterOrchestrator(topic_engine=engine, ledger=ledger)

    # Attempting to force the duplicate topic under VALIDATION or PRODUCTION must fail closed
    # without calling external live network discovery if we mock live topics as only unrelated fresh items
    mock_fresh = [
        ScoredTopic(
            id="live_fresh_topic",
            title="Voyager Golden Record: The Cosmic Message Sent Beyond the Solar System",
            category="Science & Technology",
            category_code="C",
            summary="Launched in 1977, Voyager 1 carries a gold-plated copper phonograph record.",
            curiosity=9.5,
            surprise=9.3,
            storytelling=9.4,
            visual_potential=9.5,
            emotional_connection=9.0,
            factual_reliability=9.8,
            composite_score=9.45,
            sources=["Wikipedia Encyclopedia Archive: https://en.wikipedia.org/wiki/Voyager_Golden_Record"],
            key_entities=["Voyager 1", "NASA", "Carl Sagan"],
        )
    ]
    with patch.object(engine, "discover_and_rank_live_topics", return_value=mock_fresh):
        with pytest.raises(RuntimeError) as exc:
            orch.execute_production_pipeline(
                job_id="val_dup_bypass_attempt",
                force_topic_id="cand_02_pepsi_navy",
                execution_context=ExecutionContext.VALIDATION,
            )
        assert "duplicate/cooldown" in str(exc.value)


def test_structured_script_and_20_beat_storyboard_builder(tmp_path):
    sample_topic = ScoredTopic(
        id="live_antikythera",
        title="Antikythera Mechanism",
        category="Strange but True History",
        category_code="A",
        summary="Discovered in 1901 off the Greek island of Antikythera, this bronze gearwork device dates back over two millennia.",
        extended_extract=(
            "The Antikythera mechanism is an ancient Greek hand-powered orrery built around the second century BC. "
            "Sponge divers recovered forty-two corroded fragments from a sunken Roman-era cargo ship at a depth of 45 meters. "
            "Modern X-ray tomography revealed at least thirty interlocking bronze gears predicting eclipses and planetary orbits. "
            "Nothing of comparable mechanical complexity reappeared in Europe for more than fourteen hundred years."
        ),
        factual_bullets=[
            "The Antikythera mechanism is an ancient Greek hand-powered orrery built around the second century BC.",
            "Sponge divers recovered corroded fragments from a sunken Roman-era cargo ship at a depth of 45 meters.",
            "Modern X-ray tomography revealed thirty interlocking bronze gears predicting lunar phases and eclipses.",
            "Nothing of comparable mechanical complexity reappeared in Europe for fourteen hundred years.",
        ],
        curiosity=9.5,
        surprise=9.4,
        storytelling=9.5,
        visual_potential=9.2,
        emotional_connection=8.8,
        factual_reliability=9.7,
        composite_score=9.4,
        sources=["Wikipedia Encyclopedia Archive: https://en.wikipedia.org/wiki/Antikythera_mechanism"],
        key_entities=["Antikythera", "Greek", "Bronze Gears", "Astronomical Calculator"],
    )

    script_info = compose_structured_script(
        sample_topic,
        "In 1901, divers pulled an impossible bronze computer from an ancient Mediterranean shipwreck.",
    )
    assert len(script_info["sections"]) == 5
    assert 95 <= script_info["word_count"] <= 160
    assert "Antikythera" in script_info["full_script"]

    ws = ProductionWorkspace(
        production_id="val_unit_test",
        topic_id=sample_topic.id,
        topic_title=sample_topic.title,
        category=sample_topic.category,
        base_dir=str(tmp_path / "val_unit_test"),
    )
    ws.initialize_workspace()

    timings = [
        {"id": "seg_01", "stage": "HOOK", "text": script_info["sections"][0]["text"], "start_sec": 0.0, "end_sec": 6.0},
        {"id": "seg_02", "stage": "CONTEXT", "text": script_info["sections"][1]["text"], "start_sec": 6.0, "end_sec": 18.0},
        {"id": "seg_03", "stage": "ESCALATION", "text": script_info["sections"][2]["text"], "start_sec": 18.0, "end_sec": 31.0},
        {"id": "seg_04", "stage": "REVELATION", "text": script_info["sections"][3]["text"], "start_sec": 31.0, "end_sec": 43.0},
        {"id": "seg_05", "stage": "PAYOFF", "text": script_info["sections"][4]["text"], "start_sec": 43.0, "end_sec": 52.0},
    ]

    plan, gate_beats, ass_path = build_20_beat_storyboard(
        topic=sample_topic,
        timings=timings,
        total_voice_dur=52.0,
        voiceover_path=str(tmp_path / "dummy.mp3"),
        acquired_assets=[],
        prod_ws=ws,
    )

    assert 18 <= len(plan.scenes) <= 25
    assert len(plan.scenes) == 20
    assert len(gate_beats) == 20
    assert os.path.isfile(ass_path)
    assert plan.include_crazzydocs_outro is True
    for sb in plan.scenes:
        assert os.path.isfile(sb.params["image_path"])


def test_dynamic_thumbnail_headline_and_no_repeated_script_sentences(tmp_path):
    from backend.app.rendering.engines.creative_thumbnail_engine import CreativeThumbnailEngine

    thumb_engine = CreativeThumbnailEngine()
    cands = thumb_engine.create_candidate_proposals(
        topic_title="Voyager 1 Interstellar Magnetic Anomaly",
        topic_summary="Launched in 1977, Voyager 1 crossed the heliopause 18 billion kilometers from Earth.",
        category="Science & Technology",
        available_assets=[],
    )
    headlines = [c.headline_text for c in cands]
    assert all("DEMON CORE" not in h for h in headlines)
    assert all("HIROSHIMA" not in h for h in headlines)
    assert any("VOYAGER" in h for h in headlines)

    short_topic = ScoredTopic(
        id="live_short_facts",
        title="Voyager 1 Heliopause Crossing",
        category="Science & Technology",
        category_code="C",
        summary="Voyager 1 crossed the heliopause in 2012. Plasma density jumped forty-fold overnight.",
        factual_bullets=[
            "Voyager 1 crossed the heliopause in August 2012.",
            "Plasma density jumped forty-fold overnight.",
        ],
        curiosity=9.0,
        surprise=9.0,
        storytelling=9.0,
        visual_potential=9.0,
        emotional_connection=8.5,
        factual_reliability=9.5,
        composite_score=9.0,
        sources=["NASA JPL Archive"],
        key_entities=["Voyager 1", "Heliopause", "NASA"],
    )
    script = compose_structured_script(short_topic, "Voyager 1 crossed into interstellar space in 2012.")
    sec_texts = [s["text"].strip() for s in script["sections"]]
    assert len(set(sec_texts)) == 5
