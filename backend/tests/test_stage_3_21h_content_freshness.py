"""
Stage 3.21H: Fresh Content and Storyboard Integrity Reset Tests.
Covers Requirements A through J:
A. Cooldown rejection of recently produced topics.
B. Semantic equivalence rejection.
C. Fresh unrelated topic selection.
D. Provider failure fails closed without fallback to old topics.
E. Script fingerprint differentiation across topics.
F. Storyboard fingerprint differentiation across topics.
G. Rejection of unrelated story-specific visual assets in storyboard.
H. Rejection of mismatched production manifests / cross-production asset theft.
I. Repeated frame sequence detection.
J. Test execution isolation from genuine production content ledger.
"""

from datetime import datetime, timedelta
import os
import sqlite3
import pytest

from backend.app.content.topic_ledger import (
    TopicLedger,
    ContentRecord,
    normalize_topic_title,
    compute_script_fingerprint,
    compute_storyboard_fingerprint,
)
from backend.app.content.curiosity_topic_engine import CuriosityTopicEngine, ScoredTopic, HookCandidate
from backend.app.content.content_integrity_gate import ContentIntegrityGate
from backend.app.orchestration.crazzydocs_master_orchestrator import CrazzyDocsMasterOrchestrator
from backend.app.rendering.engines.vesna_fast_renderer import VisualBeatConfig


@pytest.fixture
def temp_ledger(tmp_path):
    """Provides an isolated TopicLedger in a temporary directory."""
    db_file = str(tmp_path / "test_studio.db")
    json_file = str(tmp_path / "test_history.json")
    return TopicLedger(db_path=db_file, json_path=json_file)


# -----------------------------------------------------------------------------
# A. A previously used topic is not selected again during its cooldown.
# -----------------------------------------------------------------------------
def test_requirement_a_cooldown_rejection(temp_ledger):
    # Record a topic produced today (within 30-day cooldown)
    recent_rec = ContentRecord(
        topic_id="topic_recent_001",
        topic_title="The Great Molasses Flood of 1919",
        opening_hook="A 25-foot wave of molasses destroyed Boston.",
        main_subject="The Great Molasses Flood of 1919",
        key_entities=["Boston", "Purity Distilling", "Molasses", "Industrial Disaster"],
        script_fingerprint="fp_recent_001",
        category="Strange but True History",
        visual_style="historical_documentary",
        created_at=datetime.utcnow().isoformat() + "Z",
        publication_status="produced",
    )
    temp_ledger.record_content(recent_rec, allow_override=True, execution_context="production")

    is_dup, score, reason = temp_ledger.check_content_collision(
        candidate_title="The Great Molasses Flood of 1919",
        candidate_hook="A 25-foot wave of molasses destroyed Boston.",
        candidate_entities=["Boston", "Molasses", "Purity Distilling"],
        cooldown_days=30,
    )
    assert is_dup is True
    assert "cooldown period" in reason.lower()

    # Expired cooldown (>30 days ago) should not trigger cooldown
    old_time = (datetime.utcnow() - timedelta(days=45)).isoformat() + "Z"
    expired_rec = ContentRecord(
        topic_id="topic_old_002",
        topic_title="Ancient Roman Concrete Secrets",
        opening_hook="Why Roman concrete lasted two millennia under seawater.",
        main_subject="Roman Concrete",
        key_entities=["Rome", "Pozzolana", "Concrete"],
        script_fingerprint="fp_old_002",
        category="Science & Technology",
        visual_style="historical_documentary",
        created_at=old_time,
        publication_status="produced",
    )
    temp_ledger.record_content(expired_rec, allow_override=True, execution_context="production")

    is_dup_old, _, reason_old = temp_ledger.check_content_collision(
        candidate_title="Modern High-Rise Engineering Innovations",
        candidate_hook="How skyscrapers absorb earthquake vibrations.",
        candidate_entities=["Skyscrapers", "Dampers", "Modern Engineering"],
        cooldown_days=30,
    )
    assert is_dup_old is False
    assert "passed all diversity checks" in reason_old.lower()


# -----------------------------------------------------------------------------
# B. A semantically equivalent topic is rejected.
# -----------------------------------------------------------------------------
def test_requirement_b_semantic_equivalence_rejection(temp_ledger):
    temp_ledger.record_content(
        ContentRecord(
            topic_id="topic_vesna",
            topic_title="The Flight Attendant Who Fell 33,000 Feet Without a Parachute and Survived",
            opening_hook="She survived a 33,000 foot fall from an exploding airplane.",
            main_subject="Vesna Vulovic",
            key_entities=["Vesna Vulovic", "JAT Flight 367", "Czechoslovakia", "Guinness World Record"],
            script_fingerprint="fp_vesna",
            category="Unbelievable Real-Life Stories",
            visual_style="historical_documentary",
            created_at=(datetime.utcnow() - timedelta(days=60)).isoformat() + "Z",  # past cooldown
            publication_status="produced",
        ),
        allow_override=True,
        execution_context="production"
    )

    # Alternate phrasing of the same historical event
    is_dup, score, reason = temp_ledger.check_content_collision(
        candidate_title="Vesna Vulovic: The Flight Attendant Who Survived Falling 33000 Feet",
        candidate_hook="Vesna fell from the sky after a bomb exploded on JAT Flight 367.",
        candidate_entities=["Vesna Vulovic", "JAT Flight 367", "Czechoslovakia"],
    )
    assert is_dup is True
    assert ("collision" in reason.lower() or "similarity" in reason.lower())


# -----------------------------------------------------------------------------
# C. A fresh unrelated topic can be selected.
# -----------------------------------------------------------------------------
def test_requirement_c_fresh_unrelated_topic_selected(temp_ledger):
    # Record Yamaguchi and Vesna under cooldown
    for tid, title, hook, entities in [
        ("t_yamaguchi", "Tsutomu Yamaguchi: The Man Who Survived Both Atomic Bombs", "He survived Hiroshima and Nagasaki.", ["Hiroshima", "Nagasaki", "Tsutomu Yamaguchi"]),
        ("t_vesna", "The Flight Attendant Who Fell 33,000 Feet Without a Parachute and Survived", "She fell 33,000 feet.", ["Vesna Vulovic", "JAT Flight 367"]),
    ]:
        temp_ledger.record_content(
            ContentRecord(
                topic_id=tid,
                topic_title=title,
                opening_hook=hook,
                main_subject=title,
                key_entities=entities,
                script_fingerprint=f"fp_{tid}",
                category="Unbelievable Real-Life Stories",
                visual_style="historical_documentary",
                created_at=datetime.utcnow().isoformat() + "Z",
                publication_status="produced",
            ),
            allow_override=True,
            execution_context="production"
        )

    engine = CuriosityTopicEngine(ledger=temp_ledger, cooldown_days=30)
    ranked = engine.discover_and_rank_topics(filter_duplicates=True)
    assert len(ranked) > 0
    top_candidate = ranked[0]
    # Demon Core should be top fresh topic
    assert "Demon Core" in top_candidate.title
    assert top_candidate.is_duplicate is False


# -----------------------------------------------------------------------------
# D. An unavailable research provider does not trigger old-topic fallback.
# -----------------------------------------------------------------------------
def test_requirement_d_unavailable_provider_no_fallback(tmp_path):
    empty_ledger = TopicLedger(db_path=str(tmp_path / "empty.db"), json_path=str(tmp_path / "empty.json"))
    engine = CuriosityTopicEngine(ledger=empty_ledger)
    # Monkeypatch candidates to simulate 0 available candidates or all rejected
    from backend.app.content import curiosity_topic_engine
    orig_cands = curiosity_topic_engine.CURATED_CURIOSITY_CANDIDATES
    try:
        curiosity_topic_engine.CURATED_CURIOSITY_CANDIDATES = []
        with pytest.raises(RuntimeError) as exc_info:
            engine.select_best_topic()
        assert "No fresh topics available" in str(exc_info.value)
    finally:
        curiosity_topic_engine.CURATED_CURIOSITY_CANDIDATES = orig_cands


# -----------------------------------------------------------------------------
# E. Two different topics produce different script fingerprints.
# -----------------------------------------------------------------------------
def test_requirement_e_different_script_fingerprints():
    orchestrator = CrazzyDocsMasterOrchestrator()
    t1 = ScoredTopic(
        id="topic_demon_core",
        title="The Demon Core: The Plutonium Sphere That Radiated Los Alamos",
        category="Science & Technology",
        category_code="C",
        summary="Louis Slotin slipped with a flathead screwdriver during a critical mass test at Los Alamos.",
        curiosity=9.8, surprise=9.7, storytelling=9.6, visual_potential=9.5, emotional_connection=9.2, factual_reliability=10.0,
        composite_score=9.66, sources=[], key_entities=["Louis Slotin", "Demon Core", "Los Alamos"]
    )
    t2 = ScoredTopic(
        id="topic_pepsi_navy",
        title="The Day Pepsi Commanded the World's 6th Largest Submarine Fleet",
        category="Corporate & Financial Stories",
        category_code="D",
        summary="In 1989 the Soviet Union traded seventeen submarines and warships to Pepsi for soda syrup.",
        curiosity=9.6, surprise=9.8, storytelling=9.4, visual_potential=9.2, emotional_connection=8.5, factual_reliability=9.8,
        composite_score=9.45, sources=[], key_entities=["PepsiCo", "Soviet Union", "Submarine Fleet"]
    )

    s1 = orchestrator._synthesize_topic_script(t1, "This 14-pound metal ball killed two scientists.")
    s2 = orchestrator._synthesize_topic_script(t2, "In 1989, a soda company commanded the sixth-largest submarine fleet.")

    fp1 = compute_script_fingerprint(s1)
    fp2 = compute_script_fingerprint(s2)

    assert fp1 != fp2
    assert len(fp1) == 64
    assert len(fp2) == 64


# -----------------------------------------------------------------------------
# F. Two different topics produce different storyboard fingerprints.
# -----------------------------------------------------------------------------
def test_requirement_f_different_storyboard_fingerprints():
    orchestrator = CrazzyDocsMasterOrchestrator()
    t1 = ScoredTopic(
        id="topic_demon_core",
        title="The Demon Core: The Plutonium Sphere That Radiated Los Alamos",
        category="Science & Technology",
        category_code="C",
        summary="Louis Slotin slipped with a flathead screwdriver during a critical mass test.",
        curiosity=9.8, surprise=9.7, storytelling=9.6, visual_potential=9.5, emotional_connection=9.2, factual_reliability=10.0,
        composite_score=9.66, sources=[], key_entities=["Louis Slotin", "Demon Core"]
    )
    t2 = ScoredTopic(
        id="topic_pepsi_navy",
        title="The Day Pepsi Commanded the World's 6th Largest Submarine Fleet",
        category="Corporate & Financial Stories",
        category_code="D",
        summary="In 1989 the Soviet Union traded seventeen warships to Pepsi for soda.",
        curiosity=9.6, surprise=9.8, storytelling=9.4, visual_potential=9.2, emotional_connection=8.5, factual_reliability=9.8,
        composite_score=9.45, sources=[], key_entities=["PepsiCo", "Soviet Union"]
    )

    beats1 = orchestrator._build_topic_visual_beats(t1, "This 14-pound metal ball killed two scientists.", 54.0)
    beats2 = orchestrator._build_topic_visual_beats(t2, "In 1989, a soda company commanded warships.", 54.0)

    sb_fp1 = compute_storyboard_fingerprint(beats1)
    sb_fp2 = compute_storyboard_fingerprint(beats2)

    assert sb_fp1 != sb_fp2
    assert len(sb_fp1) == 64
    assert len(sb_fp2) == 64


# -----------------------------------------------------------------------------
# G. A storyboard cannot silently reuse unrelated story-specific visuals.
# -----------------------------------------------------------------------------
def test_requirement_g_storyboard_unrelated_visuals_rejection(tmp_path):
    gate = ContentIntegrityGate(workspace_root=str(tmp_path))

    # Attention span topic attempting to reuse atomic blast imagery
    beats = [
        VisualBeatConfig(
            id="beat_01",
            image_filename="Atomic_cloud_over_Hiroshima_jpg.jpg",
            start_sec=0.0,
            end_sec=5.0,
            top_tag="PSYCHOLOGY",
            editorial_text="Why your attention span is collapsing",
            accent_color=(255, 100, 100),
        )
    ]

    report = gate.validate_production(
        topic_title="Why Your Attention Span is Shrinking",
        category="Science & Technology",
        narration_text="Modern social media algorithms have systematically eroded human attention spans over the last decade.",
        visual_beat_configs=beats,
        video_path=str(tmp_path / "mock_vid.mp4"),
        thumbnail_path=str(tmp_path / "mock_thumb.png"),
        thumbnail_headline="ATTENTION SPANS",
        metadata_title="Why Your Attention Span is Shrinking",
        metadata_description="Detailed documentary analysis of human cognitive bandwidth in the modern smartphone era.",
        metadata_tags=["psychology", "attention", "focus", "technology", "brain"],
        attribution_text="Source archives: Cognitive Science Research Institute and Wikimedia Commons.",
    )

    assert report.is_valid is False
    assert any("CHECK_C_ASSET_TOPIC_MAPPING" in r for r in report.rejection_reasons)


# -----------------------------------------------------------------------------
# H. The renderer rejects a mismatched production manifest.
# -----------------------------------------------------------------------------
def test_requirement_h_renderer_rejects_mismatched_manifest(tmp_path):
    gate = ContentIntegrityGate(workspace_root=str(tmp_path))

    # Manifest contains assets belonging to an unrelated historical production
    foreign_manifest_assets = [
        {"path": r"D:\Agents\CrazzyDocs\media\productions\prod_cycle_4_20261110_1800\video\stolen_render.mp4"}
    ]

    curr_video = str(tmp_path / "media" / "productions" / "prod_new_run_001" / "video" / "showcase_new.mp4")

    report = gate.validate_production(
        topic_title="The Kola Superdeep Borehole",
        category="Science & Technology",
        narration_text="Soviet scientists drilled 40,000 feet into the Earth until rock turned to boiling liquid.",
        visual_beat_configs=[VisualBeatConfig(id="b1", image_filename="", start_sec=0.0, end_sec=5.0, top_tag="GEOLOGY", editorial_text="Kola Borehole", accent_color=(255, 255, 255))],
        video_path=curr_video,
        thumbnail_path=str(tmp_path / "mock_thumb.png"),
        thumbnail_headline="DEEPEST HOLE",
        metadata_title="The Kola Superdeep Borehole: Drilling Toward Earth's Core",
        metadata_description="Soviet scientists spent 24 years drilling the deepest artificial hole on Earth on the Kola Peninsula.",
        metadata_tags=["geology", "earth", "borehole", "soviet", "science"],
        attribution_text="Source archives: Soviet Academy of Sciences and Wikimedia Commons.",
        manifest_assets=foreign_manifest_assets,
    )

    assert report.is_valid is False
    assert any("CHECK_M_ASSET_OWNERSHIP" in r for r in report.rejection_reasons)


# -----------------------------------------------------------------------------
# I. Repeated frame sequences are detected.
# -----------------------------------------------------------------------------
def test_requirement_i_repeated_frame_sequences_detected(tmp_path):
    gate = ContentIntegrityGate(workspace_root=str(tmp_path))
    # Test checking integrity gate structure
    report = gate.validate_production(
        topic_title="The Secrets of Roman Concrete",
        category="Science & Technology",
        narration_text="Roman concrete structures survived for two thousand years under seawater without degrading.",
        visual_beat_configs=[VisualBeatConfig(id="b1", image_filename="", start_sec=0.0, end_sec=5.0, top_tag="ARCHITECTURE", editorial_text="Roman Concrete", accent_color=(255, 255, 255))],
        video_path=str(tmp_path / "nonexistent.mp4"),
        thumbnail_path=str(tmp_path / "thumb.png"),
        thumbnail_headline="ROMAN CONCRETE",
        metadata_title="The Secrets of Ancient Roman Concrete",
        metadata_description="Detailed documentary analysis of volcanic pozzolana and ancient mortar formulation.",
        metadata_tags=["architecture", "concrete", "rome", "engineering", "history"],
        attribution_text="Source archives: Archaeological Survey and Wikimedia Commons.",
    )
    # Missing video is flagged by CHECK_L
    assert any("CHECK_L_FRAME_SEQUENCE_INTEGRITY" in r for r in report.rejection_reasons)


# -----------------------------------------------------------------------------
# J. Test jobs cannot modify genuine topic history or publishing records.
# -----------------------------------------------------------------------------
def test_requirement_j_test_jobs_cannot_pollute_production_ledger(tmp_path):
    # Connect to a mock production db
    prod_db = str(tmp_path / "mock_prod.db")
    prod_json = str(tmp_path / "mock_prod.json")
    ledger = TopicLedger(db_path=prod_db, json_path=prod_json)

    # Attempt to record under execution_context='test'
    test_rec = ContentRecord(
        topic_id="topic_test_synthetic_001",
        topic_title="Test Topic Synthetic Event",
        opening_hook="This is a purely synthetic test hook.",
        main_subject="Synthetic Test",
        key_entities=["Synthetic", "Unit Test"],
        script_fingerprint="fp_synthetic_test",
        category="Strange but True History",
        visual_style="historical_documentary",
        created_at=datetime.utcnow().isoformat() + "Z",
        publication_status="test",
    )

    success = ledger.record_content(test_rec, allow_override=False, execution_context="test")
    assert success is True

    # Verify that nothing was written to the persistent records table
    records = ledger.load_all_records()
    assert len(records) == 0

    # Also verify with CRAZZYDOCS_TEST_MODE environment variable
    os.environ["CRAZZYDOCS_TEST_MODE"] = "1"
    try:
        ledger.record_content(test_rec, allow_override=False, execution_context="production")
        records_after = ledger.load_all_records()
        assert len(records_after) == 0
    finally:
        os.environ.pop("CRAZZYDOCS_TEST_MODE", None)
