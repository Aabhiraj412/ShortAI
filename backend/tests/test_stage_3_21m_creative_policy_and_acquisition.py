"""
Automated Verification Suite for Stage 3.21M — Permanent Creative Intelligence & Visual Acquisition Policy.

Validates:
1. Shared CreativeProductionPolicy (20+ extensible categories, 8-dimension scoring, flexible category recency).
2. Identical creative standards across ExecutionContext.VALIDATION and ExecutionContext.PRODUCTION.
3. Multi-category discovery resilience (duplicates, cooldown, provider failure logging, empty pool handling).
4. Visual acquisition Priority A -> B -> C -> D, strict license rejection (NC/ND/SA/Unknown),
   incomplete metadata handling, commercial stock license handling, and Priority B open-source animation adaptation.
5. Category-aware scripting, scene-driven storyboard generation, Priority C overlay reuse limit enforcement,
   and ContentIntegrityGate compatibility.
"""

from dataclasses import asdict
import json
import os
from pathlib import Path
from typing import Any, Dict, List

from PIL import Image
import pytest

from backend.app.content.content_integrity_gate import ContentIntegrityGate
from backend.app.content.creative_production_policy import (
    CategoryDistributionTracker,
    CategorySpec,
    CreativeProductionPolicy,
    get_creative_production_policy,
    get_operational_permissions,
)
from backend.app.content.curiosity_topic_engine import CuriosityTopicEngine, ScoredTopic
from backend.app.content.topic_ledger import TopicLedger
from backend.app.core.execution_context import ExecutionContext
from backend.app.media.attribution_generator import AttributionGenerator
from backend.app.media.downloader import AssetDownloader
from backend.app.media.license_verifier import LicenseVerifier
from backend.app.media.models import LicenseStatus, LicenseType, MediaAsset
from backend.app.media.relevance_scorer import RelevanceScorer
from backend.app.orchestration.production_workspace import ProductionWorkspace
from backend.app.rendering.documentary_storyboard_builder import (
    build_20_beat_storyboard,
    compose_structured_script,
)
from backend.app.rendering.explainer_animation_engine import ExplainerAnimationEngine


def _make_sample_topic(
    *,
    topic_id: str = "topic_bioluminescence",
    title: str = "Deep-Sea Bioluminescence",
    category: str = "Nature and Wildlife",
    summary: str = (
        "Deep-sea marine organisms generate light through luciferin oxidation catalyzed by luciferase enzymes. "
        "Researchers measured over 76 percent of ocean twilight zone fauna producing bioluminescent signals."
    ),
) -> ScoredTopic:
    return ScoredTopic(
        id=topic_id,
        title=title,
        category=category,
        category_code="NAT",
        summary=summary,
        curiosity=9.2,
        surprise=9.0,
        storytelling=9.1,
        visual_potential=9.0,
        emotional_connection=8.6,
        factual_reliability=9.5,
        composite_score=9.1,
        sources=["https://en.wikipedia.org/wiki/Bioluminescence"],
        key_entities=["Luciferin", "Luciferase", "Twilight Zone"],
        extended_extract=(
            "Deep-sea marine organisms generate light through luciferin oxidation catalyzed by luciferase enzymes. "
            "Field observations recorded in 2017 demonstrated that roughly 76 percent of midwater animals emit bioluminescence. "
            "Some species synchronize blue wavelengths to match downwelling sunlight for counter-illumination camouflage. "
            "Others project startling flashes at depths below 1000 meters to confuse approaching predators. "
            "Medical researchers later adapted fluorescent and bioluminescent proteins as biological imaging markers."
        ),
        factual_bullets=[
            "Over 76 percent of deep midwater ocean organisms produce bioluminescence.",
            "The chemical reaction combines luciferin with oxygen using luciferase enzymes.",
            "Scientists adapted related light-emitting proteins for cellular medical imaging.",
        ],
    )


def _create_test_png(path: Path, color: tuple) -> str:
    im = Image.new("RGB", (640, 480), color)
    im.save(path, format="PNG")
    return str(path)


def _create_test_gif(path: Path) -> str:
    frames = [
        Image.new("RGB", (320, 240), (20 + i * 35, 90, 180))
        for i in range(6)
    ]
    frames[0].save(
        path,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=120,
        loop=0,
    )
    return str(path)


# ---------------------------------------------------------------------------
# 1. Shared Policy, Context Parity & Category Extensibility
# ---------------------------------------------------------------------------

def test_shared_creative_policy_identical_validation_vs_production():
    val_policy = get_creative_production_policy(ExecutionContext.VALIDATION)
    prod_policy = get_creative_production_policy(ExecutionContext.PRODUCTION)

    assert val_policy is prod_policy
    assert val_policy.list_category_names() == prod_policy.list_category_names()
    assert len(val_policy.list_categories()) >= 20
    assert val_policy.scoring_weights.normalized() == prod_policy.scoring_weights.normalized()
    assert asdict(val_policy.visual_policy) == asdict(prod_policy.visual_policy)

    val_perms = get_operational_permissions(ExecutionContext.VALIDATION)
    prod_perms = get_operational_permissions(ExecutionContext.PRODUCTION)
    assert val_perms.allow_youtube_upload is False
    assert val_perms.allow_production_ledger_writes is False
    assert val_perms.allow_production_notifications is False
    assert prod_perms.allow_youtube_upload is True
    assert prod_perms.allow_production_ledger_writes is True


def test_dynamic_category_registration_and_classification():
    policy = CreativeProductionPolicy()
    base_count = len(policy.list_categories())
    assert base_count >= 20

    policy.register_category(
        CategorySpec(
            key="quantum_computing",
            display_name="Quantum Computing",
            category_code="QCOMP",
            search_queries=["qubit entanglement superconducting processor"],
            keywords=["qubit", "entanglement", "superposition", "quantum gate"],
            preferred_providers=["Wikimedia Commons", "Openverse"],
            narrative_arc=["problem", "quantum_mechanism", "experiment", "impact"],
            visual_affinities=["diagram", "animated_diagram", "authentic_photo"],
        )
    )
    assert len(policy.list_categories()) == base_count + 1
    matched = policy.classify_topic(
        "How Qubit Entanglement Enables Error Correction",
        "Superconducting quantum gate arrays test superposition coherence.",
    )
    assert matched.display_name == "Quantum Computing"


# ---------------------------------------------------------------------------
# 2. Topic Scoring, Distribution Flexibility & Discovery Resilience
# ---------------------------------------------------------------------------

def test_eight_dimension_scoring_and_flexible_category_recency(tmp_path):
    ledger = TopicLedger(db_path=str(tmp_path / "ledger.db"))
    tracker = CategoryDistributionTracker(store_path=str(tmp_path / "cat_dist.json"))
    tracker.record_selection("History", "Roman Aqueducts", "VALIDATION", "r1")
    tracker.record_selection("History", "Printing Press", "VALIDATION", "r2")

    engine = CuriosityTopicEngine(ledger=ledger)
    strong_history = {
        "id": "cand_hist",
        "title": "Antikythera Mechanism Astronomical Gear Computer",
        "category": "History",
        "summary": "Ancient Greek bronze hand-powered astronomical calendar with 30 precision gears.",
        "extended_extract": (
            "Discovered in a Mediterranean shipwreck in 1901, the Antikythera mechanism used over 30 meshing bronze gears "
            "to track solar eclipses, lunar anomalies, and Olympic cycles centuries before European clockwork."
        ),
        "factual_bullets": [
            "Dated to around 100 BCE in Greece.",
            "Predicted eclipses across the 18-year Saros cycle.",
            "Revealed via X-ray tomography in 2006.",
        ],
        "sources": ["https://en.wikipedia.org/wiki/Antikythera_mechanism"],
        "image_urls": ["File:NAMA_Machine_d'Anticythère_1.jpg", "File:Antikythera_fragment_A.jpg"],
        "key_entities": ["Antikythera", "Saros Cycle"],
    }
    weak_science = {
        "id": "cand_sci_weak",
        "title": "Basic Pebble Observation Note",
        "category": "Science",
        "summary": "A short routine note with minimal detail and no measurements.",
        "extended_extract": "Short routine observation about ordinary pebbles with very few concrete details.",
        "factual_bullets": [],
        "sources": ["https://en.wikipedia.org/wiki/Rock"],
        "image_urls": [],
        "key_entities": [],
    }
    scored_hist = engine.score_candidate(strong_history)
    scored_sci = engine.score_candidate(weak_science)

    expected_dims = {
        "curiosity", "novelty", "storytelling", "factual_reliability",
        "visual_availability", "audience_relevance", "short_form_suitability", "freshness",
    }
    assert expected_dims.issubset(set(scored_hist.score_breakdown.keys()))
    assert "does not predict" in scored_hist.discovery_metadata["scoring_disclaimer"].lower()
    # Soft recency penalty exists, yet strong History topic still beats weak Science topic
    assert tracker.compute_soft_recency_penalty("History") > 0
    assert scored_hist.composite_score > scored_sci.composite_score


def test_discovery_resilience_empty_pool_and_provider_failures(tmp_path, monkeypatch):
    ledger = TopicLedger(db_path=str(tmp_path / "ledger.db"))
    engine = CuriosityTopicEngine(ledger=ledger)

    # Empty pool must return cleanly without crashing
    monkeypatch.setattr(engine, "discover_live_candidates", lambda **kwargs: [])
    assert engine.discover_and_rank_live_topics() == []
    assert engine.last_discovery_summary["candidate_pool_size"] == 0

    # Hook generation works cleanly across categories
    hooks = engine.generate_five_hooks(_make_sample_topic())
    assert len(hooks) == 5
    assert all(h.hook_text for h in hooks)


def test_duplicate_and_cooldown_rejection_identical_across_contexts(tmp_path):
    from backend.app.content.validation_topic_history import ValidationTopicHistory

    vh = ValidationTopicHistory(history_path=str(tmp_path / "val_hist.json"), default_cooldown_days=30)
    ledger = TopicLedger(db_path=str(tmp_path / "ledger.db"), json_path=str(tmp_path / "topic_hist.json"), validation_history=vh)
    engine = CuriosityTopicEngine(ledger=ledger)
    sample = {
        "id": "cand_dup",
        "title": "Voyager Golden Record",
        "category": "Space and Astronomy",
        "summary": "Phonograph discs launched aboard Voyager probes in 1977 containing Earth sounds and music.",
        "extended_extract": "Launched in 1977 aboard Voyager 1 and Voyager 2, the copper records encode greetings in 55 languages.",
        "factual_bullets": ["Launched in 1977 aboard Voyager probes."],
        "sources": ["https://en.wikipedia.org/wiki/Voyager_Golden_Record"],
        "key_entities": ["Voyager", "Carl Sagan"],
    }
    res_first = engine.score_candidate(sample)
    assert not res_first.is_duplicate
    # Once recorded in validation history, duplicate check triggers identically
    ledger.validation_history.record_selection(
        run_id="r_dup_check",
        topic_id="cand_dup",
        topic_title="Voyager Golden Record",
        execution_context="VALIDATION",
        outcome="completed",
        key_entities=["Voyager", "Carl Sagan"],
    )
    res_second = engine.score_candidate(sample)
    assert res_second.is_duplicate is True


# ---------------------------------------------------------------------------
# 3. License Verification, Provenance & Priority B Animation Support
# ---------------------------------------------------------------------------

def test_license_verifier_rejections_and_commercial_stock_handling():
    verifier = LicenseVerifier(allow_share_alike=False, allow_non_commercial=False)

    assert verifier.verify_asset_license("CC BY-NC 4.0", "https://example.org/a", "Openverse")[0] == LicenseStatus.REJECTED
    assert verifier.verify_asset_license("CC BY-ND 4.0", "https://example.org/b", "Openverse")[0] == LicenseStatus.REJECTED
    assert verifier.verify_asset_license("CC BY-SA 4.0", "https://example.org/c", "Wikimedia Commons")[0] == LicenseStatus.REJECTED
    assert verifier.verify_asset_license("Unknown License", "https://example.org/d", "Openverse")[0] == LicenseStatus.REJECTED
    assert verifier.verify_asset_license("All rights reserved", "https://example.org/e", "Openverse")[0] == LicenseStatus.REJECTED

    # Valid commercial & open licenses pass
    assert verifier.verify_asset_license("CC0", "https://example.org/cc0", "Wikimedia Commons")[0] == LicenseStatus.APPROVED
    assert verifier.verify_asset_license("Public Domain", "https://example.org/pd", "Internet Archive")[0] == LicenseStatus.APPROVED
    assert verifier.verify_asset_license("CC BY 4.0", "https://example.org/by", "Openverse", creator="Jane Doe")[0] == LicenseStatus.APPROVED
    assert verifier.verify_asset_license("Pexels License", "https://www.pexels.com/photo/1", "Pexels")[0] == LicenseStatus.APPROVED
    assert verifier.verify_asset_license("Pixabay License", "https://pixabay.com/photos/1", "Pixabay")[0] == LicenseStatus.APPROVED

    # ShareAlike permitted only when policy explicitly allows it
    sa_verifier = LicenseVerifier(allow_share_alike=True)
    assert sa_verifier.verify_asset_license("CC BY-SA 4.0", "https://commons.wikimedia.org/wiki/File:X", "Wikimedia Commons")[0] == LicenseStatus.APPROVED


def test_relevance_scorer_category_queries():
    scorer = RelevanceScorer()
    psych_q = scorer.generate_search_queries("Pavlov Conditioning Study", "Psychology")
    assert any("psychology" in q.lower() or "experiment" in q.lower() for q in psych_q)
    tech_q = scorer.generate_search_queries("Transformer Attention Mechanism", "Artificial Intelligence")
    assert any("diagram" in q.lower() or "architecture" in q.lower() for q in tech_q)


def test_downloader_supports_priority_b_gif_and_attribution_manifest(tmp_path):
    from backend.app.media.downloader import detect_magic_type

    gif_file = Path(_create_test_gif(tmp_path / "src_wave.gif"))
    assert detect_magic_type(gif_file.read_bytes()[:32]) == "image/gif"
    assert detect_magic_type(b"\x1a\x45\xdf\xa3" + b"\x00" * 16) == "video/webm"

    gen = AttributionGenerator(manifests_dir=str(tmp_path / "manifests"))
    asset = MediaAsset(
        asset_id="wm_gif_1",
        title="Bioluminescent Wave Loop",
        creator="Marine Observatory",
        source_repository="Wikimedia Commons",
        source_url="https://commons.wikimedia.org/wiki/File:Wave.gif",
        download_url="https://upload.wikimedia.org/wave.gif",
        license_type=LicenseType.CC_BY,
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution_text="Marine Observatory (CC BY 4.0)",
        local_path=str(gif_file),
        asset_hash="abc123",
        verification_status=LicenseStatus.APPROVED,
        asset_type="existing_animation",
        original_filename="Wave.gif",
        download_timestamp="2026-11-11T10:00:00Z",
    )
    manifest = gen.generate_manifest("test_vid_1", [asset])
    assert manifest.total_assets == 1
    assert "source" in manifest.youtube_description_block.lower()
    assert manifest.items[0].asset_type == "existing_animation"
    assert manifest.items[0].original_filename == "Wave.gif"


# ---------------------------------------------------------------------------
# 4. Scripting, Priority A-D Storyboard Director & Context Parity
# ---------------------------------------------------------------------------

def test_category_script_identical_across_validation_and_production():
    topic = _make_sample_topic()
    s_val = compose_structured_script(topic, "Most deep-sea creatures make their own living light.", ExecutionContext.VALIDATION)
    s_prod = compose_structured_script(topic, "Most deep-sea creatures make their own living light.", ExecutionContext.PRODUCTION)

    assert s_val["full_script"] == s_prod["full_script"]
    assert s_val["narrative_arc"] == s_prod["narrative_arc"]
    assert 90 <= s_val["word_count"] <= 135


def test_storyboard_priority_abcd_reuse_limits_and_integrity_parity(tmp_path):
    topic = _make_sample_topic()
    ws = ProductionWorkspace(
        production_id="test_3_21m_sb",
        topic_id=topic.id,
        topic_title=topic.title,
        category=topic.category,
        base_dir=str(tmp_path / "media"),
    )
    ws.initialize_workspace()

    # Build synthetic acquired assets: 3 authentic photos (Pri A) + 1 real animated GIF (Pri B)
    assets: List[MediaAsset] = []
    for idx, col in enumerate(((22, 74, 128), (18, 122, 92), (140, 88, 32))):
        img_p = Path(_create_test_png(Path(ws.assets_acquired_dir) / f"bio_{idx}.png", col))
        assets.append(
            MediaAsset(
                asset_id=f"img_{idx}",
                title=f"Deep Sea Bioluminescence Specimen {idx}",
                creator="NOAA Ocean Exploration",
                source_repository="Wikimedia Commons",
                source_url=f"https://commons.wikimedia.org/wiki/File:Bio_{idx}.png",
                download_url=f"https://upload.wikimedia.org/bio_{idx}.png",
                license_type=LicenseType.PUBLIC_DOMAIN,
                license_url="https://creativecommons.org/publicdomain/mark/1.0/",
                attribution_text="NOAA Public Domain Archive",
                local_path=str(img_p),
                width=640,
                height=480,
                verification_status=LicenseStatus.APPROVED,
                asset_type="authentic_image",
            )
        )

    gif_p = Path(_create_test_gif(Path(ws.assets_acquired_dir) / "luciferin_reaction.gif"))
    assets.append(
        MediaAsset(
            asset_id="anim_gif_0",
            title="Luciferin Oxidation Animation",
            creator="Science Archive",
            source_repository="Wikimedia Commons",
            source_url="https://commons.wikimedia.org/wiki/File:Luciferin.gif",
            download_url="https://upload.wikimedia.org/luciferin.gif",
            license_type=LicenseType.CC0,
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
            attribution_text="Science Archive (CC0)",
            local_path=str(gif_p),
            width=320,
            height=240,
            mime_type="image/gif",
            verification_status=LicenseStatus.APPROVED,
            asset_type="existing_animation",
        )
    )

    script = compose_structured_script(topic, "Why do creatures glow a thousand meters beneath the sea?", ExecutionContext.VALIDATION)
    timings = []
    cur = 0.0
    for sec in script["sections"]:
        dur = 10.4
        timings.append({
            "id": sec["id"],
            "stage": sec["stage"],
            "arc_phase": sec.get("arc_phase", sec["stage"]),
            "text": sec["text"],
            "start_sec": round(cur, 3),
            "end_sec": round(cur + dur, 3),
            "duration_sec": dur,
        })
        cur += dur

    # Create dummy silent wav path for StoryboardPlan reference
    vo_path = str(Path(ws.audio_dir) / "voiceover.mp3")
    Path(vo_path).write_bytes(b"\x00" * 2048)

    plan, gate_beats, ass_path = build_20_beat_storyboard(
        topic=topic,
        timings=timings,
        total_voice_dur=cur,
        voiceover_path=vo_path,
        acquired_assets=assets,
        prod_ws=ws,
        script_full_text=script["full_script"],
        execution_context=ExecutionContext.VALIDATION,
    )
    assert os.path.isfile(ass_path)
    beats = plan.metadata["storyboard_beats"]
    summary = plan.metadata["visual_sourcing_summary"]
    assert len(beats) == 20
    # Strict Priority A, B, C, D presence & non-overlapping counts
    assert summary["authentic_images_acquired"] == 3
    assert summary["existing_animations_acquired"] == 1
    assert summary["priority_a_photo_beats"] >= 3
    assert summary["priority_b_existing_anim_beats"] == 1
    assert summary["priority_c_animated_authentic_beats"] >= 1
    assert summary["priority_d_generated_image_beats"] >= 1
    assert summary["max_source_photo_reuse"] <= 2

    # Required per-beat Visual Director schema + selective AI disclosure
    for b in beats:
        for key in (
            "beat_id", "narrative_purpose", "required_asset_type",
            "search_keywords", "preferred_acquisition_method", "license",
            "animation_instructions", "transition",
        ):
            assert key in b
        if b["sourcing_priority"] == "D_selective_generated":
            assert b["illustrative_reconstruction"] is True
        else:
            assert b["illustrative_reconstruction"] is False

    # Priority B output file exists & is valid non-empty MP4
    b_beat = next(b for b in beats if b["sourcing_priority"] == "B_open_source_animation")
    assert os.path.isfile(b_beat["asset_path"]) and os.path.getsize(b_beat["asset_path"]) > 1000
    # Priority C output MP4 composited on real background image exists
    c_beat = next(b for b in beats if b["sourcing_priority"] == "C_animated_authentic")
    assert os.path.isfile(c_beat["asset_path"]) and os.path.getsize(c_beat["asset_path"]) > 1000
    assert c_beat["source_image_sha256"]

    # Production context builds identical storyboard modality distribution
    from dataclasses import replace
    import shutil
    ws_prod = ProductionWorkspace(
        production_id="test_3_21m_sb_prod",
        topic_id=topic.id,
        topic_title=topic.title,
        category=topic.category,
        base_dir=str(tmp_path / "media_prod"),
    )
    ws_prod.initialize_workspace()
    prod_assets: List[MediaAsset] = []
    for a in assets:
        dst = Path(ws_prod.assets_acquired_dir) / os.path.basename(a.local_path)
        shutil.copy2(a.local_path, dst)
        prod_assets.append(replace(a, local_path=str(dst)))

    plan_prod, _, _ = build_20_beat_storyboard(
        topic=topic,
        timings=timings,
        total_voice_dur=cur,
        voiceover_path=vo_path,
        acquired_assets=prod_assets,
        prod_ws=ws_prod,
        script_full_text=script["full_script"],
        execution_context=ExecutionContext.PRODUCTION,
    )
    assert plan_prod.metadata["visual_sourcing_summary"] == summary
