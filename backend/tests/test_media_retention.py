import json
import os
import sqlite3
from argparse import Namespace
from pathlib import Path

import pytest

from backend.scripts import media_retention


def _setup_workspace(tmp_path: Path, monkeypatch, *, age_days: int = 45):
    root = tmp_path
    productions = root / "media" / "productions"
    work = productions / "validation_run" / "render_work"
    run = work.parent
    for path in (
        run / "assets",
        run / "narration",
        run / "video",
        work,
        root / "backend" / "data",
    ):
        path.mkdir(parents=True, exist_ok=True)
    (run / "production_manifest.json").write_text("{}", encoding="utf-8")
    (run / "assets" / "source.png").write_bytes(b"source")
    (run / "narration" / "master.wav").write_bytes(b"audio")
    (run / "video" / "final.mp4").write_bytes(b"final")
    (work / "beat.mp4").write_bytes(b"intermediate")
    (work / "concat_list.txt").write_text("file beat.mp4\n", encoding="utf-8")
    cutoff = media_retention.datetime.now().timestamp() - age_days * 86400
    for file in media_retention.walk_files(work):
        os.utime(file, (cutoff, cutoff))

    database = root / "backend" / "studio.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE persistent_jobs (id TEXT, status TEXT)")
    progress = root / "backend" / "data" / "production_progress_store.json"
    progress.write_text(json.dumps({"productions": {}}), encoding="utf-8")

    monkeypatch.setattr(media_retention, "ROOT", root)
    monkeypatch.setattr(media_retention, "PRODUCTIONS_ROOT", productions)
    monkeypatch.setattr(media_retention, "MEDIA_ROOT", root / "media")
    monkeypatch.setattr(media_retention, "PROGRESS_PATH", progress)
    monkeypatch.setattr(media_retention, "DATABASE_PATH", database)
    policy = {
        "policy_version": 1,
        "retention_days": {
            "regeneratable_intermediates": 30,
        },
        "allowed_quarantine_subdirectory": "render_work",
        "protected_run_patterns": ["val_3_21r_*", "val_3_21q_black_hole_*"],
        "quarantine_root": "media/quarantine/stage_3_21s",
    }
    return work, policy


def test_quarantine_dry_run_does_not_move_files(tmp_path, monkeypatch):
    work, policy = _setup_workspace(tmp_path, monkeypatch)

    plan = media_retention.candidate_report([work], policy)

    assert plan["file_count"] == 2
    assert plan["reclaimable_disk_bytes"] == 0
    assert (work / "beat.mp4").is_file()


def test_quarantine_and_recovery_preserve_hashes_without_overwrite(tmp_path, monkeypatch, capsys):
    work, policy = _setup_workspace(tmp_path, monkeypatch)
    original_hash = media_retention.file_hash(work / "beat.mp4")
    args = Namespace(
        paths=["media/productions/validation_run/render_work"],
        confirm=True,
        worker_idle_confirmed=True,
        override_retention=False,
    )

    media_retention.command_quarantine(args, policy)

    output = capsys.readouterr().out
    manifest_path = next((tmp_path / "media" / "quarantine").rglob("quarantine_manifest.json"))
    assert "Quarantine manifest:" in output
    assert not work.exists()
    quarantine_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    quarantined_beat = tmp_path / quarantine_data["files"][0]["quarantine_path"]
    media_retention.command_restore(
        Namespace(manifest=manifest_path.relative_to(tmp_path).as_posix(), confirm=False),
        policy,
    )
    assert quarantined_beat.is_file()
    assert not work.exists()

    media_retention.command_restore(
        Namespace(manifest=manifest_path.relative_to(tmp_path).as_posix(), confirm=True),
        policy,
    )
    restored = tmp_path / "media" / "productions" / "validation_run" / "render_work" / "beat.mp4"
    assert restored.is_file()
    assert media_retention.file_hash(restored) == original_hash


def test_quarantine_refuses_recent_or_active_run(tmp_path, monkeypatch):
    work, policy = _setup_workspace(tmp_path, monkeypatch, age_days=0)

    with pytest.raises(ValueError, match="Retention period"):
        media_retention.candidate_report([work], policy)

    media_retention.PROGRESS_PATH.write_text(
        json.dumps({
            "productions": {
                "validation_run": {"overall_status": "running"},
            },
        }),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Active production"):
        media_retention.candidate_report([work], policy, override_retention=True)


def test_quarantine_refuses_application_path_references(tmp_path, monkeypatch):
    work, policy = _setup_workspace(tmp_path, monkeypatch)
    reference = tmp_path / "backend" / "app" / "retention_reference.py"
    reference.parent.mkdir(parents=True)
    reference.write_text(
        "RUN_PATH = 'media/productions/validation_run/render_work/beat.mp4'\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Repository references"):
        media_retention.candidate_report([work], policy)


def test_duplicate_report_requires_matching_hashes(tmp_path, monkeypatch):
    work, _ = _setup_workspace(tmp_path, monkeypatch)
    same = work / "duplicate.mp4"
    different = work / "different.mp4"
    same.write_bytes((work / "beat.mp4").read_bytes())
    different.write_bytes(b"different content")

    duplicates = media_retention.duplicate_groups(
        [work / "beat.mp4", same, different]
    )

    assert len(duplicates) == 1
    assert set(duplicates[0]["paths"]) == {
        "media/productions/validation_run/render_work/beat.mp4",
        "media/productions/validation_run/render_work/duplicate.mp4",
    }
