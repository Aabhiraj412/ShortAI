"""
Repeated-video root cause (2026-11-13): failed productions never reached the TopicLedger,
the live ranker is deterministic, so "Science fiction" was selected by six productions in a
row and every one composed the same script byte for byte. These tests pin the registry that
records identity at selection time and the gates built on it. All databases are temporary.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.app.content import creative_identity_policy as cip
from backend.app.content.creative_identity_registry import (
    CreativeIdentityRegistry,
    DuplicateCreativeIdentity,
    asset_fingerprints,
)
from backend.app.content.topic_ledger import TopicLedger, compute_script_fingerprint
from backend.app.core.execution_context import ExecutionContext
from backend.app.orchestration import crazzydocs_master_orchestrator as om
from backend.app.scheduler.retry_policy import PERMANENT, classify_error


@pytest.fixture
def reg(tmp_path):
    return CreativeIdentityRegistry(str(tmp_path / "studio.db"))


def _cand(title):
    return SimpleNamespace(title=title)


def test_a_failed_attempt_still_blocks_the_subject(reg):
    reg.reserve("cycle_2_20261113_0600", topic_title="Science fiction", category="Science")
    reg.update("cycle_2_20261113_0600", status="FAILED")
    fresh, rejected = reg.filter_fresh(
        [_cand("Science Fiction"), _cand("Black rhinos are mostly loners")], job_id="cycle_3_20261113_1200")
    assert [c.title for c in fresh] == ["Black rhinos are mostly loners"]
    assert rejected[0]["conflicts_with"] == "cycle_2_20261113_0600"


def test_the_same_job_can_resume_its_own_topic(reg):
    reg.reserve("cycle_4_20261113_1800", topic_title="Science fiction")
    fresh, _ = reg.filter_fresh([_cand("Science fiction")], job_id="cycle_4_20261113_1800")
    assert len(fresh) == 1


def test_topic_cooldown_expires(reg):
    reg.reserve("old_job", topic_title="Science fiction")
    later = datetime.now(timezone.utc) + timedelta(days=31)
    assert reg.topic_conflict("Science fiction", exclude_job="new_job", now=later) is None
    assert reg.topic_conflict("Science fiction", exclude_job="new_job") is not None


def test_identical_script_is_detected_across_different_titles(reg):
    fp = compute_script_fingerprint("Science fiction (often shortened to sci-fi) is the genre ...")
    reg.reserve("a", topic_title="Science fiction")
    reg.update("a", script_fingerprint=fp)
    reg.reserve("b", topic_title="Sci-fi explained")
    assert reg.script_conflict(fp, exclude_job="b")["job_id"] == "a"
    assert reg.script_conflict(fp, exclude_job="a") is None


def test_identity_survives_deleted_files_and_a_new_process(tmp_path):
    db = str(tmp_path / "studio.db")
    first = CreativeIdentityRegistry(db)
    first.reserve("j1", topic_title="Grab Attention")
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 10)
    first.update("j1", video_sha256="abc", asset_fingerprints_json=["h1", "h2"], status="PRODUCED")
    video.unlink()                                          # the MP4 is gone
    again = CreativeIdentityRegistry(db)                    # fresh process / restart
    row = again.get("j1")
    assert row["video_sha256"] == "abc" and json.loads(row["asset_fingerprints_json"]) == ["h1", "h2"]
    assert again.topic_conflict("grab attention!", exclude_job="j2") is not None


def test_unknown_fields_are_refused(reg):
    reg.reserve("j", topic_title="t")
    with pytest.raises(ValueError):
        reg.update("j", created_at="2020-01-01")


def test_reused_stock_asset_is_not_a_duplicate(reg):
    reg.reserve("a", topic_title="Bridges")
    reg.update("a", asset_fingerprints_json=["shared_stock_sha"], script_fingerprint="s1")
    reg.reserve("b", topic_title="Drones")
    assert reg.script_conflict("s2", exclude_job="b") is None
    fresh, _ = reg.filter_fresh([_cand("Drones inspect concrete")], job_id="c")
    assert fresh


def test_asset_fingerprints_skip_empty_and_non_dict():
    beats = [{"asset_hash": "h1"}, {"asset_hash": None, "source_image_sha256": "None",
                                    "source_image_identity": "url:x"}, "not-a-beat", {}]
    assert asset_fingerprints(beats) == ["h1", "url:x"]


def test_backfill_imports_failed_and_completed_history(tmp_path):
    db = tmp_path / "studio.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE persistent_jobs (id TEXT, topic_title TEXT, status TEXT, created_at TEXT, actual_start_at TEXT)")
    conn.executemany("INSERT INTO persistent_jobs VALUES (?,?,?,?,?)", [
        ("cycle_2_20261113_0600", "Science fiction", "FAILED", "2026-11-13 00:30:00", None),
        ("test_job", "Ignored", "COMPLETED", "2026-11-13 00:30:00", None),
        ("cycle_9", None, "FAILED", "2026-11-13 00:30:00", None),
    ])
    conn.commit()
    conn.close()
    TopicLedger(db_path=str(db), json_path=str(tmp_path / "h.json"), include_validation_history=False)
    prod = tmp_path / "productions" / "cycle_2_20261113_0600" / "scripts"
    prod.mkdir(parents=True)
    (prod / "script_cycle_2_20261113_0600.json").write_text(json.dumps({"full_script": "Same words."}), encoding="utf-8")

    reg = CreativeIdentityRegistry(str(db))
    assert reg.backfill_from_history(str(tmp_path / "productions")) == 1
    assert reg.backfill_from_history(str(tmp_path / "productions")) == 0          # idempotent
    row = reg.get("cycle_2_20261113_0600")
    assert row["status"] == "HISTORICAL_FAILED"
    assert row["script_fingerprint"] == compute_script_fingerprint("Same words.")
    assert reg.get("test_job") is None


def test_duplicate_identity_is_classified_permanent():
    exc = DuplicateCreativeIdentity("integrity gate: script is identical to production x")
    assert classify_error(exc)[0] == PERMANENT


# --------------------------------------------------------------- orchestrator wiring
def test_registry_is_off_outside_real_production(tmp_path, monkeypatch):
    monkeypatch.delenv("CRAZZYDOCS_TEST_MODE", raising=False)
    ledger = TopicLedger(db_path=str(tmp_path / "s.db"), json_path=str(tmp_path / "h.json"),
                         include_validation_history=False)
    orch = om.CrazzyDocsMasterOrchestrator.__new__(om.CrazzyDocsMasterOrchestrator)
    orch.ledger = ledger
    assert orch._identity_registry(ExecutionContext.VALIDATION, True, "val_1") is None
    assert orch._identity_registry(ExecutionContext.PRODUCTION, False, "job_1") is None
    assert orch._identity_registry(ExecutionContext.PRODUCTION, True, "test_job") is None
    assert isinstance(orch._identity_registry(ExecutionContext.PRODUCTION, True, "cycle_1"), CreativeIdentityRegistry)
    monkeypatch.setenv("CRAZZYDOCS_TEST_MODE", "1")
    assert orch._identity_registry(ExecutionContext.PRODUCTION, True, "cycle_1") is None


def test_registry_never_touches_the_live_db_under_pytest(monkeypatch):
    monkeypatch.delenv("CRAZZYDOCS_TEST_MODE", raising=False)
    orch = om.CrazzyDocsMasterOrchestrator.__new__(om.CrazzyDocsMasterOrchestrator)
    from backend.app.content.topic_ledger import DB_PATH
    orch.ledger = SimpleNamespace(db_path=DB_PATH)
    assert orch._identity_registry(ExecutionContext.PRODUCTION, True, "cycle_1") is None


def test_orchestrator_filters_and_reserves_before_any_render():
    src = om.ORCHESTRATOR_SRC if hasattr(om, "ORCHESTRATOR_SRC") else open(om.__file__, encoding="utf-8").read()
    filt = src.index("identity_registry.filter_fresh(ranked")
    reserve = src.index("identity_registry.reserve(\n")
    script_gate = src.index("identity_registry.script_conflict(script_fp")
    render = src.index("Stage 9: Video Rendering")
    assert filt < reserve < script_gate < render


# ------------------------------------------------------------------ style catalog
@pytest.mark.parametrize("asset_type", ["authentic_photo", "archival document", "footage", "bespoke_diagram"])
@pytest.mark.parametrize("category", ["History", "Archaeology", "Space", "Nature and Wildlife", "Science"])
def test_history_archive_style_is_never_selected(category, asset_type):
    sel = cip.AdaptiveVisualStyleSelector.select(
        category=category, stage="HOOK", narrative_purpose="QUESTION",
        segment_text="In 1919 a wave of molasses crossed the city", asset_type=asset_type,
        previous_style="documentary")
    assert sel.visual_style not in cip.DEACTIVATED_STYLES
    assert "documentary" not in sel.scores


def test_pop_info_wins_a_neutral_beat():
    sel = cip.AdaptiveVisualStyleSelector.select(
        category="Psychology", stage="CONTEXT", narrative_purpose="", segment_text="people notice faces")
    assert sel.visual_style == cip.PRIMARY_STYLE


def test_storyboard_builder_refuses_retired_styles_and_directions():
    from backend.app.rendering import documentary_storyboard_builder as dsb
    src = open(dsb.__file__, encoding="utf-8").read()
    assert "if visual_style in DEACTIVATED_STYLE_VALUES" in src
    assert "if direction.value in DEACTIVATED_STYLE_VALUES" in src
    assert "archive_editorial_montage" in cip.DEACTIVATED_STYLE_VALUES
    assert "historical_documentary" in cip.DEACTIVATED_STYLE_VALUES
