"""
Stage 3.27 creative identity: near-duplicate scripts, hooks, endings and visual compositions,
on top of the existing registry (exact topic/script gates). Temporary databases only.
"""
import sqlite3

import pytest

from backend.app.content.creative_identity_registry import CreativeIdentityRegistry, jaccard, _shingles

SCRIPT = ("One physics prize pays three times more than a Nobel. Yuri Milner founded it in 2012, and each award "
          "is worth 3 million US dollars. The money goes to theorists working on strings and black holes.")


@pytest.fixture
def reg(tmp_path):
    r = CreativeIdentityRegistry(str(tmp_path / "studio.db"))
    r.reserve("old", topic_title="Breakthrough Prize")
    r.update("old", script_text=SCRIPT, hook_text="One physics prize pays three times more than a Nobel.",
             ending_text="The biggest reward in physics is for questions, not answers.",
             visual_concepts_json=["title:trophy", "timeline:clock", "stat:coin", "process:atom", "reveal:atom"])
    return r


def test_exact_script_is_blocking(reg):
    d = reg.near_duplicates(job_id="new", script_text=SCRIPT)
    assert any(x["kind"] == "script" and x["similarity"] == 1.0 for x in d["blocking"])


def test_lightly_edited_script_is_still_a_near_duplicate(reg):
    edited = SCRIPT.replace("pays three times more than", "is worth triple").replace("black holes", "dark matter")
    assert jaccard(_shingles(edited), _shingles(SCRIPT)) >= 0.5
    assert any(x["kind"] == "script" for x in reg.near_duplicates(job_id="new", script_text=edited)["blocking"])


def test_different_script_on_a_related_subject_passes(reg):
    other = ("Euclid has mapped a sliver of the sky in three dimensions. Its first data release shows galaxies "
             "stretched by dark matter, and astronomers expect a far larger map by the end of the decade.")
    assert reg.near_duplicates(job_id="new", script_text=other)["blocking"] == []


def test_reused_hook_is_blocking(reg):
    d = reg.near_duplicates(job_id="new", hook_text="One physics prize pays three times more than the Nobel!")
    assert [x["kind"] for x in d["blocking"]] == ["hook"]


def test_same_scene_composition_is_blocking_but_shared_branding_is_not(reg):
    same = ["title:trophy", "timeline:clock", "stat:coin", "process:atom", "reveal:atom"]
    assert any(x["kind"] == "visual_sequence" for x in reg.near_duplicates(job_id="new", visual_concepts=same)["blocking"])
    different = ["title:rhino", "spotlight:leaf", "stat:chart", "callout:globe", "reveal:rhino"]
    assert reg.near_duplicates(job_id="new", visual_concepts=different)["blocking"] == []


def test_templated_fallback_ending_is_advisory_not_blocking(reg):
    reg.update("old", ending_text="And that is how Breakthrough Prize reshaped our understanding. Subscribe to CrazzyDocs for more.")
    end = "And that is how Black rhinos reshaped our understanding. Subscribe to CrazzyDocs for more."
    fb = reg.near_duplicates(job_id="new", topic_title="Black rhinos", ending_text=end, script_source="extractive_fallback")
    assert fb["blocking"] == [] and [x["kind"] for x in fb["advisory"]] == ["ending"]
    llm = reg.near_duplicates(job_id="new", topic_title="Black rhinos", ending_text=end, script_source="llm:gemini")
    assert [x["kind"] for x in llm["blocking"]] == ["ending"]


def test_own_job_is_never_its_own_duplicate(reg):
    assert reg.near_duplicates(job_id="old", script_text=SCRIPT)["blocking"] == []


def test_migration_adds_columns_to_an_existing_registry(tmp_path):
    db = tmp_path / "s.db"
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE creative_identity_registry (job_id TEXT PRIMARY KEY, normalized_topic TEXT NOT NULL,
        topic_title TEXT NOT NULL, category TEXT NOT NULL DEFAULT '', topic_angle TEXT NOT NULL DEFAULT '',
        hook_fingerprint TEXT NOT NULL DEFAULT '', script_fingerprint TEXT NOT NULL DEFAULT '',
        storyboard_fingerprint TEXT NOT NULL DEFAULT '', asset_fingerprints_json TEXT NOT NULL DEFAULT '[]',
        visual_sequence_fingerprint TEXT NOT NULL DEFAULT '', renderer_config_json TEXT NOT NULL DEFAULT '{}',
        video_sha256 TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'SELECTED',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    conn.execute("INSERT INTO creative_identity_registry (job_id, normalized_topic, topic_title, created_at, updated_at) "
                 "VALUES ('cycle_1', 'science fiction', 'Science fiction', '2026-11-13T00:00:00+00:00', '2026-11-13T00:00:00+00:00')")
    conn.commit(); conn.close()
    reg = CreativeIdentityRegistry(str(db))
    row = reg.get("cycle_1")
    assert row["topic_title"] == "Science fiction" and row["visual_concepts_json"] == "[]" and row["script_text"] == ""
    reg.update("cycle_1", scene_sequence_json=["title", "reveal"], visual_engine="pop_info_v1")
    assert reg.get("cycle_1")["visual_engine"] == "pop_info_v1"
