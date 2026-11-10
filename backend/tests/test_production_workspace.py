"""
Unit Tests for Stage 3.13B: Production Asset Isolation Workspace.
Verifies directory creation, artifact registration, manifest tracking,
and isolation between distinct production runs.
"""

import json
import os
import shutil
import tempfile
import pytest

from backend.app.orchestration.production_workspace import ProductionWorkspace


@pytest.fixture
def isolated_workspace():
    tmpdir = tempfile.mkdtemp(prefix="test_prod_workspace_")
    ws = ProductionWorkspace(
        production_id="PROD_TEST_001",
        topic_id="cand_03_vesna_fall",
        topic_title="The Flight Attendant Who Fell 33,000 Feet Without a Parachute and Survived",
        category="Unbelievable Real-Life Stories",
        base_dir=os.path.join(tmpdir, "media", "productions", "PROD_TEST_001"),
    )
    ws.workspace_root = tmpdir
    ws.initialize_workspace()

    yield {"tmpdir": tmpdir, "workspace": ws}

    shutil.rmtree(tmpdir, ignore_errors=True)


def test_production_workspace_initialization(isolated_workspace):
    """Verifies that all 9 isolated production subdirectories are created cleanly."""
    ws: ProductionWorkspace = isolated_workspace["workspace"]
    assert os.path.isdir(ws.research_dir)
    assert os.path.isdir(ws.scripts_dir)
    assert os.path.isdir(ws.narration_dir)
    assert os.path.isdir(ws.visual_timeline_dir)
    assert os.path.isdir(ws.assets_dir)
    assert os.path.isdir(ws.thumbnails_dir)
    assert os.path.isdir(ws.video_dir)
    assert os.path.isdir(ws.metadata_dir)
    assert os.path.isdir(ws.reports_dir)
    assert os.path.isfile(ws.manifest_path)


def test_artifact_registration_and_isolation(isolated_workspace):
    """Verifies that registered artifacts are copied into isolated subdirectories and logged to manifest."""
    ws: ProductionWorkspace = isolated_workspace["workspace"]
    tmpdir = isolated_workspace["tmpdir"]

    # Create dummy artifact outside production folder
    src_video = os.path.join(tmpdir, "rendered_output.mp4")
    with open(src_video, "wb") as f:
        f.write(b"SAMPLE_VIDEO_STREAM_DATA")

    art = ws.register_artifact(
        stage="STAGE_09_RENDERING",
        artifact_type="video",
        source_path=src_video,
        target_filename="final_render.mp4",
        copy_into_workspace=True,
    )

    assert art.artifact_type == "video"
    assert os.path.isfile(art.file_path)
    assert art.file_path == os.path.join(ws.video_dir, "final_render.mp4")
    assert art.file_size_bytes == len(b"SAMPLE_VIDEO_STREAM_DATA")
    assert len(art.sha256) == 64

    # Verify manifest on disk
    manifest = ws.load_manifest()
    assert manifest["production_id"] == "PROD_TEST_001"
    assert manifest["topic_id"] == "cand_03_vesna_fall"
    assert manifest["artifacts_count"] == 1
    assert manifest["artifacts"][0]["file_path"] == art.file_path
    assert manifest["artifacts"][0]["sha256"] == art.sha256


def test_isolation_between_distinct_productions(isolated_workspace):
    """Verifies that two separate productions maintain totally isolated manifests and folders."""
    ws1: ProductionWorkspace = isolated_workspace["workspace"]
    tmpdir = isolated_workspace["tmpdir"]

    ws2 = ProductionWorkspace(
        production_id="PROD_TEST_002",
        topic_id="cand_01_demon_core",
        topic_title="The Demon Core",
        category="Science & Technology",
        base_dir=os.path.join(tmpdir, "media", "productions", "PROD_TEST_002"),
    )
    ws2.workspace_root = tmpdir
    ws2.initialize_workspace()

    assert ws1.prod_dir != ws2.prod_dir
    assert os.path.exists(ws1.manifest_path)
    assert os.path.exists(ws2.manifest_path)

    m1 = ws1.load_manifest()
    m2 = ws2.load_manifest()
    assert m1["production_id"] != m2["production_id"]
    assert m1["topic_id"] != m2["topic_id"]
